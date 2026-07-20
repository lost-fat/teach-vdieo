"""Lesson Studio API and UI contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from backlot.lesson_studio import (
    LessonStudioValidationError,
    _compile_prompt_cards,
    advance_lesson_stage,
    generate_lesson_scene_video,
)


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", root)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", root)
    monkeypatch.setattr(server_mod, "_summary_cache", {})
    monkeypatch.setattr(
        server_mod,
        "_PROJECTS_ROOT_STR",
        __import__("os").path.normcase(str(root.resolve())),
    )
    return root


@pytest.fixture
def client(projects_root, monkeypatch):
    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as test_client:
        yield test_client


ARTICLE = (
    "A new railway connected the port and the capital. "
    "Before it opened, the old journey was slow and unreliable. "
    "Today, food reaches the market sooner and local businesses benefit."
)


class TestLessonStudioApi:
    def test_studio_page_is_served(self, client):
        response = client.get("/studio")

        assert response.status_code == 200
        assert "英语课文视频工作台" in response.text
        assert 'id="lessonText"' in response.text

    def test_config_exposes_locked_video_duration_envelope(self, client):
        response = client.get("/api/lesson-studio/config")

        assert response.status_code == 200
        assert response.json()["video_output"] == {
            "duration_min_seconds": 2,
            "duration_max_seconds": 15,
            "duration_default_seconds": 5,
            "planned_scene_seconds": 14,
            "duration_step_seconds": 1,
            "resolutions": ["720P", "1080P"],
            "fps": 30,
        }

    def test_create_project_locks_the_source_and_returns_studio_url(
        self, client, projects_root
    ):
        response = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Railway Lesson", "source_text": ARTICLE},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["project_id"].startswith("railway-lesson-")
        assert body["studio_url"] == f"/studio?project={body['project_id']}"
        project = projects_root / body["project_id"]
        marker = json.loads((project / "project.json").read_text())
        source = json.loads((project / "artifacts" / "lesson_source.json").read_text())
        workflow = json.loads((project / "studio_state.json").read_text())
        assert marker["pipeline_type"] == "english-textbook"
        assert source["normalized_text"] == ARTICLE
        assert workflow["stage"] == "source_ready"
        assert (project / "inputs" / "article.txt").read_text() == ARTICLE

    def test_create_project_rejects_short_or_oversized_input(self, client):
        short = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Too short", "source_text": "One line."},
        )
        oversized = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Too long", "source_text": "A" * 20001},
        )

        assert short.status_code == 422
        assert oversized.status_code == 422

    def test_plan_action_returns_storyboard_state(self, client, projects_root, monkeypatch):
        created = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Plan Me", "source_text": ARTICLE},
        ).json()
        project_id = created["project_id"]

        def fake_plan(project_dir):
            assert project_dir == projects_root / project_id
            (project_dir / "artifacts" / "scene_plan.json").write_text(
                json.dumps({"version": "1.0", "scenes": []})
            )
            state_path = project_dir / "studio_state.json"
            state = json.loads(state_path.read_text())
            state.update({"stage": "storyboard_ready", "status": "awaiting_human"})
            state_path.write_text(json.dumps(state))
            return {"scene_count": 3}

        monkeypatch.setattr(server_mod, "plan_lesson_storyboard", fake_plan, raising=False)

        response = client.post(f"/api/lesson-studio/projects/{project_id}/plan")

        assert response.status_code == 200
        assert response.json()["stage"] == "storyboard_ready"
        assert response.json()["plan"]["scene_count"] == 3

    def test_generate_one_image_calls_the_locked_scene_action(
        self, client, projects_root, monkeypatch
    ):
        created = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Image Me", "source_text": ARTICLE},
        ).json()
        project_id = created["project_id"]
        project_dir = projects_root / project_id
        (project_dir / "artifacts" / "scene_plan.json").write_text(
            json.dumps({
                "version": "1.0",
                "scenes": [{
                    "id": "sc_1",
                    "type": "generated",
                    "description": "A crate begins its journey.",
                    "start_seconds": 0,
                    "end_seconds": 14,
                }],
            })
        )

        called = {}

        def fake_generate(project_dir_arg, scene_id):
            called.update({"project_dir": project_dir_arg, "scene_id": scene_id})
            return {"asset_id": "image-sc_1-take-1", "scene_id": scene_id}

        monkeypatch.setattr(
            server_mod,
            "generate_lesson_scene_image",
            fake_generate,
            raising=False,
        )

        response = client.post(
            f"/api/lesson-studio/projects/{project_id}/scenes/sc_1/image"
        )

        assert response.status_code == 200
        assert response.json()["asset"]["asset_id"] == "image-sc_1-take-1"
        assert called == {"project_dir": project_dir, "scene_id": "sc_1"}

    def test_generate_one_video_calls_the_locked_scene_action(
        self, client, projects_root, monkeypatch
    ):
        created = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Animate Me", "source_text": ARTICLE},
        ).json()
        project_id = created["project_id"]
        called = {}

        def fake_generate(project_dir_arg, scene_id):
            called.update({"project_dir": project_dir_arg, "scene_id": scene_id})
            return {"asset_id": "video-sc_1-take-1", "scene_id": scene_id}

        monkeypatch.setattr(
            server_mod,
            "generate_lesson_scene_video",
            fake_generate,
            raising=False,
        )

        response = client.post(
            f"/api/lesson-studio/projects/{project_id}/scenes/sc_1/video"
        )

        assert response.status_code == 200
        assert response.json()["stage"] == "videos_in_review"
        assert response.json()["asset"]["asset_id"] == "video-sc_1-take-1"
        assert called == {
            "project_dir": projects_root / project_id,
            "scene_id": "sc_1",
        }

    def test_scene_action_rejects_path_like_scene_ids(self, client):
        created = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Safe Lesson", "source_text": ARTICLE},
        ).json()

        response = client.post(
            f"/api/lesson-studio/projects/{created['project_id']}/scenes/%2E%2E/image"
        )

        assert response.status_code in {400, 404}


def test_chinese_prompt_compiler_keeps_provider_prompts_in_chinese():
    plan = {
        "continuity_bible": {
            "entities": [{
                "canonical_name": "红角木箱",
                "immutable_traits": ["木质", "一角涂有红漆"],
            }],
            "style": {
                "palette": ["大地色", "铁路蓝"],
                "lighting": "自然纪录片日光",
                "texture": "电影感编辑写实质感",
            },
        },
        "scenes": [{
            "id": "sc_1",
            "description": "低机位近景看见红角木箱停在旧铁轨旁。",
            "start_seconds": 0,
            "end_seconds": 14,
            "story_chapter_id": "chapter-01",
            "story_beat": "setup",
            "story_contribution": "建立等待与距离感。",
            "video_prompt_spec": {
                "single_shot": True,
                "subject_motion": "木箱上的绳子随风轻动。",
                "camera_motion": "摄像机缓慢侧移并向前推进。",
                "temporal_beats": [
                    {"start_seconds": 0, "end_seconds": 5, "action": "尘土掠过木箱。"},
                    {"start_seconds": 5, "end_seconds": 10, "action": "旧铁轨逐渐显现。"},
                    {"start_seconds": 10, "end_seconds": 14, "action": "远处旧火车驶近。"},
                ],
                "continuity_refs": ["carrier-main"],
                "caption_safe_area": "画面下方保留字幕安全区。",
                "negative_constraints": ["不要可读文字", "不要镜头内硬切"],
            },
        }],
    }

    card = _compile_prompt_cards(plan)["shots"][0]

    assert card["image_prompt_preview"].startswith("英语教学视频的电影感首帧")
    assert card["video_prompt"].startswith("生成一个完整连续的单镜头")
    assert "0–5 秒" in card["video_prompt"]
    assert card["negative_video_prompt"].startswith("禁止")
    assert "Generate a single continuous shot" not in card["video_prompt"]


def test_stage_advance_requires_complete_assets(tmp_path):
    project = tmp_path / "lesson"
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    (project / "studio_state.json").write_text(json.dumps({
        "version": "1.0",
        "project_id": "lesson",
        "stage": "storyboard_ready",
        "status": "awaiting_human",
    }))
    (artifacts / "scene_plan.json").write_text(json.dumps({
        "version": "1.0",
        "scenes": [{
            "id": "sc_1",
            "type": "generated",
            "description": "一个连续镜头",
            "start_seconds": 0,
            "end_seconds": 14,
        }],
    }))
    manifest_path = artifacts / "asset_manifest.json"
    manifest_path.write_text(json.dumps({"version": "1.0", "assets": []}))

    with pytest.raises(LessonStudioValidationError, match="1 张首帧"):
        advance_lesson_stage(project)

    manifest_path.write_text(json.dumps({
        "version": "1.0",
        "assets": [{"id": "i1", "type": "image", "path": "i.png", "source_tool": "mock", "scene_id": "sc_1"}],
    }))
    assert advance_lesson_stage(project)["stage"] == "video_ready"

    with pytest.raises(LessonStudioValidationError, match="1 段视频"):
        advance_lesson_stage(project)

    manifest_path.write_text(json.dumps({
        "version": "1.0",
        "assets": [
            {"id": "i1", "type": "image", "path": "i.png", "source_tool": "mock", "scene_id": "sc_1"},
            {"id": "v1", "type": "video", "path": "v.mp4", "source_tool": "mock", "scene_id": "sc_1"},
        ],
    }))
    assert advance_lesson_stage(project)["stage"] == "compose_ready"


def test_scene_video_generation_uses_locked_wan_contract(tmp_path, monkeypatch):
    project = tmp_path / "lesson"
    artifacts = project / "artifacts"
    image_dir = project / "assets" / "images"
    artifacts.mkdir(parents=True)
    image_dir.mkdir(parents=True)
    (image_dir / "sc_1.png").write_bytes(b"image")
    spec = {
        "single_shot": True,
        "subject_motion": "列车稳定向前行驶。",
        "camera_motion": "摄像机缓慢侧移。",
        "temporal_beats": [
            {"start_seconds": 0, "end_seconds": 5, "action": "列车进入画面。"},
            {"start_seconds": 5, "end_seconds": 10, "action": "前景树木形成视差。"},
            {"start_seconds": 10, "end_seconds": 14, "action": "列车驶向城市。"},
        ],
        "continuity_refs": ["carrier-main"],
        "caption_safe_area": "下方留出字幕空间。",
        "negative_constraints": ["不要硬切"],
    }
    (artifacts / "scene_plan.json").write_text(json.dumps({
        "version": "1.0",
        "scenes": [{
            "id": "sc_1",
            "type": "generated",
            "description": "列车驶过草原。",
            "start_seconds": 0,
            "end_seconds": 14,
            "video_prompt_spec": spec,
        }],
    }))
    (artifacts / "compiled_shot_prompts.json").write_text(json.dumps({
        "version": "1.0",
        "shots": [{
            "scene_id": "sc_1",
            "video_prompt": "生成一个完整连续的单镜头。",
            "negative_video_prompt": "禁止镜头内硬切。",
        }],
    }))
    (artifacts / "lesson_source.json").write_text(json.dumps({"source_sha256": "abc"}))
    (artifacts / "asset_manifest.json").write_text(json.dumps({
        "version": "1.0",
        "assets": [{
            "id": "image-sc_1-take-1",
            "type": "image",
            "path": "assets/images/sc_1.png",
            "source_tool": "dashscope_image",
            "scene_id": "sc_1",
        }],
    }))
    captured = {}

    class FakeVideo:
        def execute(self, inputs):
            captured.update(inputs)
            output = Path(inputs["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            return SimpleNamespace(success=True, cost_usd=0, data={})

    monkeypatch.setattr("backlot.lesson_studio.DashscopeVideo", FakeVideo)

    result = generate_lesson_scene_video(project, "sc_1")

    assert result["asset_id"] == "video-sc_1-take-1"
    assert captured["model"] == "wan2.6-i2v-flash"
    assert captured["duration"] == 14
    assert captured["resolution"] == "1080P"
    assert captured["audio"] is False
    assert captured["prompt_extend"] is False
    assert captured["watermark"] is False
    assert captured["prompt"].startswith("生成一个完整连续的单镜头")


class TestLessonStudioUiContract:
    def test_ui_exposes_progressive_generation_controls(self):
        ui_dir = Path(__file__).resolve().parents[2] / "backlot" / "ui"
        html = (ui_dir / "studio.html").read_text(encoding="utf-8")
        js = (ui_dir / "studio.js").read_text(encoding="utf-8")
        css = (ui_dir / "studio.css").read_text(encoding="utf-8")

        assert "生成分镜" in html
        assert "qwen3.7-plus" in html
        assert "生成首帧" in js
        assert "qwen-image-2.0-pro" in js
        assert "图片生成提示词" in js
        assert "视频生成提示词" in js
        assert "时间动作节拍" in js
        assert "计划首帧" in js
        assert "个版本" in js
        assert '["turning_point", "转折"]' in js
        assert "2–15 秒" in html
        assert 'id="nextStep"' in html
        assert "确认全部首帧，进入视频生成" in js
        assert "确认全部视频，进入字幕与合成" in js
        assert "/advance" in js
        assert "/video" in js
        assert ".studio-shell [hidden]" in css
        assert "display: none !important" in css
        assert "/api/lesson-studio/projects" in js

    def test_planner_contract_requires_chinese_generation_fields(self):
        contract = (
            Path(__file__).resolve().parents[2]
            / "skills/pipelines/english-textbook/studio-preview-planner.md"
        ).read_text(encoding="utf-8")

        assert "`source_text` 必须保留英文原文" in contract
        assert "其余所有文本字段必须使用简体中文" in contract
