# Installation Guide

This guide covers installing and configuring `codex-antigravity-skills` to establish a persistent bridge between Codex (strategy & review) and Antigravity (implementation worker).

## Prerequisites

- **Python**: Version 3.10 or later (standard library only; no external packages needed).
- **Antigravity CLI (`agy`)**: Installed and authenticated in your user environment. Run `agy` interactively or authenticate before headless usage. See [Antigravity CLI Headless Documentation](https://www.antigravity.google/docs/cli/headless/).
- **Codex**: Running in VS Code or CLI with skill discovery enabled. See [OpenAI Skills Guide](https://learn.chatgpt.com/docs/build-skills) and [AGENTS.md Configuration](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Quick Start

1. Clone or download the repository checkout.
2. Authenticate the Antigravity CLI in your normal user terminal session:
   ```bash
   agy
   ```
3. Run the installer from the repository root:
   ```bash
   python scripts/install.py
   ```
   On Windows PowerShell:
   ```powershell
   python scripts\install.py
   ```
4. Restart your Codex session in VS Code to discover the newly installed skills.

## What Gets Installed

1. **Bridge Runtime**:
   - Location: `~/.gemini/antigravity-cli/codex-bridge/`
   - Files: `bridge.py`, `worker.md`, `report.schema.json`, `.manifest.json`
2. **Codex Skills**:
   - Location: `~/.agents/skills/`
   - Skills: `codex-orchestrator`, `codex-reviewer`
3. **Antigravity Skills**:
   - Location: `~/.gemini/antigravity-cli/skills/`
   - Skills: `antigravity-worker`
4. **Global Instructions**:
   - Location: `~/.codex/AGENTS.md` (or `$CODEX_HOME/AGENTS.md`)
   - An idempotent managed block bounded by `<!-- codex-antigravity:start -->` and `<!-- codex-antigravity:end -->` is inserted. Existing bytes, BOM, and CRLF line endings are strictly preserved.

## Installer CLI Options

| Option | Description |
|---|---|
| `--home <path>` | Specify a custom base user home directory (useful for testing, containerized setups, or sandboxes). |
| `--dry-run` | Preview all installation actions without writing or modifying any files. |
| `--backup` | Create timestamped backups (`*.backup-<timestamp>`) of modified files (default: enabled). |
| `--no-backup` | Disable automatic backup creation. |
| `--force` | Overwrite conflicting files that were not previously recorded in the manifest. |

### Example: Dry Run

To inspect what changes would occur before applying them:
```bash
python scripts/install.py --dry-run
```

### Example: Testing in a Temporary Directory

```bash
python scripts/install.py --home /tmp/test-home --dry-run
```

## Conflict Handling & Idempotence

The installer maintains a manifest (`.manifest.json`) in the runtime directory:
- If a target file already exists and matches the source hash, it is left untouched.
- If a target file exists and its hash matches the manifest entry from a previous installation, it is updated safely.
- If a target file exists and differs from both the source and the manifest (e.g. modified manually by the user or an external tool), preflight fails and reports a conflict. You must pass `--force` to intentionally overwrite it.
- Existing runtime state (`workspaces/` directory) and logs are never deleted or modified during installation or upgrades.

## Permission Configuration

`codex-antigravity-skills` does not silently enable broad permissions or auto-approval flags. All CLI operations are executed within the permission model enforced by your Antigravity CLI installation.

If you wish to allow narrow, non-destructive test commands for automated headless execution, configure an allowlist in `~/.gemini/antigravity-cli/settings.json` under `permissions.allow`, while preserving any existing settings in that file.

Supported configuration shape:
```json
{
  "permissions": {
    "allow": [
      "command(python --version)",
      "command(python -m unittest discover -s tests -v)",
      "command(python -m compileall -q bridge scripts tests)"
    ]
  }
}
```

See official references:
- [Antigravity CLI Headless Mode](https://www.antigravity.google/docs/cli/headless/)
- [Antigravity Permissions Reference](https://www.antigravity.google/docs/permissions/)

> **Warning**: Skill instructions are guidance for agent behavior and do not constitute security enforcement. Real security boundaries and command permissions are strictly enforced by the CLI execution sandbox.
