"""Contracts for compiling provider-neutral lesson motion plans."""

from __future__ import annotations

import pytest

from lib.shot_prompt_builder import build_video_prompt


def test_wan_prompt_compiler_focuses_on_motion_and_separates_negatives():
    scene = {
        "id": "scene-transport",
        "video_prompt_spec": {
            "single_shot": True,
            "subject_motion": "A period locomotive advances steadily on the existing rail.",
            "camera_motion": "A low lateral track right with a gentle push forward.",
            "temporal_beats": [
                {
                    "start_seconds": 0,
                    "end_seconds": 3,
                    "action": "The locomotive begins far away while road dust drifts across the foreground.",
                },
                {
                    "start_seconds": 3,
                    "end_seconds": 7,
                    "action": "Sleepers sweep past faster than the middle-ground trees.",
                },
                {
                    "start_seconds": 7,
                    "end_seconds": 10,
                    "action": "A brief steam release catches the backlight and the camera settles.",
                },
            ],
            "foreground_event": "Dry grass crosses the lower side edge to strengthen parallax.",
            "visual_payoff": "The locomotive reaches a stable medium-close framing.",
            "continuity_refs": ["entity-locomotive", "location-corridor", "period-1901"],
            "caption_safe_area": "Keep the lower 30 percent quiet but naturally textured.",
            "negative_constraints": [
                "text or subtitles",
                "extra railway tracks",
                "warped locomotive geometry",
                "hard cuts",
                "camera shake",
            ],
        },
    }
    continuity_bible = {
        "entities": [
            {
                "id": "entity-locomotive",
                "canonical_name": "period steam locomotive",
                "immutable_traits": ["stable wheel geometry", "dark red body"],
            }
        ],
        "locations": [
            {
                "id": "location-corridor",
                "canonical_name": "road-and-rail corridor",
                "immutable_traits": ["one railway track", "adjacent rough road"],
            }
        ],
        "period": {
            "id": "period-1901",
            "label": "circa 1901",
            "immutable_traits": ["no modern infrastructure"],
        },
    }

    compiled = build_video_prompt(scene, continuity_bible, provider="wan-i2v")

    assert compiled["prompt"].startswith("Generate a single continuous shot.")
    assert "[0-3s]" in compiled["prompt"]
    assert "[7-10s]" in compiled["prompt"]
    assert "stable wheel geometry" in compiled["prompt"]
    assert "text or subtitles" not in compiled["prompt"]
    assert "text or subtitles" in compiled["negative_prompt"]
    assert len(compiled["prompt"]) <= 1500
    assert len(compiled["negative_prompt"]) <= 500


def test_prompt_compiler_is_content_agnostic():
    scene = {
        "video_prompt_spec": {
            "single_shot": True,
            "subject_motion": "Water rises through the plant stem.",
            "camera_motion": "Macro tracking move upward.",
            "temporal_beats": [
                {"start_seconds": 0, "end_seconds": 5, "action": "Droplets enter the roots."},
                {"start_seconds": 5, "end_seconds": 10, "action": "The flow reaches the leaves."},
            ],
            "continuity_refs": [],
            "negative_constraints": ["labels", "written words"],
        }
    }

    compiled = build_video_prompt(scene, {}, provider="wan-i2v")

    assert "locomotive" not in compiled["prompt"].lower()
    assert "Mombasa" not in compiled["prompt"]
    assert "Water rises through the plant stem" in compiled["prompt"]


def test_prompt_compiler_rejects_an_unknown_continuity_reference():
    scene = {
        "video_prompt_spec": {
            "single_shot": True,
            "subject_motion": "The subject crosses the room.",
            "camera_motion": "The camera tracks alongside.",
            "temporal_beats": [
                {"start_seconds": 0, "end_seconds": 5, "action": "The action unfolds."}
            ],
            "continuity_refs": ["missing-entity"],
            "negative_constraints": [],
        }
    }

    with pytest.raises(ValueError, match="missing-entity"):
        build_video_prompt(scene, {}, provider="wan-i2v")
