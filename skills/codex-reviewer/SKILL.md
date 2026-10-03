---
name: codex-reviewer
description: Reviews Antigravity implementation against assigned requirements and records evidence-based acceptance or concrete CLI correction feedback.
---

Collect workspace state using:
```bash
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py collect --workspace <project>
```

Inspect the task specification, worker report, `events.ndjson`, and `stderr.log`. Inspect actual repository files, Git before/after snapshots, and any untracked files (note that Git snapshots do not contain file contents for untracked files). Preserve existing user edits and project conventions. Check acceptance criteria, scope boundaries, edge cases, and architectural constraints. Verify claimed commands and results against recorded tool events, and run additional verification checks where risk warrants.

If changes or verification checks fail requirements or are incomplete:
1. Write specific, actionable feedback into a UTF-8 text file outside the project repository.
2. Submit the revision to the worker:
```bash
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py revise --workspace <project> --feedback-file <UTF-8-file>
```
Do not count denied or unrun commands as passed checks. Stop ineffective correction loops and report concrete blockers.

Only when actual changes meet all acceptance checks and evidence is validated:
1. Write review evidence outside the repo documenting requirements checked, files/diffs inspected, validation results, and material limits.
2. Record acceptance:
```bash
python -X utf8 ~/.gemini/antigravity-cli/codex-bridge/bridge.py accept --workspace <project> --review-file <UTF-8-file>
```
Acceptance records a Codex decision; it does not commit, push, or deploy code. Report the verified behavior and review findings to the user.
