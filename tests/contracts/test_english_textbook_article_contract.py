"""Generalized article-mode contracts for the English textbook pipeline."""

from __future__ import annotations

from schemas.artifacts import validate_artifact


def test_scene_plan_accepts_multiple_narrative_units_and_continuity_bible():
    plan = {
        "version": "1.0",
        "style_playbook": "esl-cinematic-editorial",
        "narrative_units": [
            {
                "id": "unit-001",
                "source_text": "Rain fell for several days.",
                "start_seconds": 0,
                "end_seconds": 3.2,
                "discourse_role": "setting",
            },
            {
                "id": "unit-002",
                "source_text": "As a result, the river rose quickly.",
                "start_seconds": 3.2,
                "end_seconds": 7.4,
                "discourse_role": "cause_effect",
            },
        ],
        "continuity_bible": {
            "entities": [
                {
                    "id": "river",
                    "canonical_name": "the river",
                    "translations": {"zh-CN": "河流"},
                    "immutable_traits": ["muddy water", "tree-lined banks"],
                }
            ],
            "locations": [],
            "period": None,
            "style": {
                "palette": ["earth brown", "rain blue"],
                "lighting": "overcast natural light",
                "texture": "mature editorial documentary",
            },
            "camera_rules": ["natural eye-level movement"],
            "prohibited_elements": ["readable generated text"],
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "generated",
                "description": "One continuous river scene covering setting and consequence.",
                "start_seconds": 0,
                "end_seconds": 7.4,
                "narrative_unit_ids": ["unit-001", "unit-002"],
                "visual_role": "cause_effect",
                "video_prompt_spec": {
                    "single_shot": True,
                    "subject_motion": "Rain agitates the river as the water level rises.",
                    "camera_motion": "A slow lateral track along the bank.",
                    "temporal_beats": [
                        {
                            "start_seconds": 0,
                            "end_seconds": 3.2,
                            "action": "Rain strikes the initially low river surface.",
                        },
                        {
                            "start_seconds": 3.2,
                            "end_seconds": 7.4,
                            "action": "The current accelerates and reaches higher on the bank.",
                        },
                    ],
                    "continuity_refs": ["river"],
                    "caption_safe_area": "Keep the lower center visually quiet.",
                    "negative_constraints": ["labels", "hard cuts"],
                },
            }
        ],
    }

    validate_artifact("scene_plan", plan)


def test_edit_decisions_accepts_word_indexed_caption_pages_and_line_breaks():
    decisions = {
        "version": "1.0",
        "cuts": [
            {"id": "cut-001", "source": "clip-001", "in_seconds": 0, "out_seconds": 7.4}
        ],
        "captions": [
            {"word": "Rain", "startMs": 0, "endMs": 300},
            {"word": "fell", "startMs": 300, "endMs": 600},
            {"word": "for", "startMs": 600, "endMs": 800},
            {"word": "days.", "startMs": 800, "endMs": 1200},
        ],
        "caption_groups": [
            {
                "id": "caption-001",
                "startMs": 0,
                "endMs": 1200,
                "startWordIndex": 0,
                "endWordIndex": 4,
                "lineBreakAfterWordIndices": [1],
                "translationText": "雨连续下了好几天。",
            }
        ],
        "render_runtime": "remotion",
    }

    validate_artifact("edit_decisions", decisions)

