# Security Policy

## Security Model

`codex-antigravity-skills` coordinates tasks between Codex and Antigravity. It is essential to understand the distinction between instruction guidance and security enforcement:

1. **Instructions Are Behavioral Guidance**: Prompt templates and skill markdown files guide AI behavior, but do not provide cryptographic or sandbox security boundaries.
2. **CLI Permissions Are The Security Enforcement**: Permissions, command allowlists, file access boundaries, and network controls are enforced by the underlying Antigravity CLI and OS execution sandbox.
3. **No Automatic Privilege Escalation**: The bridge does not pass `--skip-permissions` or disable approval prompts by default. Any command execution is subject to user permission configuration.
4. **Credential Isolation**: Never commit API keys, personal access tokens, or account credentials into task definitions or repository tracking.

## Reporting Security Issues

If you discover a security vulnerability or privilege bypass in `codex-antigravity-skills`, please report it responsibly by contacting the project maintainers via a private vulnerability advisory or direct repository security contact. Please do not open public issues for sensitive security vulnerabilities until a fix has been prepared and released.
