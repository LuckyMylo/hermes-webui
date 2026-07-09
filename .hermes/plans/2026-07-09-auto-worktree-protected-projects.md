# Auto-Worktree Protected Projects Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Hermes WebUI automatically create an isolated git worktree when a new conversation starts from a protected project workspace such as PestPatrol, so multiple concurrent chats cannot overwrite each other’s local work.

**Architecture:** Reuse the existing WebUI worktree mechanism: frontend `newSession(...,{worktree:true})` already sends `worktree=true`, and backend `/api/session/new` already calls `api.worktrees.create_worktree_for_workspace()`. Add a small protected-project policy layer that decides when `worktree=true` should be implied, plus UI/status affordances and tests. Keep production deploy/merge separate from per-chat worktrees.

**Tech Stack:** Hermes WebUI Python backend (`api/routes.py`, `api/worktrees.py`, config/state helpers), vanilla JS frontend (`static/sessions.js`, `static/panels.js`, `static/commands.js`), pytest via `./scripts/test.sh`.

---

## Current State / Evidence

Already implemented locally for PestPatrol operations:

- `/home/fortytwolab/bin/pestpatrol-task-start`
- `/home/fortytwolab/bin/pestpatrol-task-finish`
- `/home/fortytwolab/bin/pestpatrol-prod-deploy`
- `/home/fortytwolab/bin/pestpatrol-minify-worker`
- PestPatrol repo rule committed in `AGENTS.md`:
  - commit `4089cd9 docs(pestpatrol): require isolated worktrees for multi-agent edits`

Already existing in WebUI:

- `static/sessions.js:newSession(flash, options={})`
  - sends `reqBody.worktree=true` when `options.worktree` is true.
- `static/panels.js`
  - contains a UI path calling `newSession(false,{worktree:true})`.
- `api/routes.py:/api/session/new`
  - accepts `worktree=true`.
  - calls `api.worktrees.create_worktree_for_workspace(base_workspace)`.
  - stores returned metadata via `new_session(..., worktree_info=worktree_info)`.
- `api/worktrees.py`
  - already has create/status/remove helpers.
- `api/webui_session_db.py`
  - already persists `worktree_path`, `worktree_branch`, `worktree_repo_root`, `worktree_created_at`.

Therefore this plan should not build a second worktree system. It should add policy + automation around the existing one.

---

## Desired User Experience

### Normal protected-project flow

If active/new session workspace is:

```text
/home/fortytwolab/repos/pestpatrol-platform
```

and the user starts a new chat, WebUI should automatically create a worktree-backed session:

```text
/home/fortytwolab/repos/pestpatrol-platform/.worktrees/...
```

The chat’s actual `S.session.workspace` becomes the worktree path. The original protected repo remains clean and untouched; PestPatrol now ignores `.worktrees/` in `.gitignore`.

### Explicit override

For read-only/audit/integration cases, user should be able to start a non-worktree session intentionally, but the default for protected repos is safe isolation.

### Clear UI feedback

After auto-creating the worktree, show a small toast/chip:

```text
Isolated worktree created: hermes/...
```

The topbar/workspace chip should display the worktree path or a “worktree” badge so the user can trust the isolation.

---

## Protected Project Policy

Initial static policy should include only PestPatrol:

```json
{
  "id": "pestpatrol-platform",
  "name": "PestPatrol Platform",
  "base_repo": "/home/fortytwolab/repos/pestpatrol-platform",
  "auto_worktree": true,
  "worktree_required_for_new_sessions": true,
  "allow_direct_base_repo_readonly": true
}
```

Implementation should allow future extension to Fortytwolab Platform or Mylo Brain, but do not overbuild a full enterprise policy engine in the first pass.

---

## Task 1: Add protected-project policy helper

**Objective:** Centralize the decision “does this workspace require automatic worktree isolation?”

**Files:**

- Create: `api/protected_projects.py`
- Test: `tests/test_auto_worktree_protected_projects.py`

**Implementation sketch:**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProtectedProject:
    id: str
    name: str
    base_repo: Path
    auto_worktree: bool = True


PROTECTED_PROJECTS = [
    ProtectedProject(
        id="pestpatrol-platform",
        name="PestPatrol Platform",
        base_repo=Path("/home/fortytwolab/repos/pestpatrol-platform"),
    ),
]


def _resolve(path: str | Path | None) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(path).expanduser()


def protected_project_for_workspace(workspace: str | Path | None) -> ProtectedProject | None:
    ws = _resolve(workspace)
    if ws is None:
        return None
    for project in PROTECTED_PROJECTS:
        base = _resolve(project.base_repo)
        if base and (ws == base or base in ws.parents):
            return project
    return None


def should_auto_worktree(workspace: str | Path | None, *, explicit_worktree: bool = False, explicit_no_worktree: bool = False) -> bool:
    if explicit_no_worktree:
        return False
    if explicit_worktree:
        return True
    project = protected_project_for_workspace(workspace)
    return bool(project and project.auto_worktree)
```

**Tests:**

- `protected_project_for_workspace('/home/fortytwolab/repos/pestpatrol-platform')` returns PestPatrol.
- Nested paths under `apps/pestpatrol` also match.
- Existing worktree paths should not trigger a second auto-worktree if they are outside the base repo.
- `explicit_no_worktree=True` wins.

**Verification command:**

```bash
./scripts/test.sh tests/test_auto_worktree_protected_projects.py
```

Expected: PASS.

---

## Task 2: Make `/api/session/new` imply `worktree=true` for protected projects

**Objective:** Ensure frontend does not need to remember to pass `{worktree:true}` for protected base repos.

**Files:**

- Modify: `api/routes.py` around `/api/session/new` lines ~13329-13351.
- Test: `tests/test_auto_worktree_protected_projects.py`

**Implementation sketch:**

Current logic:

```python
worktree_requested = (
    body.get("worktree") is True
    or str(body.get("worktree")).strip().lower() in {"1", "true", "yes", "on"}
)
```

Replace with:

```python
explicit_worktree = (
    body.get("worktree") is True
    or str(body.get("worktree")).strip().lower() in {"1", "true", "yes", "on"}
)
explicit_no_worktree = (
    body.get("worktree") is False
    or str(body.get("worktree")).strip().lower() in {"0", "false", "no", "off"}
)

from api.protected_projects import protected_project_for_workspace, should_auto_worktree

base_workspace = workspace
if not base_workspace:
    base_workspace = str(resolve_trusted_workspace(get_last_workspace()))

protected_project = protected_project_for_workspace(base_workspace)
worktree_requested = should_auto_worktree(
    base_workspace,
    explicit_worktree=explicit_worktree,
    explicit_no_worktree=explicit_no_worktree,
)
```

When `worktree_requested`, call existing `create_worktree_for_workspace(base_workspace)` and set `workspace = worktree_info['path']` as today.

Add response metadata when auto-created:

```python
session_payload = s.compact() | {"messages": s.messages}
if protected_project:
    session_payload["protected_project"] = {
        "id": protected_project.id,
        "name": protected_project.name,
        "base_repo": str(protected_project.base_repo),
        "auto_worktree_applied": bool(worktree_info and not explicit_worktree),
    }
return j(handler, {"session": session_payload})
```

**Tests:**

Use monkeypatch to avoid real git worktree creation:

- POST `/api/session/new` with workspace PestPatrol base and no `worktree` flag calls `create_worktree_for_workspace`.
- POST with workspace PestPatrol base and `worktree:false` does not call worktree helper.
- POST with a non-protected tmp workspace does not auto-worktree.
- Explicit `worktree:true` still works for non-protected repos.

**Verification command:**

```bash
./scripts/test.sh tests/test_auto_worktree_protected_projects.py
```

Expected: PASS.

---

## Task 3: Frontend UX: announce auto-created protected worktree

**Objective:** Make isolation visible and reassuring.

**Files:**

- Modify: `static/sessions.js`
- Maybe modify: `static/i18n*.js` or existing translation source if WebUI has one.
- Test: lightweight static test if the project convention uses string assertions.

**Implementation sketch:**

After:

```javascript
S.session=data.session;S.messages=data.session.messages||[];
```

Add:

```javascript
const protectedProject=data.session&&data.session.protected_project;
if(protectedProject&&protectedProject.auto_worktree_applied&&typeof showToast==='function'){
  const label=protectedProject.name||protectedProject.id||'Protected project';
  const branch=data.session.worktree_branch||'';
  showToast(`Isolated worktree created for ${label}${branch?`: ${branch}`:''}`, 4200, 'success');
}
```

If existing translations are preferred, add key:

```text
protected_worktree_created = Isolated worktree created for {project}
```

**Tests:**

- If frontend tests are static/string based, assert `auto_worktree_applied` is handled in `static/sessions.js`.
- Manual verification later in browser.

**Verification command:**

```bash
./scripts/test.sh tests/test_auto_worktree_protected_projects.py tests/test_static_*.py
```

Adjust exact static test file after inspecting current test conventions.

---

## Task 4: Add a `/worktree` or protected-project status affordance only if needed

**Objective:** Avoid surprise and give Mylo an escape hatch.

**Files:**

- Possibly modify: `static/commands.js`
- Possibly modify: `api/routes.py`

**Preferred minimal behavior:**

Do not add a new command unless user feedback shows need. Existing UI already has worktree status/remove routes. Keep the first version focused:

- auto-worktree on protected new sessions;
- visible toast;
- worktree metadata in session.

**Optional later command:**

```text
/worktree status
/worktree off-new-chat
```

Not required for first implementation.

---

## Task 5: Integrate with workspace selection / New Chat button semantics

**Objective:** Ensure every new-session entrypoint benefits from backend policy without auditing every button.

**Files:**

- Mostly backend from Task 2.
- Read-only check: `static/boot.js`, `static/commands.js`, `static/panels.js`, `static/sessions.js`.

**Rule:** Since all normal new-chat surfaces call `/api/session/new`, backend policy should cover:

- `+ New Chat`
- `/new`
- Cmd/Ctrl+K
- profile default workspace new sessions
- terminal-created session path if it calls `newSession()`

Do not patch every caller unless tests show a caller bypasses `/api/session/new`.

**Verification:**

Use browser after implementation:

1. Select workspace `/home/fortytwolab/repos/pestpatrol-platform`.
2. Click `+ New Chat`.
3. Confirm session workspace is under worktree path, not base repo.
4. Confirm base repo remains clean:

```bash
git -C /home/fortytwolab/repos/pestpatrol-platform status --short --branch
```

Expected:

```text
## main...origin/main
```

---

## Task 6: Manual end-to-end verification in isolated state first

**Objective:** Avoid corrupting the real WebUI state during implementation.

**Files:** none.

**Command:**

```bash
cd /home/fortytwolab/hermes-webui
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-auto-worktree-state \
HERMES_HOME=/home/fortytwolab/.hermes \
HERMES_WEBUI_PORT=8790 \
python3 server.py
```

If server startup requires the existing runtime wrapper, use the local project instructions instead of inventing a new command.

**Manual checks:**

- Add/select PestPatrol base repo as workspace.
- Start a new session.
- Verify returned session has:
  - `workspace` under worktree path;
  - `worktree_path` set;
  - `worktree_branch` set;
  - protected-project payload set.
- Send a trivial read-only prompt and verify tools default to worktree workspace.

---

## Task 7: Real-state rollout for Mylo

**Objective:** Enable the behavior in the actual running WebUI.

**Steps:**

1. Stop/restart WebUI only after tests pass.
2. Preserve current session state; do not wipe `~/.hermes` or WebUI state.
3. Restart the WebUI service/process.
4. In the real WebUI:
   - select PestPatrol base repo;
   - create a new conversation;
   - verify it auto-binds to a worktree;
   - verify original repo remains clean.

**Rollback:**

- Revert the WebUI commit.
- Restart WebUI.
- Existing worktree-backed sessions remain valid because WebUI already supports worktree metadata.

---

## Task 8: Generalize later, not now

**Objective:** Avoid overbuilding before the first protected-project flow is proven.

After PestPatrol is validated, consider:

- config file for protected projects instead of hardcoded policy;
- UI settings page to manage protected projects;
- project-specific finish/deploy command discovery;
- auto-PR creation from WebUI;
- stale worktree dashboard/cleanup;
- branch naming template using session title.

Do not include these in the first implementation unless Mylo explicitly asks.

---

## Acceptance Criteria

- Starting a new WebUI session from `/home/fortytwolab/repos/pestpatrol-platform` automatically creates an isolated worktree.
- The session’s workspace is the worktree path, not the base repo.
- The base repo stays clean after the session starts.
- Existing explicit `{worktree:true}` behavior still works.
- User can opt out with `worktree:false` for intentional read-only/integration sessions.
- Worktree metadata is persisted and visible through existing session/worktree status paths.
- Tests pass via `./scripts/test.sh` for the touched backend/frontend contracts.

---

## Recommended Implementation Order

1. Implement `api/protected_projects.py` + unit tests.
2. Patch `/api/session/new` policy logic + route tests.
3. Patch `static/sessions.js` toast/metadata handling + static test if appropriate.
4. Run focused tests.
5. Run broader relevant tests:
   ```bash
   ./scripts/test.sh tests/test_auto_worktree_protected_projects.py tests/test_issue2057_worktree_status.py tests/test_worktree_remove.py
   ```
6. Manual WebUI smoke in isolated state.
7. Commit WebUI change.
8. Restart real WebUI and validate PestPatrol auto-worktree.

---

## Non-Goals

- Do not change git history for existing PestPatrol worktrees.
- Do not force all non-code chats into worktrees.
- Do not auto-merge or auto-deploy from isolated chat branches.
- Do not replace `pestpatrol-task-finish` / `pestpatrol-prod-deploy`; WebUI auto-worktree only solves isolation at session start.
- Do not introduce `git push --force` or destructive cleanup.

---

## Final Note

The key insight is that Hermes WebUI already has the hard part: worktree-backed sessions. The missing piece is policy: “PestPatrol base repo is protected, so new conversations from that workspace imply `worktree=true` unless explicitly opted out.”
