"""Backlot server — FastAPI app: board state API, SSE change feed, media.

The watcher observes ``projects/`` with watchfiles; on any change it bumps a
per-project version and wakes SSE subscribers, who tell the browser to
refetch state. The server never writes to project directories.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backlot.lesson_studio import (
    LessonStudioProviderError,
    LessonStudioValidationError,
    advance_lesson_stage,
    create_lesson_project,
    generate_lesson_scene_image,
    generate_lesson_scene_video,
    plan_lesson_storyboard,
    read_studio_state,
)
from backlot.state import PROJECTS_DIR, REPO_ROOT, list_projects, load_board_state, summarize_project

UI_DIR = Path(__file__).resolve().parent / "ui"
THUMB_CACHE_DIR = REPO_ROOT / ".backlot" / "thumbs"
THUMB_WIDTHS = (320, 640, 960)

# Paths inside a project whose changes are pure noise for the board.
_IGNORE_PARTS = {"node_modules", ".git", "__pycache__", ".cache"}

SSE_HEARTBEAT_SECONDS = 15


def _ui_html(name: str, assets: tuple[str, ...]) -> HTMLResponse:
    html = (UI_DIR / name).read_text(encoding="utf-8")
    for asset in assets:
        path = UI_DIR / asset
        if path.is_file():
            version = str(int(path.stat().st_mtime))
            html = html.replace(f"/ui/{asset}", f"/ui/{asset}?v={version}")
    return HTMLResponse(html)


class ChangeHub:
    """Fan-out of project-change notifications to SSE subscribers.

    Subscriptions are filtered: a board subscribed to one project only ever
    receives that project's ids, so unrelated-project bursts can't flood its
    queue and starve out the one notification it actually needs.
    """

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue, Optional[str]] = {}

    def subscribe(self, project_id: Optional[str] = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers[q] = project_id
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.pop(q, None)

    def publish(self, project_id: str) -> None:
        for q, only in list(self._subscribers.items()):
            if only is not None and only != project_id:
                continue
            try:
                q.put_nowait(project_id)
            except asyncio.QueueFull:
                # Queue holds only THIS subscriber's relevant ids, so a full
                # queue already guarantees a pending wake-up → safe to drop.
                pass


hub = ChangeHub()

# Library summaries are expensive to derive (full state parse per project);
# cache per project and invalidate from the watcher.
_summary_cache: dict[str, dict] = {}


def _invalidate_summary(project_id: str) -> None:
    _summary_cache.pop(project_id, None)


def _cached_summaries() -> list[dict]:
    if not PROJECTS_DIR.is_dir():
        return []
    summaries = []
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        cached = _summary_cache.get(entry.name)
        if cached is None:
            try:
                cached = summarize_project(entry)
            except Exception:
                cached = {
                    "project_id": entry.name, "title": entry.name,
                    "pipeline_type": "unknown", "has_pipeline_state": False,
                    "poster": None, "live": False, "last_activity": 0,
                    "active_stage": None, "awaiting_human": False,
                    "stage_states": [], "completed_count": 0,
                    "render_count": 0, "scene_count": 0, "error": "unreadable",
                }
            _summary_cache[entry.name] = cached
        summaries.append(cached)
    summaries.sort(key=lambda s: (not s["live"], -(s["last_activity"] or 0)))
    return summaries


# Watch-loop hot path: pure string comparison, no per-path filesystem calls
# (change batches can be thousands of paths during a render).
import os as _os

_PROJECTS_ROOT_STR = _os.path.normcase(str(PROJECTS_DIR.resolve()))


def _project_of_change(path_str: str) -> Optional[str]:
    """Map a changed filesystem path to a project id (None = irrelevant)."""
    norm = _os.path.normcase(_os.path.normpath(path_str))
    if not norm.startswith(_PROJECTS_ROOT_STR):
        return None
    rel = norm[len(_PROJECTS_ROOT_STR):].lstrip("\\/")
    if not rel:
        return None
    parts = rel.replace("\\", "/").split("/")
    if _IGNORE_PARTS.intersection(parts):
        return None
    return parts[0]


async def _watch_projects() -> None:
    """Background task: watch projects/ and publish debounced changes."""
    try:
        from watchfiles import awatch
    except ImportError:
        return  # watcher unavailable → board still works via manual refresh
    if not PROJECTS_DIR.is_dir():
        return
    async for changes in awatch(PROJECTS_DIR, recursive=True, step=400):
        touched: set[str] = set()
        for _change, path_str in changes:
            pid = _project_of_change(path_str)
            if pid:
                touched.add(pid)
        for pid in touched:
            _invalidate_summary(pid)
            hub.publish(pid)


def create_app() -> FastAPI:
    app = FastAPI(title="Backlot", docs_url=None, redoc_url=None)
    app.state.studio_locks = {}

    @app.on_event("startup")
    async def _startup() -> None:
        app.state.watch_task = asyncio.create_task(_watch_projects())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "watch_task", None)
        if task:
            task.cancel()

    # ---- API ----------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "app": "backlot"}

    @app.get("/api/projects")
    async def projects() -> list:
        return await asyncio.to_thread(_cached_summaries)

    # ---- Lesson Studio ------------------------------------------------

    def require_local_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if not origin:
            return
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(status_code=403, detail="Lesson Studio is local-only")

    async def run_studio_action(key: str, func, *args):
        lock = app.state.studio_locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            raise HTTPException(status_code=409, detail="This action is already running")
        try:
            async with lock:
                return await asyncio.to_thread(func, *args)
        except LessonStudioValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LessonStudioProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Lesson Studio action failed")
            raise HTTPException(status_code=500, detail="Lesson Studio action failed") from exc

    @app.get("/api/lesson-studio/config")
    async def lesson_studio_config() -> dict:
        return {
            "provider_ready": bool(_os.environ.get("DASHSCOPE_API_KEY")),
            "models": {
                "text": "qwen3.7-plus",
                "image": "qwen-image-2.0-pro",
                "video": "wan2.6-i2v-flash",
            },
            "video_output": {
                "duration_min_seconds": 2,
                "duration_max_seconds": 15,
                "duration_default_seconds": 5,
                "planned_scene_seconds": 14,
                "duration_step_seconds": 1,
                "resolutions": ["720P", "1080P"],
                "fps": 30,
            },
            "free_tier_only": True,
            "paid_spend_cap_usd": 0,
        }

    @app.post("/api/lesson-studio/projects")
    async def create_lesson_studio_project(request: Request) -> JSONResponse:
        require_local_origin(request)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Expected a JSON request body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Expected a JSON object")
        try:
            created = await asyncio.to_thread(
                create_lesson_project,
                title=payload.get("title", "English Lesson"),
                source_text=payload.get("source_text", ""),
                projects_dir=PROJECTS_DIR,
            )
        except LessonStudioValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project_id = created["project_id"]
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return JSONResponse(
            status_code=201,
            content={"project_id": project_id, "studio_url": created["studio_url"]},
        )

    @app.get("/api/lesson-studio/projects/{project_id}")
    async def lesson_studio_project(project_id: str) -> dict:
        project_dir = _safe_project_dir(project_id)
        source = _read_json_file(project_dir / "artifacts" / "lesson_source.json")
        return {
            "project_id": project_id,
            "title": (_read_json_file(project_dir / "project.json") or {}).get("title", project_id),
            "source_text": (source or {}).get("normalized_text", ""),
            "workflow": read_studio_state(project_dir),
            "provider_ready": bool(_os.environ.get("DASHSCOPE_API_KEY")),
            "board": await asyncio.to_thread(load_board_state, project_dir),
        }

    @app.post("/api/lesson-studio/projects/{project_id}/plan")
    async def plan_lesson_studio_project(project_id: str, request: Request) -> dict:
        require_local_origin(request)
        project_dir = _safe_project_dir(project_id)
        plan = await run_studio_action(f"plan:{project_id}", plan_lesson_storyboard, project_dir)
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"project_id": project_id, "stage": "storyboard_ready", "plan": plan}

    @app.post("/api/lesson-studio/projects/{project_id}/scenes/{scene_id}/image")
    async def generate_lesson_studio_image(
        project_id: str, scene_id: str, request: Request
    ) -> dict:
        require_local_origin(request)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scene_id):
            raise HTTPException(status_code=400, detail="invalid scene id")
        project_dir = _safe_project_dir(project_id)
        asset = await run_studio_action(
            f"image:{project_id}:{scene_id}",
            generate_lesson_scene_image,
            project_dir,
            scene_id,
        )
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"project_id": project_id, "stage": "images_in_review", "asset": asset}

    @app.post("/api/lesson-studio/projects/{project_id}/scenes/{scene_id}/video")
    async def generate_lesson_studio_video(
        project_id: str, scene_id: str, request: Request
    ) -> dict:
        require_local_origin(request)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scene_id):
            raise HTTPException(status_code=400, detail="invalid scene id")
        project_dir = _safe_project_dir(project_id)
        asset = await run_studio_action(
            f"video:{project_id}:{scene_id}",
            generate_lesson_scene_video,
            project_dir,
            scene_id,
        )
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"project_id": project_id, "stage": "videos_in_review", "asset": asset}

    @app.post("/api/lesson-studio/projects/{project_id}/advance")
    async def advance_lesson_studio_project(project_id: str, request: Request) -> dict:
        require_local_origin(request)
        project_dir = _safe_project_dir(project_id)
        state = await run_studio_action(
            f"advance:{project_id}", advance_lesson_stage, project_dir
        )
        _invalidate_summary(project_id)
        hub.publish(project_id)
        return {"project_id": project_id, "stage": state["stage"], "workflow": state}

    @app.get("/api/project/{project_id}/state")
    async def project_state(project_id: str) -> dict:
        project_dir = _safe_project_dir(project_id)
        return await asyncio.to_thread(load_board_state, project_dir)

    @app.get("/api/project/{project_id}/events")
    async def project_events(project_id: str, request: Request) -> StreamingResponse:
        _safe_project_dir(project_id)  # 404 early for unknown projects

        async def stream():
            q = hub.subscribe(project_id)
            try:
                yield _sse({"type": "hello", "project_id": project_id})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat", "ts": time.time()})
                        continue
                    # Coalesce bursts: drain anything else queued.
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    yield _sse({"type": "change", "project_id": project_id})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.get("/api/library/events")
    async def library_events(request: Request) -> StreamingResponse:
        async def stream():
            q = hub.subscribe()
            try:
                yield _sse({"type": "hello"})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        changed = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat", "ts": time.time()})
                        continue
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    yield _sse({"type": "change", "project_id": changed})
            finally:
                hub.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    # ---- Thumbnails (downscaled, cached on disk) ------------------------

    @app.get("/thumb/{project_id}/{file_path:path}")
    async def thumb(project_id: str, file_path: str, w: int = 640) -> FileResponse:
        project_dir = _safe_project_dir(project_id)
        target = (project_dir / file_path).resolve()
        try:
            target.relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        width = min(THUMB_WIDTHS, key=lambda x: abs(x - w))
        cached = await asyncio.to_thread(_thumbnail_for, target, width)
        if cached is None:
            # Never fall back to raw video bytes for an <img> consumer (F-03);
            # non-thumbable images are safe to serve as-is.
            if target.suffix.lower() in {".mp4", ".webm", ".mov"}:
                raise HTTPException(status_code=404, detail="no poster frame available")
            return FileResponse(target)
        return FileResponse(cached, media_type="image/jpeg")

    # ---- Media (range requests handled by FileResponse) ---------------

    @app.get("/media/{project_id}/{file_path:path}")
    async def media(project_id: str, file_path: str) -> FileResponse:
        project_dir = _safe_project_dir(project_id)
        target = (project_dir / file_path).resolve()
        try:
            target.relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="path escapes project")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        return FileResponse(target)

    # ---- UI ------------------------------------------------------------

    @app.get("/p/{project_id}")
    async def board_page(project_id: str) -> HTMLResponse:
        return _ui_html("board.html", ("board.css", "board.js"))

    @app.get("/studio")
    async def lesson_studio_page() -> HTMLResponse:
        return _ui_html("studio.html", ("board.css", "studio.css", "studio.js"))

    @app.get("/p/{project_path:path}")
    async def board_page_path(project_path: str) -> HTMLResponse:
        return _ui_html("board.html", ("board.css", "board.js"))

    @app.get("/")
    async def library_page() -> HTMLResponse:
        return _ui_html("index.html", ("board.css", "library.js"))

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    # The board is a long-lived SPA: a tab keeps running whatever board.js it
    # loaded, and browsers heuristically cache /ui assets. no-cache forces a
    # conditional revalidation (cheap 304 via ETag) on every load so UI fixes
    # show up on a plain refresh. Media/thumb responses keep normal caching.
    @app.middleware("http")
    async def ui_no_cache(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/studio"} or path.startswith("/ui") or path.startswith("/p/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    return app


def _safe_project_dir(project_id: str) -> Path:
    # ':' rejects Windows drive-relative ids like "C:" (PROJECTS_DIR / "C:"
    # collapses back to PROJECTS_DIR itself).
    if any(c in project_id for c in "/\\:") or project_id in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid project id")
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
    return project_dir


def _read_json_file(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _thumbnail_for(source: Path, width: int) -> Optional[Path]:
    """Downscale an image (or extract a video poster frame) to a cached JPEG."""
    suffix = source.suffix.lower()
    is_image = suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    is_video = suffix in {".mp4", ".webm", ".mov"}
    if not (is_image or is_video):
        return None
    try:
        import hashlib
        stat = source.stat()
        key = hashlib.sha1(
            f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode()
        ).hexdigest()[:20]
        cached = THUMB_CACHE_DIR / f"{key}.jpg"
        if cached.is_file():
            return cached
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Unique temp per request — concurrent misses for the same source
        # must not write (and replace from) the same temp file.
        import uuid
        tmp = THUMB_CACHE_DIR / f"{key}.{uuid.uuid4().hex[:8]}.tmp.jpg"
        if is_video:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5",
                 "-i", str(source), "-frames:v", "1",
                 "-vf", f"scale={width}:-2", str(tmp)],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0 or not tmp.is_file():
                return None
        else:
            from PIL import Image
            with Image.open(source) as img:
                img = img.convert("RGB")
                img.thumbnail((width, width * 3))
                img.save(tmp, "JPEG", quality=82)
        tmp.replace(cached)
        return cached
    except Exception:
        return None


app = create_app()
