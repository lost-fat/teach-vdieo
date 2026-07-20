"""Lesson Studio compose recovery and immutable-input contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backlot.lesson_compose import (
    _lock_compose_inputs,
    _normalize_asr_word_durations,
    _semantic_group_ranges,
)
from backlot.lesson_studio import LessonStudioValidationError


SOURCE = (
    "Tom found a small bird under a tree. "
    "Its wing was hurt, so he took it home and cared for it."
)
UNITS = [
    {
        "id": "unit-01",
        "source_text": "Tom found a small bird under a tree.",
        "start_seconds": 0,
        "end_seconds": 5,
    },
    {
        "id": "unit-02",
        "source_text": "Its wing was hurt, so he took it home and cared for it.",
        "start_seconds": 5,
        "end_seconds": 10,
    },
]


def test_zero_duration_asr_token_is_expanded_without_changing_text():
    words = [{
        "text": "a ",
        "begin_time_seconds": 0.8,
        "end_time_seconds": 0.8,
    }]

    normalized = _normalize_asr_word_durations(words)

    assert normalized == [{
        "text": "a ",
        "begin_time_seconds": 0.8,
        "end_time_seconds": 0.801,
    }]
    assert words[0]["end_time_seconds"] == 0.8


def test_caption_groups_prefer_clause_punctuation_and_remain_inside_units():
    groups = _semantic_group_ranges(SOURCE, UNITS)

    assert [group["english"] for group in groups] == [
        "Tom found a small bird under a tree.",
        "Its wing was hurt,",
        "so he took it home and cared for it.",
    ]
    assert [group["unit_index"] for group in groups] == [0, 1, 1]


def test_compose_input_snapshot_detects_mutation_without_writing_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "lesson"
    video = project / "assets" / "video" / "sc_1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"approved-video")
    artifacts = project / "artifacts"
    artifacts.mkdir()
    (artifacts / "asset_manifest.json").write_text(json.dumps({
        "version": "1.0",
        "assets": [{
            "id": "video-sc_1-take-1",
            "type": "video",
            "path": "assets/video/sc_1.mp4",
            "source_tool": "mock",
            "scene_id": "sc_1",
        }],
    }))
    plan = {"scenes": [{
        "id": "sc_1",
        "start_seconds": 0,
        "end_seconds": 5,
    }]}
    monkeypatch.setattr(
        "backlot.lesson_compose._probe_media",
        lambda _: {
            "duration_seconds": 5.0,
            "codec": "h264",
            "width": 1440,
            "height": 1440,
        },
    )

    before = video.read_bytes()
    first = _lock_compose_inputs(project, plan)
    second = _lock_compose_inputs(project, plan)

    assert video.read_bytes() == before
    assert first == second
    assert (artifacts / "compose_input_snapshot.json").is_file()

    video.write_bytes(b"changed-video")
    with pytest.raises(LessonStudioValidationError, match="快照不一致"):
        _lock_compose_inputs(project, plan)
