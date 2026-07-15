"""Regression contracts for the Phase 1 Remotion lesson render path."""

from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import wave

from schemas.artifacts import validate_artifact
from tools.base_tool import ToolResult
from tools.video.video_compose import VideoCompose


def _edit_decisions() -> dict:
    return {
        "version": "1.0",
        "cuts": [
            {
                "id": "cut-001",
                "source": "lesson-video",
                "in_seconds": 0,
                "out_seconds": 10,
            }
        ],
        "captions": [
            {"word": "Before", "startMs": 0, "endMs": 350},
            {"word": "then,", "startMs": 360, "endMs": 700},
        ],
        "audio": {
            "narration": {
                "src": "projects/test/assets/audio/narration.wav",
                "volume": 1.0,
            }
        },
        "renderer_family": "explainer-teacher",
        "render_runtime": "remotion",
        "composition_mode": "templated",
        "metadata": {
            "target_duration_seconds": 10,
            "duration_tolerance_seconds": 0.1,
        },
    }


def test_edit_decisions_schema_accepts_native_remotion_captions_and_narration():
    validate_artifact("edit_decisions", _edit_decisions())


def test_edit_decisions_schema_accepts_bilingual_captions_and_virtual_camera():
    decisions = _edit_decisions()
    decisions["cuts"] = [
        {
            "id": "cut-continuous",
            "source": "lesson-video",
            "in_seconds": 0,
            "out_seconds": 10,
            "source_in_seconds": 0,
            "transform": {
                "keyframes": [
                    {"at_seconds": 0, "scale": 1.0, "position": {"x": 50, "y": 50}},
                    {"at_seconds": 3.2, "scale": 1.04, "position": {"x": 50, "y": 48}},
                    {"at_seconds": 6.6, "scale": 1.1, "position": {"x": 50, "y": 46}},
                    {"at_seconds": 10, "scale": 1.15, "position": {"x": 50, "y": 44}},
                ],
            },
        },
    ]
    decisions["translations"] = [
        {
            "text": "在那之前，蒙巴萨与内罗毕之间的交通联系",
            "startMs": 223,
            "endMs": 4752,
        },
        {
            "text": "只有崎岖的公路和一条于1901年建成的老铁路。",
            "startMs": 4752,
            "endMs": 9876,
        },
    ]

    validate_artifact("edit_decisions", decisions)


def test_remotion_explainer_wires_translations_and_virtual_camera():
    source = (
        Path(__file__).resolve().parent.parent.parent
        / "remotion-composer"
        / "src"
        / "Explainer.tsx"
    ).read_text(encoding="utf-8")

    assert "translations={translations}" in source
    assert "groups={captionGroups}" in source
    assert "transform={cut.transform}" in source
    assert "transform?.keyframes" in source


def test_edit_decisions_accepts_semantic_caption_groups():
    decisions = _edit_decisions()
    decisions["caption_groups"] = [
        {"id": "cg-001", "startMs": 0, "endMs": 500},
        {"id": "cg-002", "startMs": 500, "endMs": 1100},
    ]

    validate_artifact("edit_decisions", decisions)


def test_bilingual_caption_guard_catches_factuality_segmentation_and_load():
    decisions = _edit_decisions()
    decisions["captions"] = [
        {"word": "Mombasa,", "startMs": 0, "endMs": 500},
        {"word": "completed", "startMs": 500, "endMs": 800},
        {"word": "in", "startMs": 800, "endMs": 900},
        {"word": "1901.", "startMs": 900, "endMs": 1100},
    ]
    decisions["caption_groups"] = [
        {"id": "cg-001", "startMs": 0, "endMs": 500},
        {"id": "cg-002", "startMs": 500, "endMs": 1100},
    ]
    decisions["translations"] = [
        {"text": "蒙巴萨（肯尼亚主要港口）", "startMs": 0, "endMs": 500},
        {"text": "这条铁路于1901年建成", "startMs": 500, "endMs": 1100},
    ]
    decisions["metadata"]["translation_glossary"] = {
        "Mombasa": "蒙巴萨",
        "Kenya": "肯尼亚",
    }
    decisions["metadata"]["translation_max_chars_per_line"] = 20

    assert VideoCompose._bilingual_caption_issues(decisions) == []

    bad = deepcopy(decisions)
    bad["translations"][0]["text"] = "巴萨——肯尼亚主要港口"
    bad["translations"][1]["text"] = "这是一条明显超过二十个字且会造成底部视觉过载的中文字幕"
    bad["caption_groups"] = [
        {"id": "cg-001", "startMs": 0, "endMs": 800},
        {"id": "cg-002", "startMs": 800, "endMs": 1100},
    ]

    issues = VideoCompose._bilingual_caption_issues(bad)
    assert any("Mombasa" in issue and "蒙巴萨" in issue for issue in issues)
    assert any("破折号" in issue for issue in issues)
    assert any("20" in issue for issue in issues)
    assert any("in 1901" in issue for issue in issues)


def test_bilingual_caption_layout_is_compact_and_hierarchical():
    source = (
        Path(__file__).resolve().parent.parent.parent
        / "remotion-composer"
        / "src"
        / "components"
        / "CaptionOverlay.tsx"
    ).read_text(encoding="utf-8")

    assert 'maxWidth: "68%"' in source
    assert "translationFontSize = 26" in source
    assert 'letterSpacing: "0.01em"' in source
    assert "premountFor={fps}" in source


def test_caption_overlay_uses_word_indexed_pages_and_planned_line_breaks():
    source = (
        Path(__file__).resolve().parent.parent.parent
        / "remotion-composer"
        / "src"
        / "components"
        / "CaptionOverlay.tsx"
    ).read_text(encoding="utf-8")

    assert "startWordIndex" in source
    assert "endWordIndex" in source
    assert "lineBreakAfterWordIndices" in source
    assert "<br" in source


def test_native_caption_array_counts_as_burned_in_subtitles():
    assert VideoCompose._has_burned_in_captions(_edit_decisions()) is True


def test_esl_playbook_uses_warm_accent_for_active_caption_word():
    theme = VideoCompose._build_theme_from_playbook(
        "esl-cinematic-editorial", _edit_decisions()
    )

    assert theme["captionHighlightColor"] == "#E8B44F"
    assert theme["captionHighlightColor"] != theme["primaryColor"]


def test_exact_target_duration_is_passed_as_remotion_frame_range(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/npx")
    tool = VideoCompose()
    seen: dict = {}

    def fake_run_command(cmd, *args, **kwargs):
        seen["cmd"] = cmd

    monkeypatch.setattr(tool, "run_command", fake_run_command)
    tool._remotion_render(
        {
            "composition_data": _edit_decisions(),
            "output_path": str(tmp_path / "lesson.mp4"),
        }
    )

    assert "--frames=0-299" in seen["cmd"]


def test_existing_project_audio_is_staged_in_remotion_public_dir(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/npx")
    monkeypatch.chdir(tmp_path)
    narration = tmp_path / "projects" / "test" / "narration.wav"
    narration.parent.mkdir(parents=True)
    narration.write_bytes(b"RIFF")
    decisions = _edit_decisions()
    decisions["audio"]["narration"]["src"] = "projects/test/narration.wav"
    seen: dict = {}
    tool = VideoCompose()

    def fake_run_command(cmd, *args, **kwargs):
        props_arg = next(item for item in cmd if item.startswith("--props="))
        props_path = Path(props_arg.split("=", 1)[1])
        seen.update(json.loads(props_path.read_text(encoding="utf-8")))
        public_arg = next(item for item in cmd if item.startswith("--public-dir="))
        public_dir = Path(public_arg.split("=", 1)[1])
        staged_src = seen["audio"]["narration"]["src"]
        seen["staged_audio_exists"] = (public_dir / staged_src).exists()
        seen["staged_audio_bytes"] = (public_dir / staged_src).read_bytes()

    monkeypatch.setattr(tool, "run_command", fake_run_command)
    tool._remotion_render(
        {
            "composition_data": decisions,
            "output_path": str(tmp_path / "lesson.mp4"),
        }
    )

    assert seen["audio"]["narration"]["src"].startswith("asset_")
    assert seen["staged_audio_exists"] is True
    assert seen["staged_audio_bytes"] == b"RIFF"


def test_explainer_preserves_posix_leading_slash_when_stripping_file_uri():
    source = (
        Path(__file__).resolve().parent.parent.parent
        / "remotion-composer"
        / "src"
        / "Explainer.tsx"
    ).read_text(encoding="utf-8")

    assert 'src.replace(/^file:\\/\\//, "")' in source
    assert 'src.replace(/^file:\\/\\/\\/?/, "")' not in source


def test_final_review_revises_for_duration_drift_and_partial_native_captions(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "lesson.mp4"
    output_path.write_bytes(b"fake-video")
    decisions = _edit_decisions()
    decisions["captions"] = [{"word": "Before", "startMs": 0, "endMs": 1}]
    source_text = (
        "Before then, the only transport links between Mombasa, Kenya's main "
        "port, and Nairobi, Kenya's capital, were rough roads and an old "
        "railway line completed in 1901."
    )

    class FakeProcess:
        def __init__(self, *, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffprobe" and "-select_streams" not in cmd:
            return FakeProcess(
                stdout=json.dumps(
                    {
                        "format": {"duration": "12.0", "size": "1000"},
                        "streams": [
                            {
                                "codec_type": "video",
                                "width": 1920,
                                "height": 1080,
                                "r_frame_rate": "30/1",
                                "codec_name": "h264",
                            },
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                    }
                )
            )
        if cmd[0] == "ffprobe":
            return FakeProcess(stdout=json.dumps({"streams": []}))
        if "-frames:v" in cmd:
            Path(cmd[-1]).write_bytes(b"x" * 3000)
            return FakeProcess()
        return FakeProcess(
            stderr="mean_volume: -20.0 dB\nmax_volume: -3.0 dB\n"
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    review = VideoCompose()._run_final_review(
        output_path,
        decisions,
        script_text=source_text,
    )

    assert review["status"] == "revise"
    assert review["checks"]["subtitle_check"]["coverage_ratio"] < 0.1
    assert any("Duration drift" in issue for issue in review["issues_found"])
    assert any("Caption coverage" in issue for issue in review["issues_found"])


def test_strict_final_review_passes_exact_delivery_contract(tmp_path, monkeypatch):
    output_path = tmp_path / "lesson.mp4"
    output_path.write_bytes(b"fake-video")
    source_text = (
        "Before then, the only transport links between Mombasa, Kenya's main "
        "port, and Nairobi, Kenya's capital, were rough roads and an old "
        "railway line completed in 1901."
    )
    tokens = source_text.split()
    decisions = _edit_decisions()
    decisions["captions"] = [
        {
            "word": token,
            "startMs": round(index * 9_000 / len(tokens)),
            "endMs": round((index + 1) * 9_000 / len(tokens)),
        }
        for index, token in enumerate(tokens)
    ]
    decisions["metadata"].update(
        {
            "expected_resolution": "1920x1080",
            "expected_video_codec": "h264",
            "proposal_render_runtime": "remotion",
            "strict_review": True,
        }
    )
    transcript_path = tmp_path / "raw-asr-qa.json"
    transcript_path.write_text(
        json.dumps(
            {
                "word_timestamps": [
                    {"word": token, "start": index * 0.3, "end": (index + 1) * 0.3}
                    for index, token in enumerate(tokens)
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeProcess:
        def __init__(self, *, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffprobe" and "-select_streams" not in cmd:
            return FakeProcess(
                stdout=json.dumps(
                    {
                        "format": {"duration": "10.0", "size": "1000"},
                        "streams": [
                            {
                                "codec_type": "video",
                                "width": 1920,
                                "height": 1080,
                                "r_frame_rate": "30/1",
                                "codec_name": "h264",
                            },
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                    }
                )
            )
        if cmd[0] == "ffprobe":
            return FakeProcess(stdout=json.dumps({"streams": []}))
        if "-frames:v" in cmd:
            Path(cmd[-1]).write_bytes(b"x" * 3_000)
            return FakeProcess()
        if "-af" in cmd:
            if "-vn" not in cmd:
                return FakeProcess(stderr="Encoder not found", returncode=1)
            return FakeProcess(stderr="No such filter: 'volumedetect'", returncode=1)
        if "wav" in cmd:
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(8_000)
                writer.writeframes(b"\x10\x27" * 8_000)
            return FakeProcess(stdout=wav_buffer.getvalue())
        return FakeProcess(
            stderr="mean_volume: -20.0 dB\nmax_volume: -3.0 dB\n"
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    review = VideoCompose()._run_final_review(
        output_path,
        decisions,
        narration_transcript_path=transcript_path,
        script_text=source_text,
    )

    assert review["status"] == "pass"
    assert review["issues_found"] == []
    assert review["checks"]["subtitle_check"]["coverage_ratio"] == 1.0
    assert review["checks"]["transcript_comparison"]["word_accuracy"] == 1.0
    assert review["checks"]["audio_spotcheck"]["narration_present"] is True
    assert review["checks"]["audio_spotcheck"]["music_present"] is False


def test_high_level_render_blocks_revise_review(tmp_path, monkeypatch):
    output_path = tmp_path / "lesson.mp4"
    source_path = tmp_path / "motion.mp4"
    source_path.write_bytes(b"motion")
    decisions = _edit_decisions()
    asset_manifest = {
        "assets": [{"id": "lesson-video", "path": str(source_path)}]
    }
    tool = VideoCompose()
    monkeypatch.setattr(tool, "_pre_compose_validation", lambda *args: None)
    monkeypatch.setattr(tool, "_needs_remotion", lambda cuts: True)

    def fake_render(inputs):
        output_path.write_bytes(b"rendered")
        return ToolResult(success=True, data={"output": str(output_path)})

    monkeypatch.setattr(tool, "_remotion_render", fake_render)
    monkeypatch.setattr(
        tool,
        "_run_final_review",
        lambda *args, **kwargs: {
            "status": "revise",
            "issues_found": ["Caption coverage incomplete"],
        },
    )

    result = tool._render(
        {
            "edit_decisions": decisions,
            "asset_manifest": asset_manifest,
            "output_path": str(output_path),
        }
    )

    assert result.success is False
    assert "status=revise" in result.error
