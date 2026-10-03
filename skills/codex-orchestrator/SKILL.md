---
name: codex-orchestrator
description: Delegates implementation to Antigravity CLI for the user's Codex-led workflow, preserving task context and collecting review evidence.
---

Codex owns planning, strategy, architecture and acceptance; Antigravity executes code and reports to Codex. Use the persistent bridge runtime installed at `~/.gemini/antigravity-cli/codex-bridge/bridge.py`.

Identify the actual project directory, read its instructions, dependencies, and Git status, and inspect bridge status before dispatching work. Never assume the home directory is the project directory. Resume unresolved tasks, preserving existing user changes. For risky or exploratory changes, use an isolated Git worktree when appropriate without switching the user's active branch.

Create a UTF-8 JSON task file outside the project repository containing:
- `objective` (string): clear, single-sentence implementation goal.
- `scope` (array of strings): explicit files, directories, and boundaries allowed for editing.
- `acceptance_checks` (array of strings): concrete verification steps and assertions.
- `commands` (array of strings): exact commands authorized to run for verification.
- `decisions` (array of strings): architectural or interface decisions already agreed upon.

Invoke the bridge through the terminal (using Python standard library, requiring no third-party dependencies):
```bash
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py dispatch --workspace <absolute-project-dir> --task-file <absolute-json-file>
```
On Windows PowerShell:
```powershell
python -X utf8 "$HOME/.gemini/antigravity-cli/codex-bridge/bridge.py" dispatch --workspace "<absolute-project-dir>" --task-file "<absolute-json-file>"
```
Always quote paths properly; never interpolate untrusted task text directly into shell arguments. Keep monitoring the running session; never duplicate a dispatch while a workspace lock is active.

Durable state and run logs are stored outside the project repository under `~/.gemini/antigravity-cli/codex-bridge/workspaces/<hash>` (configurable via `CODEX_AGY_STATE_DIR`). Tasks, reports, review evidence, conversation IDs, and Git before/after snapshots persist across VS Code and session restarts.

To check status or collect artifacts:
```bash
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py status --workspace <project>
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py collect --workspace <project>
```

When revising an existing conversation after reviewing output, supply corrections through:
```bash
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py revise --workspace <project> --feedback-file <UTF-8-file>
```
Never use global `--continue`.

Always use `codex-reviewer` before accepting. Tasks in `failed`, `blocked`, `interrupted`, or with permission notices require diagnosis and actual code inspection, never blind retries. If a crashed run leaves an `active.lock`, verify that worker and bridge PIDs are stopped before removing only that specific lock.

Do not implement product code as a silent fallback if Antigravity is unavailable; explain the blocker clearly and continue planning/review.

Important notes on permissions and environment:
- Skill instructions are behavioral guidance, not security enforcement. Configured CLI tool permissions and system protection boundaries still apply.
- In headless mode, permission denials or expired authentications may exit 0 with soft-denials (`denied_actions`), returning a blocked status. Verify real evidence.
- On Windows with Codex sandbox isolation, user terminal authentication credentials may not be inherited by sandbox subprocesses. Full unattended execution is not guaranteed in sandboxed environments.
