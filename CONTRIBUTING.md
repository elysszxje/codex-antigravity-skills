# Contributing to codex-antigravity-skills

Thank you for contributing to `codex-antigravity-skills`.

## Guiding Principles

1. **Zero External Dependencies**: The codebase runs exclusively on Python standard library (Python >= 3.10). Do not add dependencies to `pyproject.toml` or require external packages for running tests or installing the tool.
2. **Cross-Platform Compatibility**: Code and paths must function seamlessly across Windows, Linux, and macOS. Always support spaces and Unicode in file paths.
3. **Privacy & Hygiene**: Never commit hardcoded personal usernames, absolute paths, authentication tokens, runtime logs, or state directories.
4. **Safety & Separation**: Keep runtime state outside repository checkouts. Never delete user data or runtime state during updates.

## Development & Testing

Run tests and bytecode compilation directly from checkout:

```bash
python -m unittest discover -s tests -v
python -m compileall -q bridge scripts tests
```

On Windows:
```powershell
python -m unittest discover -s tests -v
python -m compileall -q bridge scripts tests
```

## Submitting Changes

- Ensure all existing tests pass and add unit tests for new behaviors or bug fixes.
- Verify idempotence and conflict protection when modifying installation logic.
- Avoid introducing placeholders or external package requirements.
