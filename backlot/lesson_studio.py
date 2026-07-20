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
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.checkpoint import init_project, write_checkpoint
from lib.lesson_source import build_lesson_source
from schemas.artifacts import validate_artifact
from tools.graphics.dashscope_image import DashscopeImage
from tools.text.dashscope_text import DashscopeText
from tools.video.dashscope_video import DashscopeVideo


SOURCE_MIN_CHARS = 40
SOURCE_MAX_CHARS = 20_000
TITLE_MAX_CHARS = 120
CLIP_SECONDS = 5
CLIP_BEAT_RANGES = ((0, 2), (2, 4), (4, 5))
IMAGE_NEGATIVE_PROMPT = "禁止可读文字、字幕、标签、标志、水印、分屏、拼贴、几何变形和重复主体。"
ALLOWED_BEATS = {
    "hook", "setup", "tension", "turning_point", "development", "payoff", "reflection",
}
ALLOWED_VISUAL_ROLES = {
    "setting", "movement", "comparison", "cause_effect", "process",
    "historical_event", "abstract_concept", "dialogue", "transition",
}
ALLOWED_VISUAL_MODES = {"direct_evidence", "interpretive", "metaphor", "bridge", "payoff"}
ALLOWED_CARRIER_KINDS = {"person", "object", "place", "process", "question", "motif", "ensemble"}
ALLOWED_HUMAN_PRESENCE = {"none", "background", "supporting", "primary"}
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HUMAN_SOURCE_RE = re.compile(
    r"\b(?:people|person|persons|passenger|passengers|worker|workers|"
    r"businessman|businesswoman|businesspeople|manager|managers|family|families|"
    r"child|children|student|students|teacher|teachers|farmer|farmers|resident|"
    r"residents|customer|customers|citizen|citizens|kenyan|kenyans|friend|friends)\b",
    re.IGNORECASE,
)
_INTERNAL_EDIT_RE = re.compile(r"分屏|硬切|切回|切换到|切至|切到|淡入|淡出|蒙太奇")
_EDIT_NEGATION_PREFIX_RE = re.compile(
    r"(?:避免|禁止|不要|不得|不应|不再|不直接展示|不展示|不使用|"
    r"不采用|不出现|不包含|没有|无需|无须|无)"
    r"(?:任何|真实|实际|镜头内|反射|画面|文字|的|\s)*$"
)
_PROJECT_WRITE_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_WRITE_LOCKS_GUARD = threading.Lock()


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


def _project_write_lock(project_dir: Path) -> threading.RLock:
    key = str(project_dir.resolve())
    with _PROJECT_WRITE_LOCKS_GUARD:
        return _PROJECT_WRITE_LOCKS.setdefault(key, threading.RLock())


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
    with _project_write_lock(project_dir):
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


def _strip_action_timecode(value: str) -> str:
    return re.sub(
        r"^\s*\d+(?:\.\d+)?\s*[–—-]\s*\d+(?:\.\d+)?\s*秒\s*[:：]?\s*",
        "",
        value,
    ).strip() or value


def _require_simplified_chinese(value: Any, field: str) -> None:
    text = _require_string(value, field)
    if not _CJK_RE.search(text):
        raise LessonStudioValidationError(
            f"分镜字段 {field} 必须使用简体中文（专名可保留原文）。"
        )


def _source_requires_people(source_text: str) -> bool:
    return bool(_HUMAN_SOURCE_RE.search(source_text))


def _requests_internal_edit(value: str) -> bool:
    for match in _INTERNAL_EDIT_RE.finditer(value):
        clause_start = max(
            value.rfind(separator, 0, match.start())
            for separator in ("，", "。", "；", ",", ";")
        )
        prefix = value[clause_start + 1:match.start()]
        if _EDIT_NEGATION_PREFIX_RE.search(prefix):
            continue
        return True
    return False


def _validate_chinese_generation_fields(raw: dict[str, Any], scenes: list[dict[str, Any]]) -> None:
    for field in (
        "theme", "visual_premise", "opening_state", "turning_point",
        "closing_state", "recurring_motif",
    ):
        _require_simplified_chinese(raw.get(field), field)

    carrier = raw.get("carrier") if isinstance(raw.get("carrier"), dict) else {}
    for field in ("name", "description"):
        _require_simplified_chinese(carrier.get(field), f"carrier.{field}")
    traits = carrier.get("traits") if isinstance(carrier.get("traits"), list) else []
    if not traits:
        raise LessonStudioValidationError("分镜规划缺少字段：carrier.traits")
    for index, value in enumerate(traits):
        _require_simplified_chinese(value, f"carrier.traits[{index}]")

    style = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    palette = style.get("palette") if isinstance(style.get("palette"), list) else []
    if not palette:
        raise LessonStudioValidationError("分镜规划缺少字段：style.palette")
    for index, value in enumerate(palette):
        _require_simplified_chinese(value, f"style.palette[{index}]")
    for field in ("lighting", "texture"):
        _require_simplified_chinese(style.get(field), f"style.{field}")

    required_scene_fields = (
        "chapter_objective", "story_contribution", "description", "state_from",
        "state_to", "subject_motion", "camera_motion",
    )
    optional_scene_fields = ("foreground_event", "visual_payoff", "match_action")
    for scene_index, scene in enumerate(scenes):
        for field in required_scene_fields:
            _require_simplified_chinese(scene.get(field), f"scenes[{scene_index}].{field}")
        actions = scene.get("temporal_actions")
        if not isinstance(actions, list) or len(actions) != 3:
            raise LessonStudioValidationError("每个镜头必须包含三个连续动作节拍。")
        for action_index, action in enumerate(actions):
            _require_simplified_chinese(
                action, f"scenes[{scene_index}].temporal_actions[{action_index}]"
            )
        for field in optional_scene_fields:
            if scene.get(field):
                _require_simplified_chinese(scene[field], f"scenes[{scene_index}].{field}")

        presence = str(scene.get("human_presence") or "")
        if presence not in ALLOWED_HUMAN_PRESENCE:
            raise LessonStudioValidationError(
                "每个镜头必须设置 human_presence：none、background、supporting 或 primary。"
            )
        if presence != "none":
            _require_simplified_chinese(
                scene.get("human_action"), f"scenes[{scene_index}].human_action"
            )
        continuous_text = " ".join([
            str(scene.get("description") or ""),
            str(scene.get("subject_motion") or ""),
            str(scene.get("camera_motion") or ""),
            *(str(action) for action in scene.get("temporal_actions", [])),
        ])
        if _requests_internal_edit(continuous_text):
            raise LessonStudioValidationError(
                f"scenes[{scene_index}] 必须描述连续单镜头，不能包含分屏或镜头内剪切。"
            )


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
    _validate_chinese_generation_fields(raw, scenes)
    if _source_requires_people(source_text) and not any(
        str(scene.get("human_presence")) != "none" for scene in scenes
    ):
        raise LessonStudioValidationError(
            "课文包含乘客、职工、商人或家庭等人物，分镜不能全部无人；"
            "请至少安排一个人物自然行动的镜头。"
        )
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
        "自然大地色", "铁路蓝", "市场绿"
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
            "lighting": _require_string(style.get("lighting") or "自然纪录片日光", "style.lighting"),
            "texture": _require_string(style.get("texture") or "成熟的电影感编辑写实质感", "style.texture"),
        },
        "camera_rules": [
            "每个生成片段只使用一个连续镜头",
            "保持屏幕运动方向，并在技术切点前后匹配动作",
        ],
        "prohibited_elements": [
            "可读的生成文字", "分屏", "带标签的地图",
            "正面访谈式画面", "镜头内部硬切",
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
        actions = [
            _strip_action_timecode(_require_string(action, "temporal_actions[]"))
            for action in actions
        ]
        human_presence = str(source_scene.get("human_presence") or "none")
        human_action = str(source_scene.get("human_action") or "").strip()
        description = _require_string(source_scene.get("description"), "description")
        subject_motion = _require_string(source_scene.get("subject_motion"), "subject_motion")
        if human_presence != "none":
            presence_label = {
                "background": "自然背景人物",
                "supporting": "参与叙事的配角",
                "primary": "主要行动人物",
            }[human_presence]
            description = f"{description} 人物呈现：{presence_label}，{human_action}"
            subject_motion = f"{subject_motion} 人物行动：{human_action}"
        narrative_units.append({
            "id": unit_id,
            "source_text": _require_string(source_scene.get("source_text"), "source_text"),
            "start_seconds": start,
            "end_seconds": end,
            "discourse_role": role,
        })
        spec: dict[str, Any] = {
            "single_shot": True,
            "subject_motion": subject_motion,
            "camera_motion": _require_string(source_scene.get("camera_motion"), "camera_motion"),
            "temporal_beats": [
                {
                    "start_seconds": beat_start,
                    "end_seconds": beat_end,
                    "action": action,
                }
                for (beat_start, beat_end), action in zip(CLIP_BEAT_RANGES, actions)
            ],
            "continuity_refs": ["carrier-main"],
            "caption_safe_area": "画面下方 30% 保持安静且具有自然纹理，为双语字幕留出安全区。",
            "negative_constraints": [
                "可读的生成文字或字幕", "镜头内硬切",
                "分屏", "不稳定的镜头抖动", "主体几何变形",
            ],
        }
        if source_scene.get("foreground_event"):
            spec["foreground_event"] = str(source_scene["foreground_event"]).strip()
        if source_scene.get("visual_payoff"):
            spec["visual_payoff"] = str(source_scene["visual_payoff"]).strip()
        scene = {
            "id": scene_id,
            "type": "generated",
            "description": description,
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
        spec = scene["video_prompt_spec"]
        temporal_text = " ".join(
            f"[{beat['start_seconds']}–{beat['end_seconds']} 秒] {beat['action']}"
            for beat in spec["temporal_beats"]
        )
        video_parts = [
            "生成一个完整连续的单镜头，镜头内不得硬切。",
            f"画面起点：{scene['description']}",
            f"贯穿主体：{carrier['canonical_name']}，稳定特征为{'、'.join(carrier['immutable_traits'])}。",
            f"主体运动：{spec['subject_motion']}",
            f"运镜：{spec['camera_motion']}",
            f"时间动作节拍：{temporal_text}",
        ]
        if spec.get("foreground_event"):
            video_parts.append(f"前景视差事件：{spec['foreground_event']}")
        if spec.get("visual_payoff"):
            video_parts.append(f"结尾视觉回报：{spec['visual_payoff']}")
        video_parts.extend([
            f"色彩：{'、'.join(style['palette'])}；光线：{style['lighting']}；质感：{style['texture']}。",
            "若场景包含人物，人物必须自然参与行动，不摆拍、不凝视镜头，职业、服饰、年龄与当地地域和时代相符。",
            spec["caption_safe_area"],
        ])
        video_prompt = " ".join(video_parts)
        negative_prompt = "禁止：" + "；".join([
            *spec["negative_constraints"],
            *bible.get("prohibited_elements", []),
        ]) + "。"
        image_prompt = (
            "英语教学视频的电影感首帧。"
            f"{scene['description']} 保持贯穿全片的主体："
            f"{carrier['canonical_name']}，稳定特征为{'、'.join(carrier['immutable_traits'])}。"
            f"配色：{'、'.join(style['palette'])}。光线：{style['lighting']}。"
            f"质感：{style['texture']}。地理自然、文化细节真实，16:9 构图。"
            "若场景包含人物，人物必须自然参与行动，不摆拍、不凝视镜头，职业、服饰、年龄与当地地域和时代相符。"
            "画面下方 30% 保持视觉安静，为后续双语字幕留出安全区。"
            "禁止生成文字、标签、标志、字幕、带标签的地图或水印。"
        )
        shots.append({
            "scene_id": scene["id"],
            "start_seconds": scene["start_seconds"],
            "end_seconds": scene["end_seconds"],
            "story_chapter_id": scene["story_chapter_id"],
            "story_beat": scene["story_beat"],
            "story_contribution": scene["story_contribution"],
            "image_prompt_preview": image_prompt,
            "video_prompt": video_prompt,
            "negative_video_prompt": negative_prompt,
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
        "system_prompt": (
            "你是纪录片视觉故事导演。只返回一个有效 JSON 对象。"
            "除 source_text 和规定的英文枚举值外，所有文本字段必须使用简体中文。"
        ),
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
            f"上一个 JSON 未通过校验：{first_error}\n"
            "请返回修正后的完整 JSON，保持课文原文区间精确且完整。"
            "除 source_text 和规定的英文枚举值外，所有文本字段必须使用简体中文。"
            "每个镜头必须补全 human_presence；不为 none 时必须提供中文 human_action。\n\n"
            f"上一个 JSON：\n{json.dumps(raw, ensure_ascii=False)}\n\n"
            f"英文课文原文：\n{source_text}"
        )
        repair = DashscopeText().execute({
            "model": "qwen3.7-plus",
            "system_prompt": "修复分镜规划，只返回一个有效 JSON 对象。",
            "prompt": repair_prompt,
            "temperature": 0.15,
            "max_tokens": 8192,
            "output_path": str(project_dir / "artifacts" / "studio_planner_repaired.json"),
        })
        repaired_raw = (repair.data or {}).get("json") if repair.success else None
        if not isinstance(repaired_raw, dict):
            _update_studio_state(project_dir, stage="source_ready", status="error", message=str(first_error))
            raise LessonStudioProviderError(str(first_error))
        try:
            plan = _build_scene_plan(repaired_raw, source_text)
        except LessonStudioValidationError as repaired_error:
            _update_studio_state(
                project_dir,
                stage="source_ready",
                status="error",
                active_scene_id=None,
                message=str(repaired_error),
            )
            raise

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


def _scene_ids(project_dir: Path) -> list[str]:
    plan = _read_json(project_dir / "artifacts" / "scene_plan.json")
    scene_ids = [
        str(scene.get("id"))
        for scene in plan.get("scenes", [])
        if isinstance(scene, dict) and scene.get("id")
    ]
    if not scene_ids:
        raise LessonStudioValidationError("分镜尚未生成。")
    return scene_ids


def _completed_scene_ids(project_dir: Path, asset_type: str) -> set[str]:
    manifest = _read_json(
        project_dir / "artifacts" / "asset_manifest.json",
        {"version": "1.0", "assets": []},
    )
    return {
        str(asset.get("scene_id"))
        for asset in manifest.get("assets", [])
        if isinstance(asset, dict) and asset.get("type") == asset_type and asset.get("scene_id")
    }


def _append_lesson_asset(project_dir: Path, asset: dict[str, Any]) -> dict[str, Any]:
    """Merge one completed asset into the latest manifest atomically.

    Image/video API calls may run in parallel for different scenes.  Only the
    short manifest commit is serialized so a later completion cannot overwrite
    an earlier completion with the stale manifest it read before the API call.
    """
    manifest_path = project_dir / "artifacts" / "asset_manifest.json"
    with _project_write_lock(project_dir):
        manifest = _read_json(
            manifest_path,
            {"version": "1.0", "assets": [], "total_cost_usd": 0},
        )
        assets = manifest.get("assets")
        if not isinstance(assets, list):
            assets = []
            manifest["assets"] = assets
        asset_id = str(asset.get("id") or "")
        if not any(str(existing.get("id") or "") == asset_id for existing in assets):
            assets.append(asset)
        manifest["total_cost_usd"] = round(
            sum(float(existing.get("cost_usd") or 0) for existing in assets), 4
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
        return deepcopy(manifest)


def _finish_lesson_asset(
    project_dir: Path,
    asset: dict[str, Any],
    *,
    asset_type: str,
    stage: str,
    message: str,
) -> dict[str, Any]:
    with _project_write_lock(project_dir):
        manifest = _append_lesson_asset(project_dir, asset)
        generated = sum(
            1 for item in manifest["assets"] if item.get("type") == asset_type
        )
        counter = "images_generated" if asset_type == "image" else "videos_generated"
        _update_studio_state(
            project_dir,
            stage=stage,
            status="awaiting_human",
            active_scene_id=None,
            message=message,
            **{counter: generated},
        )
        return manifest


def reconcile_lesson_assets(project_dir: Path) -> dict[str, int]:
    """Restore provider-success files omitted by an interrupted/stale commit."""
    prompt_artifact = _read_json(project_dir / "artifacts" / "compiled_shot_prompts.json")
    prompt_cards = {
        str(item.get("scene_id")): item
        for item in prompt_artifact.get("shots", [])
        if isinstance(item, dict) and item.get("scene_id")
    }
    plan = _read_json(project_dir / "artifacts" / "scene_plan.json")
    scenes = {
        str(item.get("id")): item
        for item in plan.get("scenes", [])
        if isinstance(item, dict) and item.get("id")
    }
    source = _read_json(project_dir / "artifacts" / "lesson_source.json")
    source_hash = str(source.get("source_sha256") or "")
    manifest = _read_json(
        project_dir / "artifacts" / "asset_manifest.json",
        {"version": "1.0", "assets": []},
    )
    known_paths = {
        str(item.get("path"))
        for item in manifest.get("assets", [])
        if isinstance(item, dict) and item.get("path")
    }
    recovered = {"images_recovered": 0, "videos_recovered": 0}
    pattern = re.compile(r"^(?P<scene>[A-Za-z0-9_-]+)-take-(?P<take>\d+)\.(?P<ext>png|mp4)$")

    candidates = [
        *(project_dir / "assets" / "images").glob("*-take-*.png"),
        *(project_dir / "assets" / "video").glob("*-take-*.mp4"),
    ]
    for path in sorted(candidates):
        rel_path = path.relative_to(project_dir).as_posix()
        if rel_path in known_paths:
            continue
        match = pattern.fullmatch(path.name)
        if not match:
            continue
        scene_id = match.group("scene")
        take = int(match.group("take"))
        card = prompt_cards.get(scene_id)
        scene = scenes.get(scene_id)
        if not isinstance(card, dict) or not isinstance(scene, dict):
            continue
        is_image = match.group("ext") == "png"
        seed_suffix = f"{scene_id}:{take}" if is_image else f"{scene_id}:video:{take}"
        seed = int(
            hashlib.sha256(f"{source_hash}:{seed_suffix}".encode()).hexdigest()[:8], 16
        ) % 2_147_483_648
        if is_image:
            asset = {
                "id": f"image-{scene_id}-take-{take}",
                "type": "image",
                "path": rel_path,
                "source_tool": "dashscope_image",
                "scene_id": scene_id,
                "prompt": str(card.get("image_prompt_preview") or ""),
                "negative_prompt": IMAGE_NEGATIVE_PROMPT,
                "seed": seed,
                "model": "qwen-image-2.0-pro",
                "cost_usd": 0.02,
                "resolution": "2688x1536",
                "format": "png",
                "subtype": "generated-first-frame",
                "generation_summary": "从已成功落盘但未登记的北京百炼图片文件恢复。",
                "provider": "dashscope",
                "license": "AI-generated under the configured DashScope account terms",
            }
            recovered["images_recovered"] += 1
        else:
            duration = int(
                round(float(scene.get("end_seconds", 0)) - float(scene.get("start_seconds", 0)))
            )
            asset = {
                "id": f"video-{scene_id}-take-{take}",
                "type": "video",
                "path": rel_path,
                "source_tool": "dashscope_video",
                "scene_id": scene_id,
                "prompt": str(card.get("video_prompt") or ""),
                "negative_prompt": str(card.get("negative_video_prompt") or ""),
                "seed": seed,
                "model": "wan2.6-i2v-flash",
                "cost_usd": 0,
                "duration_seconds": duration,
                "resolution": "1080P",
                "format": "mp4",
                "subtype": "generated-continuous-shot",
                "generation_summary": "从已成功落盘但未登记的北京百炼视频文件恢复。",
                "provider": "dashscope",
                "license": "AI-generated under the configured DashScope account terms",
            }
            if isinstance(scene.get("video_prompt_spec"), dict):
                asset["video_prompt_spec"] = scene["video_prompt_spec"]
            recovered["videos_recovered"] += 1
        manifest = _append_lesson_asset(project_dir, asset)
        known_paths.add(rel_path)

    if any(recovered.values()) and (project_dir / "studio_state.json").is_file():
        _update_studio_state(
            project_dir,
            images_generated=sum(
                1 for item in manifest.get("assets", []) if item.get("type") == "image"
            ),
            videos_generated=sum(
                1 for item in manifest.get("assets", []) if item.get("type") == "video"
            ),
        )
    return recovered


def advance_lesson_stage(project_dir: Path) -> dict[str, Any]:
    state = read_studio_state(project_dir)
    stage = str(state.get("stage") or "source_ready")
    scene_ids = _scene_ids(project_dir)
    if stage in {
        "source_ready", "storyboard_ready", "images_in_review", "generating_image",
    }:
        missing = [sid for sid in scene_ids if sid not in _completed_scene_ids(project_dir, "image")]
        if missing:
            raise LessonStudioValidationError(f"还需生成 {len(missing)} 张首帧才能进入视频阶段。")
        return _update_studio_state(
            project_dir,
            stage="video_ready",
            status="awaiting_human",
            message="首帧已全部确认。请逐镜生成并审阅视频。",
        )
    if stage in {"video_ready", "videos_in_review", "generating_video"}:
        missing = [sid for sid in scene_ids if sid not in _completed_scene_ids(project_dir, "video")]
        if missing:
            raise LessonStudioValidationError(f"还需生成 {len(missing)} 段视频才能进入合成阶段。")
        return _update_studio_state(
            project_dir,
            stage="compose_ready",
            status="awaiting_human",
            message="视频镜头已全部确认。下一步是慢速旁白、双语字幕与 Remotion 合成。",
        )
    if stage == "compose_ready":
        raise LessonStudioValidationError("项目已进入字幕与合成阶段。")
    raise LessonStudioValidationError("当前阶段尚不能进入下一步。")


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
        "negative_prompt": IMAGE_NEGATIVE_PROMPT,
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
    asset = {
        "id": asset_id,
        "type": "image",
        "path": rel_path,
        "source_tool": "dashscope_image",
        "scene_id": scene_id,
        "prompt": str(prompt_card["image_prompt_preview"]),
        "negative_prompt": IMAGE_NEGATIVE_PROMPT,
        "seed": seed,
        "model": "qwen-image-2.0-pro",
        "cost_usd": float(result.cost_usd or 0),
        "resolution": "2688x1536",
        "format": "png",
        "subtype": "generated-first-frame",
        "generation_summary": "用户单次点击触发的北京百炼文生图；关闭提示词改写、水印和模型回退。",
        "provider": "dashscope",
        "license": "AI-generated under the configured DashScope account terms",
    }
    _finish_lesson_asset(
        project_dir,
        asset,
        asset_type="image",
        stage="images_in_review",
        message="首帧已生成。可以继续生成其他镜头或重新生成当前镜头。",
    )
    return {"asset_id": asset_id, "scene_id": scene_id, "path": rel_path, "take": take}


def _project_asset_path(project_dir: Path, stored_path: str) -> Path:
    candidate = (project_dir / str(stored_path)).resolve()
    project_root = project_dir.resolve()
    if not candidate.is_relative_to(project_root):
        raise LessonStudioValidationError("素材路径超出当前项目。")
    return candidate


def generate_lesson_scene_video(project_dir: Path, scene_id: str) -> dict[str, Any]:
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
        raise LessonStudioValidationError("该镜头缺少视频生成提示词。")

    manifest_path = project_dir / "artifacts" / "asset_manifest.json"
    manifest = _read_json(manifest_path, {"version": "1.0", "assets": [], "total_cost_usd": 0})
    images = [
        asset for asset in manifest.get("assets", [])
        if asset.get("scene_id") == scene_id and asset.get("type") == "image"
    ]
    if not images:
        raise LessonStudioValidationError("请先为该镜头生成并确认首帧。")
    reference_path = _project_asset_path(project_dir, str(images[-1].get("path") or ""))
    if not reference_path.is_file():
        raise LessonStudioValidationError("该镜头的首帧文件不存在。")

    existing = [
        asset for asset in manifest.get("assets", [])
        if asset.get("scene_id") == scene_id and asset.get("type") == "video"
    ]
    take = len(existing) + 1
    duration = int(round(float(scene["end_seconds"]) - float(scene["start_seconds"])))
    if not 2 <= duration <= 15:
        raise LessonStudioValidationError("视频镜头时长必须为 2–15 秒的整数。")
    filename = f"{scene_id}-take-{take}.mp4"
    output_path = project_dir / "assets" / "video" / filename
    task_state_path = project_dir / "state" / f"{scene_id}-take-{take}-wan.json"
    source = _read_json(project_dir / "artifacts" / "lesson_source.json")
    seed_material = f"{source.get('source_sha256', '')}:{scene_id}:video:{take}"
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:8], 16) % 2_147_483_648
    _update_studio_state(
        project_dir,
        stage="generating_video",
        status="in_progress",
        active_scene_id=scene_id,
        message=f"wan2.6-i2v-flash 正在生成 {scene_id} 的 {duration} 秒连续镜头。",
    )
    result = DashscopeVideo().execute({
        "model": "wan2.6-i2v-flash",
        "operation": "image_to_video",
        "prompt": str(prompt_card["video_prompt"]),
        "negative_prompt": str(prompt_card["negative_video_prompt"]),
        "reference_image_path": str(reference_path),
        "duration": duration,
        "resolution": "1080P",
        "audio": False,
        "prompt_extend": False,
        "shot_type": "single",
        "watermark": False,
        "seed": seed,
        "output_path": str(output_path),
        "task_state_path": str(task_state_path),
    })
    if not result.success:
        _update_studio_state(
            project_dir,
            stage="video_ready" if not existing else "videos_in_review",
            status="error",
            active_scene_id=None,
            message=result.error,
        )
        raise LessonStudioProviderError(result.error or "视频生成失败。")

    asset_id = f"video-{scene_id}-take-{take}"
    rel_path = output_path.relative_to(project_dir).as_posix()
    asset = {
        "id": asset_id,
        "type": "video",
        "path": rel_path,
        "source_tool": "dashscope_video",
        "scene_id": scene_id,
        "prompt": str(prompt_card["video_prompt"]),
        "negative_prompt": str(prompt_card["negative_video_prompt"]),
        "video_prompt_spec": scene["video_prompt_spec"],
        "seed": seed,
        "model": "wan2.6-i2v-flash",
        "cost_usd": float(result.cost_usd or 0),
        "duration_seconds": duration,
        "resolution": "1080P",
        "format": "mp4",
        "subtype": "generated-continuous-shot",
        "generation_summary": "用户单次点击触发的北京百炼图生视频；关闭模型回退、原生音频、提示词改写和水印。",
        "provider": "dashscope",
        "license": "AI-generated under the configured DashScope account terms",
    }
    _finish_lesson_asset(
        project_dir,
        asset,
        asset_type="video",
        stage="videos_in_review",
        message="镜头视频已生成。可以继续生成其他镜头，或重新生成当前镜头。",
    )
    return {"asset_id": asset_id, "scene_id": scene_id, "path": rel_path, "take": take}
