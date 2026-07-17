"""Generalized article-mode contracts for the English textbook pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path

from schemas.artifacts import validate_artifact


def test_article_scene_plan_supports_a_visual_story_arc_instead_of_sentence_illustration():
    plan = {
        "version": "1.0",
        "narrative_units": [
            {
                "id": "unit-001",
                "source_text": "The old journey was slow.",
                "start_seconds": 0,
                "end_seconds": 4,
                "discourse_role": "setting",
            },
            {
                "id": "unit-002",
                "source_text": "A new railway changed the connection.",
                "start_seconds": 4,
                "end_seconds": 8,
                "discourse_role": "historical_event",
            },
            {
                "id": "unit-003",
                "source_text": "People could arrive sooner.",
                "start_seconds": 8,
                "end_seconds": 12,
                "discourse_role": "cause_effect",
            },
        ],
        "visual_story_arc": {
            "theme": "Connection changes everyday possibility.",
            "visual_premise": "Follow one food crate from an uncertain departure to a timely arrival.",
            "story_carrier": {
                "id": "carrier-crate",
                "kind": "object",
                "description": "One recognizable produce crate that carries the transformation.",
                "continuity_ref": "entity-crate",
            },
            "opening_state": "The crate waits beside an unreliable old route.",
            "turning_point": "The new railway takes over the journey.",
            "closing_state": "The same crate arrives fresh as the market opens.",
            "recurring_motif": "A clock hand and the crate's red corner mark.",
            "chapters": [
                {
                    "id": "chapter-setup",
                    "role": "setup",
                    "narrative_unit_ids": ["unit-001"],
                    "objective": "Make waiting and distance tangible.",
                    "entry_state": "The journey has not begun.",
                    "exit_state": "Delay feels costly.",
                },
                {
                    "id": "chapter-turn",
                    "role": "turning_point",
                    "narrative_unit_ids": ["unit-002"],
                    "objective": "Let the carrier enter the new system.",
                    "entry_state": "The old route dominates.",
                    "exit_state": "The new route creates momentum.",
                },
                {
                    "id": "chapter-payoff",
                    "role": "payoff",
                    "narrative_unit_ids": ["unit-003"],
                    "objective": "Resolve the journey through a human-scale consequence.",
                    "entry_state": "Arrival is still uncertain.",
                    "exit_state": "The benefit is visible at the market.",
                },
            ],
        },
        "continuity_bible": {
            "entities": [
                {
                    "id": "entity-crate",
                    "canonical_name": "produce crate",
                    "immutable_traits": ["weathered wood", "red painted corner"],
                }
            ],
            "locations": [],
            "period": None,
            "style": {
                "palette": ["earth brown", "railway red"],
                "lighting": "natural documentary light",
                "texture": "mature editorial realism",
            },
            "camera_rules": ["preserve screen direction"],
            "prohibited_elements": ["readable generated text"],
        },
        "scenes": [
            {
                "id": "scene-001",
                "type": "generated",
                "description": "The crate waits in the foreground while the difficult route recedes behind it.",
                "start_seconds": 0,
                "end_seconds": 4,
                "narrative_unit_ids": ["unit-001"],
                "story_chapter_id": "chapter-setup",
                "story_beat": "setup",
                "story_contribution": "Introduce the carrier and make delay the problem.",
                "visual_mode": "interpretive",
                "visual_state_change": {
                    "from": "still and unattended",
                    "to": "claimed for a difficult departure",
                },
            },
            {
                "id": "scene-002",
                "type": "generated",
                "description": "A match action carries the same crate onto the new train.",
                "start_seconds": 4,
                "end_seconds": 8,
                "narrative_unit_ids": ["unit-002"],
                "story_chapter_id": "chapter-turn",
                "story_beat": "turning_point",
                "story_contribution": "Transform waiting into forward motion.",
                "visual_mode": "bridge",
                "visual_state_change": {"from": "blocked", "to": "moving"},
                "continuity_from_scene_id": "scene-001",
                "match_action": "Hands lift the crate in the same screen direction.",
            },
            {
                "id": "scene-003",
                "type": "generated",
                "description": "The marked crate opens at a lively morning market.",
                "start_seconds": 8,
                "end_seconds": 12,
                "narrative_unit_ids": ["unit-003"],
                "story_chapter_id": "chapter-payoff",
                "story_beat": "payoff",
                "story_contribution": "Pay off the journey with fresh food arriving on time.",
                "visual_mode": "payoff",
                "visual_state_change": {"from": "in transit", "to": "useful to people"},
                "continuity_from_scene_id": "scene-002",
                "match_action": "The opening lid completes the prior unloading action.",
            },
        ],
    }

    validate_artifact("scene_plan", plan)


def test_scene_director_requires_two_pass_article_dramaturgy():
    director = (
        Path(__file__).resolve().parent.parent.parent
        / "skills"
        / "pipelines"
        / "english-textbook"
        / "scene-director.md"
    ).read_text(encoding="utf-8")

    assert "Pass A — whole-article dramaturgy" in director
    assert "Pass B — shot design" in director
    assert "Narrative units are evidence, not shot requests" in director
    assert "story carrier" in director


def test_lesson_plan_can_lock_article_mode_to_measured_narration():
    source = "Rain fell for several days. As a result, the river rose quickly."
    plan = {
        "version": "1.0",
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "delivery_mode": "article",
        "duration_policy": {
            "mode": "narration_measured",
            "clip_target_seconds": 10,
            "clip_max_seconds": 15,
        },
        "audience": {"level": "A2-B1", "description": "English learners"},
        "target_duration_seconds": 12,
        "caption": {"language": "en", "mode": "word_highlight"},
        "voice": {
            "profile": "english_teacher_female",
            "model": "qwen3-tts-vd-2026-01-26",
            "language_type": "English",
        },
        "visual": {
            "style_playbook": "esl-cinematic-editorial",
            "image_model": "qwen-image-2.0-pro",
            "video_model": "wan2.6-i2v-flash",
        },
        "render": {
            "runtime": "remotion",
            "composition_mode": "templated",
            "resolution": "1920x1080",
            "fps": 30,
        },
        "music": {"source": "none", "reason": "Keep the lesson voice clear"},
        "quota_policy": {"free_tier_only": True, "paid_spend_cap": 0},
    }

    validate_artifact("lesson_plan", plan)


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
