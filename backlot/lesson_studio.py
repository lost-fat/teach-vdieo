"""Self-service English lesson workflow used by the local Lesson Studio.

The Studio writes the same project artifacts Backlot already observes.  Text
planning and first-frame generation remain explicit user actions; no video API
is called from this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.checkpoint import init_project, write_checkpoint
from lib.lesson_source import build_lesson_source
from lib.shot_prompt_builder import build_video_prompt
from schemas.artifacts import validate_artifact
from tools.graphics.dashscope_image import DashscopeImage
from tools.text.dashscope_text import DashscopeText


SOURCE_MIN_CHARS = 40
SOURCE_MAX_CHARS = 20_000
TITLE_MAX_CHARS = 120
CLIP_SECONDS = 14
ALLOWED_BEATS = {
    "hook", "setup", "tension", "turning_point", "development", "payoff", "reflection",
}
ALLOWED_VISUAL_ROLES = {
    "setting", "movement", "comparison", "cause_effect", "process",
    "historical_event", "abstract_concept", "dialogue", "transition",
}
ALLOWED_VISUAL_MODES = {"direct_evidence", "interpretive", "metaphor", "bridge", "payoff"}
ALLOWED_CARRIER_KINDS = {"person", "object", "place", "process", "question", "motif", "ensemble"}


class LessonStudioError(RuntimeError):
    """Base error safe to surface in the local Studio UI."""


class LessonStudioValidationError(LessonStudioError):
    pass


class LessonStudioProviderError(LessonStudioError):
    pass


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default or {})
    return value if isinstance(value, dict) else deepcopy(default or {})


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (slug[:48].strip("-") or "english-lesson")


def _validate_input(title: str, source_text: str) -> tuple[str, str]:
    clean_title = re.sub(r"\s+", " ", str(title)).strip()
    clean_source = str(source_text).strip()
    if not clean_title:
        clean_title = "English Lesson"
    if len(clean_title) > TITLE_MAX_CHARS:
        raise LessonStudioValidationError(f"标题不能超过 {TITLE_MAX_CHARS} 个字符。")
    if len(clean_source) < SOURCE_MIN_CHARS:
        raise LessonStudioValidationError(f"课文至少需要 {SOURCE_MIN_CHARS} 个字符。")
    if len(clean_source) > SOURCE_MAX_CHARS:
        raise LessonStudioValidationError(f"课文不能超过 {SOURCE_MAX_CHARS} 个字符。")
    return clean_title, clean_source


def create_lesson_project(
    *, title: str, source_text: str, projects_dir: Path
) -> dict[str, Any]:
    clean_title, clean_source = _validate_input(title, source_text)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    project_id = f"{_slugify(clean_title)}-{stamp}-{secrets.token_hex(2)}"
    project_dir = init_project(
        project_id,
        title=clean_title,
        pipeline_type="english-textbook",
        pipeline_dir=projects_dir,
        style_playbook="esl-cinematic-editorial",
    )
    inputs_dir = project_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "article.txt").write_text(clean_source, encoding="utf-8")

    lesson_source = build_lesson_source(clean_source, language="en")
    validate_artifact("lesson_source", lesson_source)
    _atomic_write_json(project_dir / "artifacts" / "lesson_source.json", lesson_source)
    write_checkpoint(
        projects_dir,
        project_id,
        "ingest",
        "completed",
        {"lesson_source": lesson_source},
        pipeline_type="english-textbook",
        style_playbook="esl-cinematic-editorial",
        review={"status": "pass", "summary": "Source locked verbatim by Lesson Studio."},
        metadata={"origin": "lesson_studio"},
    )

    workflow = {
        "version": "1.0",
        "project_id": project_id,
        "stage": "source_ready",
        "status": "ready",
        "message": "课文已锁定，可以生成分镜。",
        "models": {
            "text": "qwen3.7-plus",
            "image": "qwen-image-2.0-pro",
            "video": "wan2.6-i2v-flash",
        },
        "quota_policy": {"free_tier_only": True, "paid_spend_cap_usd": 0},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(project_dir / "studio_state.json", workflow)
    return {
        "project_id": project_id,
        "project_dir": project_dir,
        "studio_url": f"/studio?project={project_id}",
    }


def read_studio_state(project_dir: Path) -> dict[str, Any]:
    return _read_json(
        project_dir / "studio_state.json",
        {
            "version": "1.0",
            "project_id": project_dir.name,
            "stage": "source_ready",
            "status": "ready",
        },
    )


def _update_studio_state(project_dir: Path, **updates: Any) -> dict[str, Any]:
    state = read_studio_state(project_dir)
    state.update(updates)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(project_dir / "studio_state.json", state)
    return state


def _planner_prompt(source_text: str) -> str:
    template_path = (
        Path(__file__).resolve().parents[1]
        / "skills" / "pipelines" / "english-textbook" / "studio-preview-planner.md"
    )
    template = template_path.read_text(encoding="utf-8")
    return f"{template.rstrip()}\n\n<SOURCE_ARTICLE>\n{source_text}\n</SOURCE_ARTICLE>\n"


def _normalize_for_coverage(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("“", '"').replace("”", '"')).strip()


def _require_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LessonStudioValidationError(f"分镜规划缺少字段：{field}")
    return text


def _validate_planner_json(raw: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not 3 <= len(scenes) <= 12:
        raise LessonStudioValidationError("文本模型必须返回 3–12 个连续故事镜头。")
    excerpts = [_require_string(scene.get("source_text"), "scenes[].source_text") for scene in scenes]
    if _normalize_for_coverage(" ".join(excerpts)) != _normalize_for_coverage(source_text):
        raise LessonStudioValidationError("分镜的原文区间没有按顺序完整覆盖课文。")
    beats = {_require_string(scene.get("story_beat"), "scenes[].story_beat") for scene in scenes}
    if not beats.issubset(ALLOWED_BEATS) or len(beats) < 3:
        raise LessonStudioValidationError("视觉故事必须至少包含三个有效叙事阶段。")
    return scenes


def _build_chapters(scenes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    chapters: list[dict[str, Any]] = []
    scene_chapter_ids: list[str] = []
    for index, scene in enumerate(scenes):
        role = str(scene["story_beat"])
        if not chapters or chapters[-1]["role"] != role:
            chapter_id = f"chapter-{len(chapters) + 1:02d}"
            chapters.append({
                "id": chapter_id,
                "role": role,
                "narrative_unit_ids": [],
                "objective": _require_string(scene.get("chapter_objective") or scene.get("story_contribution"), "chapter objective"),
                "entry_state": _require_string(scene.get("state_from"), "state_from"),
                "exit_state": _require_string(scene.get("state_to"), "state_to"),
            })
        chapter = chapters[-1]
        unit_id = f"unit-{index + 1:02d}"
        chapter["narrative_unit_ids"].append(unit_id)
        chapter["exit_state"] = _require_string(scene.get("state_to"), "state_to")
        scene_chapter_ids.append(chapter["id"])
    if len(chapters) < 3:
        raise LessonStudioValidationError("视觉故事章节不足，无法形成起承转合。")
    return chapters, scene_chapter_ids


def _build_scene_plan(raw: dict[str, Any], source_text: str) -> dict[str, Any]:
    source_scenes = _validate_planner_json(raw, source_text)
    chapters, chapter_ids = _build_chapters(source_scenes)
    carrier = raw.get("carrier") if isinstance(raw.get("carrier"), dict) else {}
    carrier_kind = str(carrier.get("kind") or "object")
    if carrier_kind not in ALLOWED_CARRIER_KINDS:
        carrier_kind = "object"
    carrier_traits = carrier.get("traits") if isinstance(carrier.get("traits"), list) else []
    carrier_traits = [str(item).strip() for item in carrier_traits if str(item).strip()]
    if not carrier_traits:
        carrier_traits = [_require_string(carrier.get("description"), "carrier.description")]

    style = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    palette = style.get("palette") if isinstance(style.get("palette"), list) else []
    palette = [str(item).strip() for item in palette if str(item).strip()] or [
        "natural earth", "railway blue", "market green"
    ]
    bible = {
        "entities": [{
            "id": "carrier-main",
            "canonical_name": _require_string(carrier.get("name"), "carrier.name"),
            "immutable_traits": carrier_traits,
        }],
        "locations": [],
        "period": None,
        "style": {
            "palette": palette,
            "lighting": _require_string(style.get("lighting") or "natural documentary daylight", "style.lighting"),
            "texture": _require_string(style.get("texture") or "mature cinematic editorial realism", "style.texture"),
        },
        "camera_rules": [
            "One continuous shot per generated clip",
            "Preserve screen direction and match action across technical boundaries",
        ],
        "prohibited_elements": [
            "readable generated text", "split screens", "maps with labels",
            "talking-head testimonials", "internal hard cuts",
        ],
    }

    narrative_units = []
    scenes = []
    for index, (source_scene, chapter_id) in enumerate(zip(source_scenes, chapter_ids)):
        unit_id = f"unit-{index + 1:02d}"
        scene_id = f"sc_{index + 1}"
        start = index * CLIP_SECONDS
        end = start + CLIP_SECONDS
        role = str(source_scene.get("visual_role") or "setting")
        if role not in ALLOWED_VISUAL_ROLES:
            role = "setting"
        visual_mode = str(source_scene.get("visual_mode") or "interpretive")
        if visual_mode not in ALLOWED_VISUAL_MODES:
            visual_mode = "interpretive"
        actions = source_scene.get("temporal_actions")
        if not isinstance(actions, list) or len(actions) != 3:
            raise LessonStudioValidationError("每个镜头必须包含三个连续动作节拍。")
        actions = [_require_string(action, "temporal_actions[]") for action in actions]
        narrative_units.append({
            "id": unit_id,
            "source_text": _require_string(source_scene.get("source_text"), "source_text"),
            "start_seconds": start,
            "end_seconds": end,
            "discourse_role": role,
        })
        spec: dict[str, Any] = {
            "single_shot": True,
            "subject_motion": _require_string(source_scene.get("subject_motion"), "subject_motion"),
            "camera_motion": _require_string(source_scene.get("camera_motion"), "camera_motion"),
            "temporal_beats": [
                {"start_seconds": 0, "end_seconds": 5, "action": actions[0]},
                {"start_seconds": 5, "end_seconds": 10, "action": actions[1]},
                {"start_seconds": 10, "end_seconds": 14, "action": actions[2]},
            ],
            "continuity_refs": ["carrier-main"],
            "caption_safe_area": "Keep the lower 30 percent quiet but naturally textured for bilingual captions.",
            "negative_constraints": [
                "readable generated text or subtitles", "hard cuts within the shot",
                "split screen", "unstable camera shake", "warped subject geometry",
            ],
        }
        if source_scene.get("foreground_event"):
            spec["foreground_event"] = str(source_scene["foreground_event"]).strip()
        if source_scene.get("visual_payoff"):
            spec["visual_payoff"] = str(source_scene["visual_payoff"]).strip()
        scene = {
            "id": scene_id,
            "type": "generated",
            "description": _require_string(source_scene.get("description"), "description"),
            "start_seconds": start,
            "end_seconds": end,
            "narrative_unit_ids": [unit_id],
            "visual_role": role,
            "story_chapter_id": chapter_id,
            "story_beat": str(source_scene["story_beat"]),
            "story_contribution": _require_string(source_scene.get("story_contribution"), "story_contribution"),
            "visual_mode": visual_mode,
            "visual_state_change": {
                "from": _require_string(source_scene.get("state_from"), "state_from"),
                "to": _require_string(source_scene.get("state_to"), "state_to"),
            },
            "video_prompt_spec": spec,
        }
        if index:
            scene["continuity_from_scene_id"] = f"sc_{index}"
            scene["match_action"] = _require_string(
                source_scene.get("match_action") or raw.get("recurring_motif"),
                "match_action",
            )
        scenes.append(scene)

    plan = {
        "version": "1.0",
        "style_playbook": "esl-cinematic-editorial",
        "narrative_units": narrative_units,
        "visual_story_arc": {
            "theme": _require_string(raw.get("theme"), "theme"),
            "visual_premise": _require_string(raw.get("visual_premise"), "visual_premise"),
            "story_carrier": {
                "id": "story-carrier",
                "kind": carrier_kind,
                "description": _require_string(carrier.get("description"), "carrier.description"),
                "continuity_ref": "carrier-main",
            },
            "opening_state": _require_string(raw.get("opening_state"), "opening_state"),
            "turning_point": _require_string(raw.get("turning_point"), "turning_point"),
            "closing_state": _require_string(raw.get("closing_state"), "closing_state"),
            "recurring_motif": _require_string(raw.get("recurring_motif"), "recurring_motif"),
            "chapters": chapters,
        },
        "continuity_bible": bible,
        "scenes": scenes,
    }
    validate_artifact("scene_plan", plan)
    return plan


def _compile_prompt_cards(plan: dict[str, Any]) -> dict[str, Any]:
    bible = plan["continuity_bible"]
    style = bible["style"]
    carrier = bible["entities"][0]
    shots = []
    for scene in plan["scenes"]:
        compiled = build_video_prompt(scene, bible, provider="wan-i2v")
        image_prompt = (
            "Cinematic editorial first frame for an English learning video. "
            f"{scene['description']} Preserve the recurring story carrier: "
            f"{carrier['canonical_name']} — {', '.join(carrier['immutable_traits'])}. "
            f"Palette: {', '.join(style['palette'])}. Lighting: {style['lighting']}. "
            f"Texture: {style['texture']}. Natural geography and culturally grounded detail, "
            "16:9 composition. Keep the lower 30 percent visually quiet for later bilingual "
            "captions. Do not render words, labels, logos, subtitles, maps with labels, or watermarks."
        )
        shots.append({
            "scene_id": scene["id"],
            "start_seconds": scene["start_seconds"],
            "end_seconds": scene["end_seconds"],
            "story_chapter_id": scene["story_chapter_id"],
            "story_beat": scene["story_beat"],
            "story_contribution": scene["story_contribution"],
            "image_prompt_preview": image_prompt,
            "video_prompt": compiled["prompt"],
            "negative_video_prompt": compiled["negative_prompt"],
            "temporal_beats": scene["video_prompt_spec"]["temporal_beats"],
            "source": "scene_plan_preview",
            "submitted_to_media_api": False,
        })
    return {"version": "1.0", "provider_preview": "wan-i2v", "shots": shots}


def plan_lesson_storyboard(project_dir: Path) -> dict[str, Any]:
    source_path = project_dir / "inputs" / "article.txt"
    source_text = source_path.read_text(encoding="utf-8").strip()
    prompt = _planner_prompt(source_text)
    (project_dir / "inputs" / "studio_planner_prompt.txt").write_text(prompt, encoding="utf-8")
    _update_studio_state(
        project_dir,
        stage="planning_storyboard",
        status="in_progress",
        message="qwen3.7-plus 正在设计整篇视觉故事。",
    )
    result = DashscopeText().execute({
        "model": "qwen3.7-plus",
        "system_prompt": "You are a documentary story director. Return only one valid JSON object.",
        "prompt": prompt,
        "temperature": 0.35,
        "max_tokens": 8192,
        "output_path": str(project_dir / "artifacts" / "studio_planner_raw.json"),
    })
    if not result.success:
        _update_studio_state(project_dir, stage="source_ready", status="error", message=result.error)
        raise LessonStudioProviderError(result.error or "文本规划失败。")
    raw = (result.data or {}).get("json")
    if not isinstance(raw, dict):
        raise LessonStudioProviderError("文本模型没有返回有效分镜 JSON。")

    try:
        plan = _build_scene_plan(raw, source_text)
    except LessonStudioValidationError as first_error:
        repair_prompt = (
            f"Your previous JSON failed validation: {first_error}. Return a corrected complete JSON "
            "using the identical schema and exact source coverage.\n\n"
            f"PREVIOUS_JSON:\n{json.dumps(raw, ensure_ascii=False)}\n\n"
            f"SOURCE_ARTICLE:\n{source_text}"
        )
        repair = DashscopeText().execute({
            "model": "qwen3.7-plus",
            "system_prompt": "Repair the storyboard. Return only one valid JSON object.",
            "prompt": repair_prompt,
            "temperature": 0.15,
            "max_tokens": 8192,
            "output_path": str(project_dir / "artifacts" / "studio_planner_repaired.json"),
        })
        repaired_raw = (repair.data or {}).get("json") if repair.success else None
        if not isinstance(repaired_raw, dict):
            _update_studio_state(project_dir, stage="source_ready", status="error", message=str(first_error))
            raise LessonStudioProviderError(str(first_error))
        plan = _build_scene_plan(repaired_raw, source_text)

    prompts = _compile_prompt_cards(plan)
    _atomic_write_json(project_dir / "artifacts" / "scene_plan.json", plan)
    _atomic_write_json(project_dir / "artifacts" / "compiled_shot_prompts.json", prompts)
    state = _update_studio_state(
        project_dir,
        stage="storyboard_ready",
        status="awaiting_human",
        message="分镜已生成。请检查故事、首帧提示词和视频提示词，再逐镜生成图片。",
        scene_count=len(plan["scenes"]),
        images_generated=0,
    )
    return {
        "scene_count": len(plan["scenes"]),
        "total_duration_seconds": len(plan["scenes"]) * CLIP_SECONDS,
        "theme": plan["visual_story_arc"]["theme"],
        "state": state,
    }


def _safe_scene_id(scene_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scene_id):
        raise LessonStudioValidationError("无效的镜头 ID。")
    return scene_id


def generate_lesson_scene_image(project_dir: Path, scene_id: str) -> dict[str, Any]:
    scene_id = _safe_scene_id(scene_id)
    plan = _read_json(project_dir / "artifacts" / "scene_plan.json")
    scene = next((item for item in plan.get("scenes", []) if item.get("id") == scene_id), None)
    if not isinstance(scene, dict):
        raise LessonStudioValidationError("镜头不存在或分镜尚未生成。")
    prompt_artifact = _read_json(project_dir / "artifacts" / "compiled_shot_prompts.json")
    prompt_card = next(
        (item for item in prompt_artifact.get("shots", []) if item.get("scene_id") == scene_id),
        None,
    )
    if not isinstance(prompt_card, dict):
        raise LessonStudioValidationError("该镜头缺少首帧提示词。")

    manifest_path = project_dir / "artifacts" / "asset_manifest.json"
    manifest = _read_json(manifest_path, {"version": "1.0", "assets": [], "total_cost_usd": 0})
    existing = [
        asset for asset in manifest.get("assets", [])
        if asset.get("scene_id") == scene_id and asset.get("type") == "image"
    ]
    take = len(existing) + 1
    filename = f"{scene_id}-take-{take}.png"
    output_path = project_dir / "assets" / "images" / filename
    source = _read_json(project_dir / "artifacts" / "lesson_source.json")
    seed_material = f"{source.get('source_sha256', '')}:{scene_id}:{take}"
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:8], 16) % 2_147_483_648
    _update_studio_state(
        project_dir,
        stage="generating_image",
        status="in_progress",
        active_scene_id=scene_id,
        message=f"qwen-image-2.0-pro 正在生成 {scene_id} 首帧。",
    )
    result = DashscopeImage().execute({
        "model": "qwen-image-2.0-pro",
        "prompt": str(prompt_card["image_prompt_preview"]),
        "negative_prompt": (
            "readable text, subtitles, labels, logos, watermark, split screen, "
            "collage, warped geometry, duplicate subjects"
        ),
        "size": "2688*1536",
        "n": 1,
        "prompt_extend": False,
        "watermark": False,
        "seed": seed,
        "output_path": str(output_path),
        "scene_id": scene_id,
    })
    if not result.success:
        _update_studio_state(
            project_dir,
            stage="storyboard_ready",
            status="error",
            active_scene_id=None,
            message=result.error,
        )
        raise LessonStudioProviderError(result.error or "图片生成失败。")

    asset_id = f"image-{scene_id}-take-{take}"
    rel_path = output_path.relative_to(project_dir).as_posix()
    manifest.setdefault("assets", []).append({
        "id": asset_id,
        "type": "image",
        "path": rel_path,
        "source_tool": "dashscope_image",
        "scene_id": scene_id,
        "prompt": str(prompt_card["image_prompt_preview"]),
        "negative_prompt": (
            "readable text, subtitles, labels, logos, watermark, split screen, "
            "collage, warped geometry, duplicate subjects"
        ),
        "seed": seed,
        "model": "qwen-image-2.0-pro",
        "cost_usd": float(result.cost_usd or 0),
        "resolution": "2688x1536",
        "format": "png",
        "subtype": "generated-first-frame",
        "generation_summary": "One user-triggered Beijing DashScope call; prompt extension, watermark, and provider fallback disabled.",
        "provider": "dashscope",
        "license": "AI-generated under the configured DashScope account terms",
    })
    manifest["total_cost_usd"] = round(
        sum(float(asset.get("cost_usd") or 0) for asset in manifest["assets"]), 4
    )
    manifest["metadata"] = {
        **(manifest.get("metadata") or {}),
        "free_tier_only": True,
        "paid_spend_cap_usd": 0,
        "cost_basis": "estimated_list_price_not_account_charge",
        "fallback_used": False,
    }
    validate_artifact("asset_manifest", manifest)
    _atomic_write_json(manifest_path, manifest)
    images_generated = sum(1 for asset in manifest["assets"] if asset.get("type") == "image")
    _update_studio_state(
        project_dir,
        stage="images_in_review",
        status="awaiting_human",
        active_scene_id=None,
        images_generated=images_generated,
        message="首帧已生成。可以继续生成其他镜头或重新生成当前镜头。",
    )
    return {"asset_id": asset_id, "scene_id": scene_id, "path": rel_path, "take": take}

