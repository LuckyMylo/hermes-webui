from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import api.routes as routes


PESTPATROL_BASE = "/home/fortytwolab/repos/pestpatrol-platform"


def test_protected_project_policy_matches_pestpatrol_base_and_nested_path():
    from api.protected_projects import (
        protected_project_for_workspace,
        should_auto_worktree,
    )

    base_project = protected_project_for_workspace(PESTPATROL_BASE)
    nested_project = protected_project_for_workspace(
        f"{PESTPATROL_BASE}/apps/pestpatrol/src/pages"
    )

    assert base_project is not None
    assert base_project.id == "pestpatrol-platform"
    assert nested_project is not None
    assert nested_project.id == "pestpatrol-platform"
    assert should_auto_worktree(PESTPATROL_BASE) is True
    assert should_auto_worktree(f"{PESTPATROL_BASE}/apps/pestpatrol") is True


def test_protected_project_policy_respects_explicit_intent(tmp_path):
    from api.protected_projects import should_auto_worktree

    assert should_auto_worktree(PESTPATROL_BASE, explicit_no_worktree=True) is False
    assert should_auto_worktree(tmp_path, explicit_worktree=True) is True
    assert should_auto_worktree(tmp_path) is False


def _capture_session_new(monkeypatch, body):
    captured = {}

    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(routes, "read_body", lambda handler: body)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda path: Path(str(path)))
    monkeypatch.setattr(routes, "get_last_workspace", lambda: PESTPATROL_BASE)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *a, **k: None)
    monkeypatch.setattr(routes, "_session_model_state_from_request", lambda *a, **k: (None, None))
    monkeypatch.setattr(routes, "_validate_session_toolsets_shape", lambda value: None)

    class FakeSession:
        messages = []
        profile = "default"
        session_id = "auto-wt-session"

        def __init__(self, *, workspace, worktree_info):
            self.workspace = workspace
            self.worktree_path = (worktree_info or {}).get("path")
            self.worktree_branch = (worktree_info or {}).get("branch")
            self.worktree_repo_root = (worktree_info or {}).get("repo_root")
            self.worktree_created_at = (worktree_info or {}).get("created_at")

        def compact(self):
            payload = {
                "session_id": self.session_id,
                "workspace": self.workspace,
            }
            if self.worktree_path:
                payload.update(
                    {
                        "worktree_path": self.worktree_path,
                        "worktree_branch": self.worktree_branch,
                        "worktree_repo_root": self.worktree_repo_root,
                        "worktree_created_at": self.worktree_created_at,
                    }
                )
            return payload

    def fake_new_session(**kwargs):
        captured["new_session_kwargs"] = kwargs
        return FakeSession(
            workspace=kwargs.get("workspace"),
            worktree_info=kwargs.get("worktree_info"),
        )

    monkeypatch.setattr(routes, "new_session", fake_new_session)
    monkeypatch.setattr(
        routes,
        "j",
        lambda handler, payload, status=200, extra_headers=None: captured.update(
            payload=payload,
            status=status,
        )
        or True,
    )
    return captured


def test_session_new_auto_creates_worktree_for_protected_project(monkeypatch):
    import api.worktrees as worktrees

    captured = _capture_session_new(
        monkeypatch,
        {"workspace": PESTPATROL_BASE, "profile": "default"},
    )
    calls = []

    def fake_create_worktree(workspace):
        calls.append(workspace)
        return {
            "path": "/tmp/hermes-wt-pestpatrol",
            "branch": "hermes/pestpatrol/test",
            "repo_root": PESTPATROL_BASE,
            "created_at": 123.0,
        }

    monkeypatch.setattr(worktrees, "create_worktree_for_workspace", fake_create_worktree)

    assert routes.handle_post(object(), SimpleNamespace(path="/api/session/new")) is True

    assert calls == [PESTPATROL_BASE]
    session = captured["payload"]["session"]
    assert captured["status"] == 200
    assert session["workspace"] == "/tmp/hermes-wt-pestpatrol"
    assert session["worktree_branch"] == "hermes/pestpatrol/test"
    assert session["protected_project"] == {
        "id": "pestpatrol-platform",
        "name": "PestPatrol Platform",
        "base_repo": PESTPATROL_BASE,
        "auto_worktree_applied": True,
    }


def test_session_new_worktree_false_opts_out_for_protected_project(monkeypatch):
    import api.worktrees as worktrees

    captured = _capture_session_new(
        monkeypatch,
        {"workspace": PESTPATROL_BASE, "profile": "default", "worktree": False},
    )
    monkeypatch.setattr(
        worktrees,
        "create_worktree_for_workspace",
        lambda workspace: (_ for _ in ()).throw(AssertionError("should not create worktree")),
    )

    assert routes.handle_post(object(), SimpleNamespace(path="/api/session/new")) is True

    session = captured["payload"]["session"]
    assert session["workspace"] == PESTPATROL_BASE
    assert "worktree_path" not in session
    assert session["protected_project"]["auto_worktree_applied"] is False


def test_session_new_non_protected_workspace_does_not_auto_worktree(tmp_path, monkeypatch):
    import api.worktrees as worktrees

    workspace = tmp_path / "ordinary"
    workspace.mkdir()
    captured = _capture_session_new(
        monkeypatch,
        {"workspace": str(workspace), "profile": "default"},
    )
    monkeypatch.setattr(
        worktrees,
        "create_worktree_for_workspace",
        lambda workspace: (_ for _ in ()).throw(AssertionError("should not create worktree")),
    )

    assert routes.handle_post(object(), SimpleNamespace(path="/api/session/new")) is True

    session = captured["payload"]["session"]
    assert session["workspace"] == str(workspace)
    assert "protected_project" not in session


def test_frontend_announces_auto_worktree_payload():
    src = Path("static/sessions.js").read_text(encoding="utf-8")
    assert "protectedProject&&protectedProject.auto_worktree_applied" in src
    assert "Isolated worktree created for" in src
    assert "S.session.worktree_branch" in src
