"""Recoverable narration, bilingual-caption, and Remotion lesson compose.

The generated scene videos are immutable inputs.  Compose writes only new
artifacts below ``assets/audio``, ``artifacts``, and ``renders`` so a provider
or renderer failure can never discard an approved shot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlot.lesson_studio import (
    TEXT_MODEL,
    LessonStudioProviderError,
    LessonStudioValidationError,
    _append_lesson_asset,
    _atomic_write_json,
    _project_asset_path,
    _read_json,
    _update_studio_state,
    read_studio_state,
)
from lib.lesson_alignment import align_asr_words, build_raw_qa_transcript
from lib.lesson_source import validate_narration_timeline
from schemas.artifacts import validate_artifact
from tools.analysis.dashscope_asr import DashscopeAsr
from tools.audio.dashscope_tts import DashscopeTTS, VOICE_DESIGN_MODEL
from tools.text.dashscope_text import DashscopeText
from tools.video.video_compose import VideoCompose


ASR_MODEL = "qwen3-asr-flash-filetrans"
VOICE_PROFILE = "english_teacher_female"
CAPTION_MAX_WORDS = 10
CAPTION_MIN_WORDS = 4
TRANSLATION_MAX_CHARS_PER_LINE = 20
NARRATION_LEAD_SECONDS = 0.15
NARRATION_SLOW_FACTOR = 0.88


def _ensure_local_media_tools() -> None:
    """Expose the FFmpeg pair bundled with the installed Remotion compositor."""

    repo_root = Path(__file__).resolve().parents[1]
    candidates = sorted(
        (repo_root / "remotion-composer" / "node_modules" / "@remotion").glob(
            "compositor-*/ffmpeg"
        )
    )
    if not candidates:
        return
    tool_dir = candidates[0].parent
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if str(tool_dir) not in path_parts:
        os.environ["PATH"] = os.pathsep.join([str(tool_dir), *path_parts])
    library_parts = [
        item for item in os.environ.get("DYLD_LIBRARY_PATH", "").split(os.pathsep)
        if item
    ]
    if str(tool_dir) not in library_parts:
        os.environ["DYLD_LIBRARY_PATH"] = os.pathsep.join([str(tool_dir), *library_parts])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_media(path: Path, *, require_video: bool = True) -> dict[str, Any]:
    _ensure_local_media_tools()
    if not shutil.which("ffprobe"):
        raise LessonStudioValidationError("当前环境找不到 FFprobe，无法安全校验镜头视频。")
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise LessonStudioValidationError(
            f"镜头视频无法解码：{path.name}（{completed.stderr.strip() or 'ffprobe 失败'}）"
        )
    try:
        probe = json.loads(completed.stdout)
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LessonStudioValidationError(f"镜头视频探测结果无效：{path.name}") from exc
    video_stream = next(
        (stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if duration <= 0 or (require_video and not isinstance(video_stream, dict)):
        raise LessonStudioValidationError(f"镜头视频缺少有效视频流：{path.name}")
    return {
        "duration_seconds": round(duration, 3),
        "codec": str((video_stream or {}).get("codec_name") or "unknown"),
        "width": int((video_stream or {}).get("width") or 0),
        "height": int((video_stream or {}).get("height") or 0),
    }


def _selected_video_inputs(project_dir: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = _read_json(
        project_dir / "artifacts" / "asset_manifest.json",
        {"version": "1.0", "assets": []},
    )
    videos = [item for item in manifest.get("assets", []) if item.get("type") == "video"]
    selected: list[dict[str, Any]] = []
    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("id") or "")
        candidates = [item for item in videos if str(item.get("scene_id") or "") == scene_id]
        if not candidates:
            raise LessonStudioValidationError(f"镜头 {scene_id} 没有已确认的视频。")
        asset = candidates[-1]
        path = _project_asset_path(project_dir, str(asset.get("path") or ""))
        if not path.is_file():
            raise LessonStudioValidationError(f"镜头 {scene_id} 的视频文件不存在。")
        probe = _probe_media(path)
        expected = float(scene.get("end_seconds", 0)) - float(scene.get("start_seconds", 0))
        if probe["duration_seconds"] + 0.3 < expected:
            raise LessonStudioValidationError(
                f"镜头 {scene_id} 只有 {probe['duration_seconds']} 秒，短于计划的 {expected:g} 秒。"
            )
        selected.append({
            "scene_id": scene_id,
            "asset_id": str(asset.get("id") or ""),
            "path": str(asset.get("path") or ""),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "probe": probe,
        })
    return selected


def _lock_compose_inputs(project_dir: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot_path = project_dir / "artifacts" / "compose_input_snapshot.json"
    selected = _selected_video_inputs(project_dir, plan)
    current = {
        "version": "1.0",
        "policy": "approved scene videos are immutable read-only compose inputs",
        "videos": selected,
    }
    existing = _read_json(snapshot_path)
    if existing:
        if existing.get("videos") != selected:
            raise LessonStudioValidationError(
                "合成输入与已锁定快照不一致；为避免覆盖或误用镜头，已停止。"
            )
        return selected
    current["created_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(snapshot_path, current)
    return selected


def _next_attempt_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    first = directory / f"{stem}{suffix}"
    if not first.exists():
        return first
    index = 2
    while (directory / f"{stem}-{index}{suffix}").exists():
        index += 1
    return directory / f"{stem}-{index}{suffix}"


def _normalize_asr_word_durations(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand provider zero-duration boundary tokens to a safe 1ms interval."""

    normalized: list[dict[str, Any]] = []
    for word in words:
        item = dict(word)
        start = float(item.get("begin_time_seconds", item.get("start_seconds", 0)))
        end = float(item.get("end_time_seconds", item.get("end_seconds", 0)))
        if end <= start:
            item["end_time_seconds"] = round(start + 0.001, 3)
        normalized.append(item)
    return normalized


def _latest_completed_asr(audio_dir: Path, project_dir: Path) -> tuple[Path, Path] | None:
    outputs = sorted(audio_dir.glob("narration-asr*.json"), key=lambda path: path.stat().st_mtime)
    for output in reversed(outputs):
        task_state = project_dir / "state" / f"{output.stem}-task.json"
        state = _read_json(task_state)
        raw_stem = output.stem.replace("narration-asr", "narration-raw", 1)
        raw_audio = audio_dir / f"{raw_stem}.wav"
        if state.get("status") == "succeeded" and raw_audio.is_file():
            return raw_audio, output
    return None


def _source_unit_ranges(source_text: str, units: list[dict[str, Any]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for index, unit in enumerate(units):
        excerpt = str(unit.get("source_text") or "")
        if not excerpt or not source_text.startswith(excerpt, cursor):
            raise LessonStudioValidationError(
                f"旁白单元 {index + 1} 与锁定课文不连续，无法建立字幕时间轴。"
            )
        end = cursor + len(excerpt)
        if index < len(units) - 1:
            while end < len(source_text) and source_text[end].isspace():
                end += 1
        ranges.append((cursor, end))
        cursor = end
    if cursor != len(source_text):
        raise LessonStudioValidationError("旁白单元没有完整覆盖锁定课文。")
    return ranges


def _unit_word_ranges(source_text: str, units: list[dict[str, Any]]) -> list[tuple[int, int]]:
    ranges = _source_unit_ranges(source_text, units)
    result: list[tuple[int, int]] = []
    cursor = 0
    for start, end in ranges:
        count = len(source_text[start:end].split())
        result.append((cursor, cursor + count))
        cursor += count
    if cursor != len(source_text.split()):
        raise LessonStudioValidationError("旁白单元词数与锁定课文不一致。")
    return result


def _run_ffmpeg(command: list[str], *, description: str) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = "\n".join((completed.stderr or completed.stdout).splitlines()[-12:])
        raise LessonStudioProviderError(f"{description}失败：{detail or 'FFmpeg 返回错误'}")


def _build_slow_narration(
    *,
    raw_audio: Path,
    output_audio: Path,
    aligned_words: list[dict[str, Any]],
    source_text: str,
    units: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _ensure_local_media_tools()
    if not shutil.which("ffmpeg"):
        raise LessonStudioValidationError("当前环境找不到 FFmpeg，无法制作慢速旁白。")
    raw_duration = _probe_media(raw_audio, require_video=False)["duration_seconds"]
    word_ranges = _unit_word_ranges(source_text, units)
    remapped: list[dict[str, Any]] = []
    pacing: list[dict[str, Any]] = []
    output_audio.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lesson-audio-", dir=output_audio.parent) as temp_name:
        temp_dir = Path(temp_name)
        segment_paths: list[Path] = []
        for index, (unit, (word_start, word_end)) in enumerate(zip(units, word_ranges)):
            unit_words = aligned_words[word_start:word_end]
            if not unit_words:
                raise LessonStudioValidationError(f"旁白单元 {index + 1} 没有 ASR 单词时间。")
            scene_start = float(unit.get("start_seconds", index * 5))
            scene_end = float(unit.get("end_seconds", scene_start + 5))
            scene_duration = scene_end - scene_start
            clip_start = max(0.0, unit_words[0]["start_ms"] / 1000 - 0.08)
            clip_end = min(raw_duration, unit_words[-1]["end_ms"] / 1000 + 0.10)
            source_duration = max(0.05, clip_end - clip_start)
            audible_capacity = max(0.25, scene_duration - NARRATION_LEAD_SECONDS - 0.20)
            target_duration = min(audible_capacity, source_duration / NARRATION_SLOW_FACTOR)
            tempo_factor = source_duration / target_duration
            if not 0.5 <= tempo_factor <= 2.0:
                raise LessonStudioValidationError(
                    f"旁白单元 {index + 1} 需要 {tempo_factor:.2f} 倍变速，超出安全范围。"
                )
            segment = temp_dir / f"segment-{index + 1:02d}.wav"
            audio_filter = (
                f"atrim=start={clip_start:.6f}:end={clip_end:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"atempo={tempo_factor:.8f},"
                f"adelay={round(NARRATION_LEAD_SECONDS * 1000)}:all=1,"
                f"apad=whole_dur={scene_duration:.6f},"
                f"atrim=duration={scene_duration:.6f},"
                "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo"
            )
            _run_ffmpeg(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(raw_audio), "-vn", "-af", audio_filter,
                    "-c:a", "pcm_s16le", str(segment),
                ],
                description=f"第 {index + 1} 段慢速旁白处理",
            )
            segment_paths.append(segment)
            for word in unit_words:
                start_ms = round(
                    (scene_start + NARRATION_LEAD_SECONDS
                     + (word["start_ms"] / 1000 - clip_start) / tempo_factor) * 1000
                )
                end_ms = round(
                    (scene_start + NARRATION_LEAD_SECONDS
                     + (word["end_ms"] / 1000 - clip_start) / tempo_factor) * 1000
                )
                remapped.append({
                    "text": word["text"],
                    "start_ms": max(round(scene_start * 1000), start_ms),
                    "end_ms": min(round(scene_end * 1000), max(start_ms + 1, end_ms)),
                })
            pacing.append({
                "unit_id": str(unit.get("id") or f"unit-{index + 1:02d}"),
                "source_clip_start_seconds": round(clip_start, 3),
                "source_clip_end_seconds": round(clip_end, 3),
                "tempo_factor": round(tempo_factor, 4),
                "timeline_start_seconds": scene_start,
                "timeline_end_seconds": scene_end,
            })

        temporary_output = output_audio.with_name(f".{output_audio.name}.{os.getpid()}.tmp.wav")
        filter_inputs: list[str] = []
        for segment in segment_paths:
            filter_inputs.extend(["-i", str(segment)])
        concat_inputs = "".join(f"[{index}:a]" for index in range(len(segment_paths)))
        _run_ffmpeg(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                *filter_inputs,
                "-filter_complex", f"{concat_inputs}concat=n={len(segment_paths)}:v=0:a=1[out]",
                "-map", "[out]", "-c:a", "pcm_s16le", str(temporary_output),
            ],
            description="旁白拼接",
        )
        os.replace(temporary_output, output_audio)
    return remapped, pacing


def _build_narration_timeline(
    *,
    source_text: str,
    source_sha256: str,
    units: list[dict[str, Any]],
    words: list[dict[str, Any]],
    audio_path: str,
) -> dict[str, Any]:
    char_ranges = _source_unit_ranges(source_text, units)
    word_ranges = _unit_word_ranges(source_text, units)
    timeline_units: list[dict[str, Any]] = []
    for index, (unit, char_range, word_range) in enumerate(zip(units, char_ranges, word_ranges)):
        scene_start_ms = round(float(unit.get("start_seconds", index * 5)) * 1000)
        scene_end_ms = round(float(unit.get("end_seconds", index * 5 + 5)) * 1000)
        start_char, end_char = char_range
        timeline_units.append({
            "id": str(unit.get("id") or f"unit-{index + 1:02d}"),
            "source_text": source_text[start_char:end_char],
            "source_start_char": start_char,
            "source_end_char": end_char,
            "audio_asset_id": "narration-final",
            "audio_path": audio_path,
            "actual_duration_ms": scene_end_ms - scene_start_ms,
            "words": words[word_range[0]:word_range[1]],
            "visual_beats": [
                {
                    "id": f"vb-{index + 1:02d}",
                    "start_ms": scene_start_ms,
                    "end_ms": scene_end_ms,
                    "visual_intent": "已确认的五秒连续生成镜头，与本旁白意群一一对应。",
                }
            ],
        })
    total_duration_ms = round(max(float(unit["end_seconds"]) for unit in units) * 1000)
    timeline = {
        "version": "1.0",
        "source_sha256": source_sha256,
        "total_duration_ms": total_duration_ms,
        "units": timeline_units,
    }
    validate_artifact("narration_timeline", timeline)
    validate_narration_timeline(source_text, timeline)
    return timeline


def _semantic_group_ranges(
    source_text: str, units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    words = source_text.split()
    unit_ranges = _unit_word_ranges(source_text, units)
    groups: list[dict[str, Any]] = []
    for unit_index, (start, end) in enumerate(unit_ranges):
        cursor = start
        while cursor < end:
            remaining = end - cursor
            if remaining <= CAPTION_MAX_WORDS:
                group_end = end
            else:
                upper = min(end, cursor + CAPTION_MAX_WORDS)
                candidates = [
                    position + 1
                    for position in range(cursor + CAPTION_MIN_WORDS - 1, upper)
                    if words[position].rstrip('"\'').endswith((",", ";", ":", ".", "!", "?"))
                ]
                group_end = candidates[-1] if candidates else upper
                tail = end - group_end
                if 0 < tail < CAPTION_MIN_WORDS:
                    group_end = max(cursor + CAPTION_MIN_WORDS, end - CAPTION_MIN_WORDS)
            groups.append({
                "id": f"cg-{len(groups) + 1:03d}",
                "unit_index": unit_index,
                "start_word_index": cursor,
                "end_word_index": group_end,
                "english": " ".join(words[cursor:group_end]),
            })
            cursor = group_end
    return groups


def _translation_prompt(groups: list[dict[str, Any]]) -> str:
    payload = [
        {"group_id": group["id"], "english": group["english"]}
        for group in groups
    ]
    return (
        "请把以下英语教学字幕意群翻译成自然、准确、适合学生阅读的简体中文。\n"
        "要求：逐组翻译，不合并、不遗漏；人名地名使用标准中文译名；中文按自然语序表达；"
        "不要使用双破折号；每组尽量不超过36个汉字。\n"
        "只返回 JSON：{\"translations\":[{\"group_id\":\"cg-001\",\"text\":\"...\"}],"
        "\"glossary\":{\"English proper noun\":\"标准中文译名\"}}。\n\n"
        f"字幕意群：{json.dumps(payload, ensure_ascii=False)}"
    )


def _wrap_translation(text: str) -> str:
    clean = re.sub(r"\s+", "", str(text)).replace("——", "，").strip()
    if not clean:
        raise LessonStudioValidationError("字幕翻译为空。")
    if len(clean) <= TRANSLATION_MAX_CHARS_PER_LINE:
        return clean
    if len(clean) > TRANSLATION_MAX_CHARS_PER_LINE * 2:
        raise LessonStudioValidationError(
            f"中文字幕超过两行容量：{clean}"
        )
    ideal = len(clean) // 2
    candidates = [
        index + 1 for index, char in enumerate(clean)
        if char in "，。；！？、" and 4 <= index + 1 <= TRANSLATION_MAX_CHARS_PER_LINE
    ]
    split_at = min(candidates, key=lambda value: abs(value - ideal)) if candidates else min(
        TRANSLATION_MAX_CHARS_PER_LINE, ideal
    )
    if len(clean) - split_at > TRANSLATION_MAX_CHARS_PER_LINE:
        split_at = len(clean) - TRANSLATION_MAX_CHARS_PER_LINE
    return clean[:split_at] + "\n" + clean[split_at:]


def _translate_caption_groups(
    project_dir: Path, groups: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    result = DashscopeText().execute({
        "model": TEXT_MODEL,
        "system_prompt": "你是严谨的英语教学字幕译者。只返回有效 JSON 对象。",
        "prompt": _translation_prompt(groups),
        "temperature": 0.15,
        "max_tokens": 4096,
        "output_path": str(project_dir / "artifacts" / "caption_translation_raw.json"),
    })
    if not result.success:
        raise LessonStudioProviderError(result.error or "中文字幕翻译失败。")
    raw = (result.data or {}).get("json")
    translations = raw.get("translations") if isinstance(raw, dict) else None
    if not isinstance(translations, list):
        raise LessonStudioProviderError("字幕翻译模型没有返回 translations 数组。")
    by_id = {
        str(item.get("group_id") or ""): str(item.get("text") or "")
        for item in translations if isinstance(item, dict)
    }
    expected_ids = [group["id"] for group in groups]
    if set(by_id) != set(expected_ids):
        raise LessonStudioProviderError("字幕翻译没有逐组完整覆盖英文意群。")
    translated = [
        {**group, "translation": _wrap_translation(by_id[group["id"]])}
        for group in groups
    ]
    glossary_raw = raw.get("glossary") if isinstance(raw, dict) else {}
    glossary = {
        str(source).strip(): str(target).strip()
        for source, target in (glossary_raw or {}).items()
        if str(source).strip() and str(target).strip()
    } if isinstance(glossary_raw, dict) else {}
    translated_text = "".join(item["translation"] for item in translated)
    missing_glossary = [target for target in glossary.values() if target not in translated_text]
    if missing_glossary:
        raise LessonStudioProviderError("字幕翻译未使用其返回的标准专名译法。")
    return translated, glossary


def _caption_artifacts(
    *,
    words: list[dict[str, Any]],
    translated_groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    captions = [
        {"word": word["text"], "startMs": word["start_ms"], "endMs": word["end_ms"]}
        for word in words
    ]
    caption_groups: list[dict[str, Any]] = []
    translations: list[dict[str, Any]] = []
    for group in translated_groups:
        start = int(group["start_word_index"])
        end = int(group["end_word_index"])
        item: dict[str, Any] = {
            "id": group["id"],
            "startMs": captions[start]["startMs"],
            "endMs": captions[end - 1]["endMs"],
            "startWordIndex": start,
            "endWordIndex": end,
            "translationText": group["translation"],
        }
        if end - start >= 8:
            item["lineBreakAfterWordIndices"] = [start + (end - start) // 2 - 1]
        caption_groups.append(item)
        translations.append({
            "text": group["translation"],
            "startMs": item["startMs"],
            "endMs": item["endMs"],
        })
    return captions, caption_groups, translations


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_bilingual_srt(
    path: Path,
    captions: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> None:
    blocks: list[str] = []
    for index, group in enumerate(groups, start=1):
        english = " ".join(
            caption["word"]
            for caption in captions[group["startWordIndex"]:group["endWordIndex"]]
        )
        blocks.append(
            f"{index}\n{_srt_timestamp(group['startMs'])} --> {_srt_timestamp(group['endMs'])}\n"
            f"{english}\n{group['translationText']}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _absolute_render_manifest(project_dir: Path) -> dict[str, Any]:
    manifest = _read_json(project_dir / "artifacts" / "asset_manifest.json")
    rendered = json.loads(json.dumps(manifest))
    for asset in rendered.get("assets", []):
        path = _project_asset_path(project_dir, str(asset.get("path") or ""))
        asset["path"] = str(path)
    return rendered


def _build_edit_decisions(
    *,
    project_dir: Path,
    plan: dict[str, Any],
    selected: list[dict[str, Any]],
    narration_path: Path,
    captions: list[dict[str, Any]],
    caption_groups: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    glossary: dict[str, str],
    source_sha256: str,
) -> dict[str, Any]:
    by_scene = {item["scene_id"]: item for item in selected}
    scenes = plan.get("scenes", [])
    cuts: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        start = float(scene["start_seconds"])
        end = float(scene["end_seconds"])
        cuts.append({
            "id": f"cut-{index + 1:03d}",
            "source": by_scene[str(scene["id"])]["asset_id"],
            "in_seconds": start,
            "out_seconds": end,
            "source_in_seconds": 0,
            "speed": 1,
            "layer": "primary",
            "transition_in": "cut" if index == 0 else "dissolve",
            "transition_out": "hold" if index == len(scenes) - 1 else "dissolve",
            "transition_duration": 0 if index == 0 else 0.18,
            "reason": "仅在切换到另一段已确认的生成视频时，按语义边界使用短叠化。",
        })
    duration = max(float(scene["end_seconds"]) for scene in scenes)
    decisions = {
        "version": "1.0",
        "cuts": cuts,
        "audio": {
            "narration": {
                "src": str(narration_path.resolve()),
                "volume": 1,
                "segments": [
                    {
                        "asset_id": "narration-final",
                        "start_seconds": float(scene["start_seconds"]),
                        "end_seconds": float(scene["end_seconds"]),
                    }
                    for scene in scenes
                ],
            },
            "sfx": [],
        },
        "captions": captions,
        "caption_groups": caption_groups,
        "translations": translations,
        "renderer_family": "explainer-teacher",
        "render_runtime": "remotion",
        "composition_mode": "templated",
        "metadata": {
            "target_duration_seconds": duration,
            "duration_tolerance_seconds": 0.15,
            "expected_resolution": "1920x1080",
            "expected_video_codec": "h264",
            "strict_review": True,
            "proposal_render_runtime": "remotion",
            "playbook": "esl-cinematic-editorial",
            "source_sha256": source_sha256,
            "caption_source": "canonical source aligned from DashScope ASR word timing",
            "translation_language": "zh-CN",
            "translation_glossary": glossary,
            "translation_max_chars_per_line": TRANSLATION_MAX_CHARS_PER_LINE,
            "translation_style": "natural Simplified Chinese; no double em dashes",
            "caption_grouping": "punctuation-aware meaning groups within each narrative unit",
            "subtitle_layout": "English highlighted primary line with Simplified Chinese secondary line",
            "music": "none",
            "fallback_used": False,
            "input_snapshot": "artifacts/compose_input_snapshot.json",
        },
    }
    validate_artifact("edit_decisions", decisions)
    return decisions


def _existing_completed_render(project_dir: Path) -> dict[str, Any] | None:
    state = read_studio_state(project_dir)
    output = str(state.get("output_path") or "")
    if state.get("stage") == "completed" and output:
        path = _project_asset_path(project_dir, output)
        if path.is_file():
            return {
                "output_path": output,
                "duration_seconds": state.get("duration_seconds"),
                "resumed": True,
            }
    return None


def compose_lesson_project(project_dir: Path) -> dict[str, Any]:
    """Compose an approved Lesson Studio project without mutating shot videos."""

    existing = _existing_completed_render(project_dir)
    if existing:
        return existing
    state = read_studio_state(project_dir)
    stage = str(state.get("stage") or "")
    if stage not in {
        "compose_ready", "compose_error", "composing_narration",
        "composing_captions", "composing_render",
    }:
        raise LessonStudioValidationError("当前项目尚未完成全部镜头视频确认。")

    try:
        plan = _read_json(project_dir / "artifacts" / "scene_plan.json")
        source = _read_json(project_dir / "artifacts" / "lesson_source.json")
        source_text = str(source.get("normalized_text") or "")
        source_sha256 = str(source.get("source_sha256") or "")
        units = plan.get("narrative_units") if isinstance(plan.get("narrative_units"), list) else []
        if not source_text or not source_sha256 or not units:
            raise LessonStudioValidationError("课文锁定文件或旁白单元缺失。")
        selected = _lock_compose_inputs(project_dir, plan)
        _update_studio_state(
            project_dir,
            stage="composing_narration",
            status="in_progress",
            message="已锁定四段原视频；正在生成慢速英语旁白和词级时间轴。",
            active_scene_id=None,
        )

        artifacts_dir = project_dir / "artifacts"
        audio_dir = project_dir / "assets" / "audio"
        timeline_path = artifacts_dir / "narration_timeline.json"
        narration_path = audio_dir / "narration.wav"
        qa_path = audio_dir / "raw-asr-qa.json"
        pacing_path = artifacts_dir / "narration_pacing.json"
        timeline = _read_json(timeline_path)
        if timeline and narration_path.is_file() and qa_path.is_file():
            validate_narration_timeline(source_text, timeline)
            aligned_words = [word for unit in timeline["units"] for word in unit["words"]]
        else:
            completed_asr = _latest_completed_asr(audio_dir, project_dir)
            if completed_asr:
                raw_audio, asr_output = completed_asr
                raw_words = DashscopeAsr._extract_words(_read_json(asr_output))
            else:
                raw_audio = _next_attempt_path(audio_dir, "narration-raw", ".wav")
                tts = DashscopeTTS().execute({
                    "model": VOICE_DESIGN_MODEL,
                    "voice_profile": VOICE_PROFILE,
                    "language_type": "English",
                    "text": source_text,
                    "output_path": str(raw_audio),
                })
                if not tts.success:
                    raise LessonStudioProviderError(tts.error or "慢速英语旁白生成失败。")
                audio_url = str((tts.data or {}).get("audio_url") or "")
                if not audio_url:
                    raise LessonStudioProviderError("TTS 成功但没有返回供 ASR 使用的临时音频地址。")
                asr_output = _next_attempt_path(audio_dir, "narration-asr", ".json")
                task_state = project_dir / "state" / f"{asr_output.stem}-task.json"
                asr = DashscopeAsr().execute({
                    "model": ASR_MODEL,
                    "audio_url": audio_url,
                    "language": "en",
                    "enable_words": True,
                    "enable_itn": False,
                    "output_path": str(asr_output),
                    "task_state_path": str(task_state),
                })
                if not asr.success:
                    raise LessonStudioProviderError(asr.error or "旁白词级时间轴识别失败。")
                raw_words = (asr.data or {}).get("words") or []
            raw_words = _normalize_asr_word_durations(raw_words)
            canonical_words = align_asr_words(source_text, raw_words)
            qa_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(qa_path, build_raw_qa_transcript(raw_words))
            aligned_words, pacing = _build_slow_narration(
                raw_audio=raw_audio,
                output_audio=narration_path,
                aligned_words=canonical_words,
                source_text=source_text,
                units=units,
            )
            _atomic_write_json(pacing_path, {"version": "1.0", "units": pacing})
            timeline = _build_narration_timeline(
                source_text=source_text,
                source_sha256=source_sha256,
                units=units,
                words=aligned_words,
                audio_path=narration_path.relative_to(project_dir).as_posix(),
            )
            _atomic_write_json(timeline_path, timeline)

        _update_studio_state(
            project_dir,
            stage="composing_captions",
            status="in_progress",
            message="慢速旁白已完成；正在按意群生成中英文字幕。",
        )
        caption_plan_path = artifacts_dir / "caption_plan.json"
        caption_plan = _read_json(caption_plan_path)
        if caption_plan:
            translated_groups = caption_plan.get("groups") or []
            glossary = caption_plan.get("glossary") or {}
        else:
            groups = _semantic_group_ranges(source_text, units)
            translated_groups, glossary = _translate_caption_groups(project_dir, groups)
            caption_plan = {
                "version": "1.0",
                "strategy": "punctuation-aware semantic groups constrained to narrative-unit boundaries",
                "groups": translated_groups,
                "glossary": glossary,
            }
            _atomic_write_json(caption_plan_path, caption_plan)
        captions, caption_groups, translations = _caption_artifacts(
            words=aligned_words,
            translated_groups=translated_groups,
        )
        srt_path = project_dir / "assets" / "subtitles" / "bilingual.srt"
        _write_bilingual_srt(srt_path, captions, caption_groups)

        decisions = _build_edit_decisions(
            project_dir=project_dir,
            plan=plan,
            selected=selected,
            narration_path=narration_path,
            captions=captions,
            caption_groups=caption_groups,
            translations=translations,
            glossary=glossary,
            source_sha256=source_sha256,
        )
        decisions_path = artifacts_dir / "edit_decisions.json"
        _atomic_write_json(decisions_path, decisions)
        _update_studio_state(
            project_dir,
            stage="composing_render",
            status="in_progress",
            message="旁白和双语字幕已完成；Remotion 正在合成最终视频。",
        )

        renders_dir = project_dir / "renders"
        renders_dir.mkdir(parents=True, exist_ok=True)
        temporary_render = renders_dir / ".english-lesson-final.rendering.mp4"
        final_render = renders_dir / "english-lesson-final.mp4"
        render_result = VideoCompose().execute({
            "operation": "render",
            "edit_decisions": decisions,
            "asset_manifest": _absolute_render_manifest(project_dir),
            "scene_plan": plan.get("scenes", []),
            "output_path": str(temporary_render),
            "narration_transcript_path": str(qa_path),
            "script_text": source_text,
            "remotion_timeout_ms": 120_000,
        })
        if not render_result.success:
            if temporary_render.is_file():
                draft = renders_dir / "english-lesson-draft.mp4"
                if not draft.exists():
                    os.replace(temporary_render, draft)
            if (render_result.data or {}).get("final_review"):
                _atomic_write_json(
                    artifacts_dir / "final_review.json",
                    (render_result.data or {})["final_review"],
                )
            raise LessonStudioProviderError(render_result.error or "Remotion 合成失败。")
        os.replace(temporary_render, final_render)
        final_review = (render_result.data or {}).get("final_review") or {}
        _atomic_write_json(artifacts_dir / "final_review.json", final_review)
        duration = _probe_media(final_render)["duration_seconds"]
        report = {
            "version": "1.0",
            "status": "pass",
            "runtime": "remotion",
            "renderer_family": "explainer-teacher",
            "output_path": final_render.relative_to(project_dir).as_posix(),
            "duration_seconds": duration,
            "source_video_hashes": {
                item["scene_id"]: item["sha256"] for item in selected
            },
            "narration_model": VOICE_DESIGN_MODEL,
            "asr_model": ASR_MODEL,
            "text_model": TEXT_MODEL,
            "music": "none",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write_json(artifacts_dir / "render_report.json", report)
        rel_final = final_render.relative_to(project_dir).as_posix()
        _append_lesson_asset(project_dir, {
            "id": "final-render-v1",
            "type": "video",
            "path": rel_final,
            "source_tool": "video_compose",
            "scene_id": "final",
            "model": "Remotion Explainer",
            "cost_usd": 0,
            "duration_seconds": duration,
            "resolution": "1920x1080",
            "format": "mp4",
            "subtype": "final-bilingual-lesson",
            "generation_summary": "四段已确认视频、慢速英语旁白和双语意群字幕的 Remotion 合成。",
            "provider": "local",
            "license": "Derived from project-owned generated assets",
        })
        _update_studio_state(
            project_dir,
            stage="completed",
            status="completed",
            message="旁白、双语字幕与 Remotion 合成已完成。",
            output_path=rel_final,
            duration_seconds=duration,
            compose_completed_at=report["completed_at"],
        )
        return {"output_path": rel_final, "duration_seconds": duration, "resumed": False}
    except (LessonStudioValidationError, LessonStudioProviderError) as exc:
        _update_studio_state(
            project_dir,
            stage="compose_error",
            status="error",
            message=f"合成已停止，原镜头视频仍安全保留。{exc}",
            active_scene_id=None,
        )
        raise
    except Exception as exc:
        _update_studio_state(
            project_dir,
            stage="compose_error",
            status="error",
            message="合成遇到未预期错误，原镜头视频仍安全保留。",
            active_scene_id=None,
        )
        raise LessonStudioProviderError(f"合成失败：{exc}") from exc
