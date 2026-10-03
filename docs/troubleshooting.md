# Troubleshooting Guide

This guide covers common operational issues, diagnostics, and edge cases when running the Codex-Antigravity bridge.

## 1. Permission Denials & Headless Soft-Denials

### Symptom
The bridge exits with code `2` and status `"blocked"`, with `permission_notice: true` or `denied_actions` populated in `state.json`.

### Cause
In headless mode, Antigravity cannot prompt interactively for user confirmation. If an unapproved tool or command is invoked, the CLI logs a soft denial or populates `denied_actions` and exits.

### Resolution
1. Inspect the run's `stderr.log`:
   ```bash
   cat ~/.gemini/antigravity-cli/codex-bridge/workspaces/<hash>/runs/<latest>/stderr.log
   ```
2. Identify the denied command or tool.
3. If the command is safe and authorized for the task, add it to your project or user permissions allowlist in Antigravity configuration.
4. Issue a revision using `revise` with updated feedback explaining the newly authorized action.
5. **Never bypass permission boundaries**: Do not attempt to run denied commands via alternative shells or obfuscated scripts.

---

## 2. Windows Codex Sandbox Identity Separation

### Symptom
Antigravity commands fail with authentication or credential errors when executed by Codex inside a sandboxed environment on Windows, even though `agy` works in your normal user terminal.

### Cause
Windows sandbox isolation models often execute worker subprocesses under separate containerized identities or distinct token contexts. User-level credentials (e.g. stored in `AppData\Local` or Windows Credential Manager) may not be available to the sandboxed subprocess.

### Resolution
- Do not expect fully unattended headless operation in sandboxed environments where authentication tokens cannot be read.
- Launch the bridge from a standard integrated terminal session running under your authenticated user account.
- For testing, pass `--agy-executable` or point to an explicit mock/binary.

---

## 3. Stale Lock (`active.lock`)

### Symptom
Executing `dispatch`, `revise`, `retry`, or `accept` fails with:
```
ValueError: Workspace locked: .../active.lock. Verify stopped PIDs before recovering a stale lock
```

### Cause
A previous run crashed, was killed externally (e.g. `kill -9` or system restart), and could not run its cleanup handler.

### Resolution
1. Open the `active.lock` file to read the recorded PID:
   ```bash
   cat ~/.gemini/antigravity-cli/codex-bridge/workspaces/<hash>/active.lock
   ```
2. Verify if that PID is still running:
   - On Linux/macOS: `ps -p <PID>`
   - On Windows: `Get-Process -Id <PID> -ErrorAction SilentlyContinue`
3. If the process is still running, wait for it or terminate it cleanly.
4. If the process no longer exists, remove only that specific `active.lock` file:
   - On Linux/macOS: `rm ~/.gemini/antigravity-cli/codex-bridge/workspaces/<hash>/active.lock`
   - On Windows: `Remove-Item ~/.gemini/antigravity-cli/codex-bridge/workspaces/<hash>/active.lock`

---

## 4. Failed Starts & When to Use `retry` vs `revise`

### Rules
- **Use `revise`** whenever a conversation ID was established (`state["conversation_id"]` is non-empty). `revise` passes `--conversation <id>` to resume the exact session context.
- **Use `retry`** *only* when a run failed or was interrupted before any conversation ID was generated (e.g. invalid CLI arguments, missing executable, immediate startup crash).
- **Never blindly retry**: Both `revise` and `retry` require a `--feedback-file` documenting what happened and why the next run is expected to succeed.

---

## 5. Corrupted or Missing Worker Output

### Symptom
Bridge raises:
```
ValueError: AGY returned no report. Inspect result.json and stderr.log; completion has not been verified.
```

### Cause
The worker exited without writing structured output conforming to `report.schema.json`, or the response field was empty.

### Resolution
1. Review `runs/<latest>/result.json` to see raw output returned by the model.
2. Review `runs/<latest>/stderr.log` for Python exceptions, CLI panics, or timeouts.
3. Formulate specific feedback in a text file reminding the worker of its reporting contract.
4. Run `revise` with the feedback file.
