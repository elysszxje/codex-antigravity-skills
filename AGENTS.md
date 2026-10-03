# Instructions for AI Agents Contributing to codex-antigravity-skills

Welcome, AI contributor. When working within this repository, adhere strictly to the following rules and conventions:

## 1. Zero External Dependencies
- Use Python standard library modules only (Python >= 3.10).
- Do not import, install, or declare external third-party libraries (no `pip install`, no `requests`, no `pytest`, etc.).
- The test suite and tools must run directly from checkout using `python -m unittest`.

## 2. Privacy & Portability
- Never commit hardcoded absolute user home paths, usernames, environment tokens, or machine-specific identities.
- Use `Path.home()`, `os.environ`, or CLI flag overrides for path resolution.
- Ensure all file operations, path joining, and subprocess calls support cross-platform path separators, whitespace in paths, and UTF-8 encoding.

## 3. Runtime State Isolation
- Runtime execution state, workspace archives, and lock files must never be placed inside the repository checkout.
- State defaults to `~/.gemini/antigravity-cli/codex-bridge/workspaces` and is configurable via `CODEX_AGY_STATE_DIR` or `--state-dir`.

## 4. Verification Requirements
Always execute the required verification commands before concluding any task:
```bash
python -m unittest discover -s tests -v
python -m compileall -q bridge scripts tests
```
Do not report success without actual command execution evidence.
