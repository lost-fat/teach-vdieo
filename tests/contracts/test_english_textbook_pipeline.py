"""Contracts for the source-faithful English textbook pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lib.checkpoint import CheckpointValidationError, validate_checkpoint
from lib.pipeline_loader import get_required_tools, get_stage_order, list_pipelines, load_pipeline
from schemas.artifacts import ARTIFACT_NAMES, validate_artifact


ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "english_textbook_phase1.json").read_text(
        encoding="utf-8"
    )
)
SOURCE = FIXTURE["source_text"]
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()


def _lesson_source() -> dict:
    return {
        "version": "1.0",
        "language": "en",
        "source_text": SOURCE,
        "normalized_text": SOURCE,
        "source_sha256": SOURCE_HASH,
        "adaptation_mode": "verbatim",
        "normalizations_applied": [],
    }


def _lesson_plan() -> dict:
    return {
        "version": "1.0",
        "source_sha256": SOURCE_HASH,
        "audience": {"level": "A2-B1", "description": "English learners"},
        "target_duration_seconds": 10,
        "caption": {"language": "en", "mode": "word_highlight"},
        "voice": {
            "profile": "english_teacher_female",
            "model": "qwen3-tts-vd-2026-01-26",
            "language_type": "English",
        },
        "visual": {
            "style_playbook": "esl-cinematic-editorial",
            "image_model": "flux2-klein-base-4b",
            "video_model": "wan2.6-i2v-flash",
        },
        "render": {
            "runtime": "remotion",
            "composition_mode": "templated",
            "resolution": "1920x1080",
            "fps": 30,
        },
        "music": {"source": "none", "reason": "Phase 1 narration timing review"},
        "quota_policy": {"free_tier_only": True, "paid_spend_cap": 0},
    }


def _narration_timeline() -> dict:
    tokens = SOURCE.split()
    word_step_ms = 9_100 / len(tokens)
    return {
        "version": "1.0",
        "source_sha256": SOURCE_HASH,
        "total_duration_ms": 10_000,
        "units": [
            {
                "id": "nu-001",
                "source_text": SOURCE,
                "source_start_char": 0,
                "source_end_char": len(SOURCE),
                "audio_asset_id": "narration-nu-001",
                "audio_path": "projects/test/assets/audio/narration.wav",
                "actual_duration_ms": 9_100,
                "words": [
                    {
                        "text": token,
                        "start_ms": round(index * word_step_ms),
                        "end_ms": round((index + 1) * word_step_ms),
                    }
                    for index, token in enumerate(tokens)
                ],
                "visual_beats": [
                    {
                        "id": "vb-001",
                        "start_ms": 0,
                        "end_ms": 4_500,
                        "visual_intent": "Mombasa port and road network.",
                    },
                    {
                        "id": "vb-002",
                        "start_ms": 4_500,
                        "end_ms": 9_100,
                        "visual_intent": "Historic railway toward Nairobi.",
                    },
                ],
            }
        ],
    }


def test_manifest_loads_and_is_listed():
    manifest = load_pipeline("english-textbook")
    assert manifest["name"] == "english-textbook"
    assert manifest["category"] == "custom"
    assert "english-textbook" in list_pipelines()


def test_manifest_has_audio_first_stage_order():
    manifest = load_pipeline("english-textbook")
    assert get_stage_order(manifest) == [
        "ingest",
        "idea",
        "script",
        "narration",
        "scene_plan",
        "assets",
        "edit",
        "compose",
        "publish",
    ]


def test_manifest_locks_confirmed_mixed_provider_api_contract():
    api = load_pipeline("english-textbook")["metadata"]["api_contract"]
    assert api == {
        "provider": "mixed",
        "region": "cn-beijing",
        "text_model": "qwen3.7-plus",
        "image_provider": "openai_compatible_local",
        "image_model": "flux2-klein-base-4b",
        "image_size": "1024x1024",
        "image_quality": "medium",
        "video_model": "wan2.6-i2v-flash",
        "tts_model": "qwen3-tts-vd-2026-01-26",
        "asr_model": "qwen3-asr-flash-filetrans",
        "free_tier_only": True,
        "paid_spend_cap": 0,
        "verification_duration_seconds": 10,
    }


def test_manifest_exposes_all_required_tools():
    tools = get_required_tools(load_pipeline("english-textbook"))
    assert {
        "dashscope_text",
        "dashscope_tts",
        "dashscope_asr",
        "local_openai_image",
        "dashscope_video",
        "subtitle_gen",
        "video_compose",
        "audio_mixer",
    }.issubset(tools)


def test_new_artifacts_are_registered_and_validate():
    expected = {"lesson_source", "lesson_plan", "narration_timeline"}
    assert expected.issubset(set(ARTIFACT_NAMES))

    validate_artifact("lesson_source", _lesson_source())
    validate_artifact("lesson_plan", _lesson_plan())
    validate_artifact("narration_timeline", _narration_timeline())


def test_decision_log_accepts_full_run_preapproval_policy():
    validate_artifact(
        "decision_log",
        {
            "version": "1.0",
            "project_id": "english-textbook-phase1",
            "decisions": [
                {
                    "decision_id": "d-approval-001",
                    "stage": "idea",
                    "category": "approval_policy",
                    "subject": "Human approval policy for the 10-second validation run",
                    "options_considered": [
                        {
                            "option_id": "full-run-preauthorized",
                            "label": "Full run preauthorized",
                            "score": 1.0,
                            "reason": "The user explicitly preauthorized the complete validation run.",
                        }
                    ],
                    "selected": "full-run-preauthorized",
                    "reason": "Record the user's explicit authorization so later gates remain auditable.",
                    "user_visible": True,
                    "user_approved": True,
                    "confidence": 1.0,
                }
            ],
        },
    )


@pytest.mark.parametrize(
    ("artifact_name", "artifact"),
    [
        ("lesson_source", _lesson_source()),
        ("lesson_plan", _lesson_plan()),
        ("narration_timeline", _narration_timeline()),
    ],
)
def test_artifact_schemas_reject_unknown_fields(artifact_name, artifact):
    artifact["secret_or_unknown"] = "must not pass through"
    with pytest.raises(Exception):
        validate_artifact(artifact_name, artifact)


def _checkpoint(stage: str, artifacts: dict) -> dict:
    return {
        "version": "1.0",
        "project_id": "english-textbook-phase1",
        "pipeline_type": "english-textbook",
        "stage": stage,
        "status": "completed",
        "timestamp": "2026-07-14T00:00:00Z",
        "artifacts": artifacts,
        "human_approved": True,
    }


def test_ingest_checkpoint_requires_lesson_source():
    with pytest.raises(CheckpointValidationError, match="lesson_source"):
        validate_checkpoint(_checkpoint("ingest", {}))
    validate_checkpoint(
        _checkpoint("ingest", {"lesson_source": _lesson_source()})
    )


def test_narration_checkpoint_requires_timeline():
    with pytest.raises(CheckpointValidationError, match="narration_timeline"):
        validate_checkpoint(_checkpoint("narration", {}))
    validate_checkpoint(
        _checkpoint(
            "narration",
            {
                "lesson_source": _lesson_source(),
                "narration_timeline": _narration_timeline(),
            },
        )
    )


def test_narration_checkpoint_rejects_timeline_for_rewritten_source():
    rewritten = "A completely different narration."
    rewritten_hash = hashlib.sha256(rewritten.encode("utf-8")).hexdigest()
    timeline = {
        "version": "1.0",
        "source_sha256": rewritten_hash,
        "total_duration_ms": 1_000,
        "units": [
            {
                "id": "nu-rewritten",
                "source_text": rewritten,
                "source_start_char": 0,
                "source_end_char": len(rewritten),
                "audio_asset_id": "rewritten-audio",
                "audio_path": "rewritten.wav",
                "actual_duration_ms": 1_000,
                "words": [
                    {"text": word, "start_ms": index * 250, "end_ms": (index + 1) * 250}
                    for index, word in enumerate(rewritten.split())
                ],
                "visual_beats": [
                    {
                        "id": "vb-rewritten",
                        "start_ms": 0,
                        "end_ms": 1_000,
                        "visual_intent": "This must not replace the locked source.",
                    }
                ],
            }
        ],
    }

    with pytest.raises(CheckpointValidationError, match="lesson_source"):
        validate_checkpoint(
            _checkpoint(
                "narration",
                {
                    "lesson_source": _lesson_source(),
                    "narration_timeline": timeline,
                },
            )
        )


def test_director_skills_exist_and_have_operational_sections():
    skills = load_pipeline("english-textbook")["required_skills"]
    for skill in skills:
        if skill.startswith("meta/"):
            continue
        path = ROOT / "skills" / f"{skill}.md"
        assert path.exists(), f"missing director skill: {path}"
        content = path.read_text(encoding="utf-8")
        assert "When to Use" in content
        assert "Process" in content
        assert "Self-Evaluate" in content


def test_pipeline_references_registered_artifacts_only():
    manifest = load_pipeline("english-textbook")
    referenced = set()
    for stage in manifest["stages"]:
        referenced.update(stage.get("produces", []))
        referenced.update(stage.get("required_artifacts_in", []))
        referenced.update(stage.get("optional_artifacts_in", []))
    assert referenced.issubset(set(ARTIFACT_NAMES))
