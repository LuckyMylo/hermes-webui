"""Protected-project policy for WebUI session worktree isolation.

A protected project is a repository where starting ordinary editing sessions in
its base checkout is unsafe because multiple agents may work concurrently.  For
those repositories, new WebUI sessions should default to an isolated git
worktree unless the caller explicitly opts out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProtectedProject:
    id: str
    name: str
    base_repo: Path
    auto_worktree: bool = True


PROTECTED_PROJECTS: tuple[ProtectedProject, ...] = (
    ProtectedProject(
        id="pestpatrol-platform",
        name="PestPatrol Platform",
        base_repo=Path("/home/fortytwolab/repos/pestpatrol-platform"),
    ),
)


def _resolve_path(path: str | Path | None) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(path).expanduser()


def protected_project_for_workspace(workspace: str | Path | None) -> ProtectedProject | None:
    """Return the protected project containing *workspace*, if any."""

    ws = _resolve_path(workspace)
    if ws is None:
        return None
    for project in PROTECTED_PROJECTS:
        base = _resolve_path(project.base_repo)
        if base and (ws == base or base in ws.parents):
            return project
    return None


def should_auto_worktree(
    workspace: str | Path | None,
    *,
    explicit_worktree: bool = False,
    explicit_no_worktree: bool = False,
) -> bool:
    """Return whether a new session should be created in a git worktree.

    Explicit caller intent wins over policy: ``worktree=false`` disables
    automatic isolation for read-only/integration cases, while ``worktree=true``
    still works for unprotected repositories.
    """

    if explicit_no_worktree:
        return False
    if explicit_worktree:
        return True
    project = protected_project_for_workspace(workspace)
    return bool(project and project.auto_worktree)


def protected_project_payload(project: ProtectedProject, *, auto_worktree_applied: bool) -> dict:
    """Return compact project metadata safe for frontend session payloads."""

    return {
        "id": project.id,
        "name": project.name,
        "base_repo": str(_resolve_path(project.base_repo) or project.base_repo),
        "auto_worktree_applied": bool(auto_worktree_applied),
    }
