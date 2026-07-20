"""Lesson Studio API and UI contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod


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
        assert "Lesson Studio" in response.text
        assert 'id="lessonText"' in response.text

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

    def test_scene_action_rejects_path_like_scene_ids(self, client):
        created = client.post(
            "/api/lesson-studio/projects",
            json={"title": "Safe Lesson", "source_text": ARTICLE},
        ).json()

        response = client.post(
            f"/api/lesson-studio/projects/{created['project_id']}/scenes/%2E%2E/image"
        )

        assert response.status_code in {400, 404}


class TestLessonStudioUiContract:
    def test_ui_exposes_progressive_generation_controls(self):
        ui_dir = Path(__file__).resolve().parents[2] / "backlot" / "ui"
        html = (ui_dir / "studio.html").read_text(encoding="utf-8")
        js = (ui_dir / "studio.js").read_text(encoding="utf-8")

        assert "生成分镜" in html
        assert "qwen3.7-plus" in html
        assert "生成首帧" in js
        assert "qwen-image-2.0-pro" in js
        assert "VIDEO PROMPT" in js
        assert "/api/lesson-studio/projects" in js
