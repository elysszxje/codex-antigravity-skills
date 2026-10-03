"""Installer for Codex-Antigravity Bridge and Skills.

Idempotently installs the bridge runtime, Codex skills, Antigravity worker skill,
and registers the working agreement in AGENTS.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_file_sha256(path: Path) -> str:
    return get_sha256(path.read_bytes())


def backup_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def render_agreement_block(bridge_path: Path, newline: str = "\n") -> str:
    lines = [
        "<!-- codex-antigravity:start -->",
        "## Codex and Antigravity working agreement",
        "The user's default workflow: Codex is the direct assistant responsible for planning, strategy, architecture and product review. Antigravity is the implementation worker and reports to Codex.",
        "For code implementation tasks, use codex-orchestrator and codex-reviewer skills. Invoke the worker bridge through the terminal:",
        f'python -X utf8 "{bridge_path.as_posix()}" <action> --workspace <project>',
        "Read durable workspace task state before starting or resuming work. Supply explicit scope and acceptance checks. Independently review actual changes and test evidence; worker completion is not acceptance. Preserve existing project instructions and user edits. Do not silently implement product code when Antigravity is unavailable; explain the concrete blocker. Codex may edit orchestration tooling.",
        "This agreement does not expand execution permissions or authorization for external actions.",
        "<!-- codex-antigravity:end -->",
    ]
    return newline.join(lines)


def plan_agents_md_update(
    agents_path: Path,
    bridge_path: Path,
) -> tuple[bool, bytes, str]:
    marker_start = "<!-- codex-antigravity:start -->"
    marker_end = "<!-- codex-antigravity:end -->"

    if not agents_path.exists():
        newline = os.linesep
        new_block = render_agreement_block(bridge_path, newline)
        encoded = (new_block + newline).encode("utf-8")
        return True, encoded, newline

    raw_bytes = agents_path.read_bytes()
    try:
        content = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Invalid UTF-8 encoding in {agents_path}: {exc}. "
            "Correct file encoding before running installer to prevent data corruption."
        ) from exc

    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    has_crlf = b"\r\n" in raw_bytes
    newline = "\r\n" if has_crlf else "\n"

    start_count = content.count(marker_start)
    end_count = content.count(marker_end)

    if start_count != end_count or start_count > 1:
        raise ValueError(
            f"Malformed or unmatched managed markers in {agents_path} "
            f"(start markers: {start_count}, end markers: {end_count}). "
            "Inspect file manually to prevent data loss."
        )

    new_block = render_agreement_block(bridge_path, newline)

    if start_count == 1:
        start_idx = content.find(marker_start)
        end_idx = content.find(marker_end)
        if end_idx < start_idx:
            raise ValueError(
                f"Reversed managed markers in {agents_path}. "
                "Inspect file manually to prevent data loss."
            )
        prefix = content[:start_idx]
        suffix = content[end_idx + len(marker_end) :]
        current_block = content[start_idx : end_idx + len(marker_end)]
        if current_block == new_block:
            return False, raw_bytes, newline
        new_content = prefix + new_block + suffix
    else:
        if content and not content.endswith(newline):
            new_content = content + newline + newline + new_block + newline
        elif content:
            new_content = content + newline + new_block + newline
        else:
            new_content = new_block + newline

    encoded = new_content.encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded

    changed = encoded != raw_bytes
    return changed, encoded, newline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home",
        type=Path,
        help="Base user home directory (useful for testing or custom install roots)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate installation without writing files",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create timestamped backups of modified existing files (default: True)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_false",
        dest="backup",
        help="Disable automatic backups",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite non-owned conflicting files",
    )
    return parser.parse_args(argv)


def install(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_home = args.home.resolve() if args.home else Path.home().resolve()

    if args.home:
        codex_home = base_home / ".codex"
    else:
        codex_home = (
            Path(os.environ["CODEX_HOME"]).resolve()
            if os.environ.get("CODEX_HOME")
            else (base_home / ".codex")
        )

    runtime_dir = base_home / ".gemini" / "antigravity-cli" / "codex-bridge"
    codex_skills_dir = base_home / ".agents" / "skills"
    agy_skills_dir = base_home / ".gemini" / "antigravity-cli" / "skills"

    # Preflight Phase 1: Validate manifest if present
    manifest_file = runtime_dir / ".manifest.json"
    old_manifest_files: dict[str, str] = {}
    if manifest_file.exists():
        try:
            raw_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                raise ValueError("Manifest root must be a JSON object")
            files_entry = raw_manifest.get("files")
            if not isinstance(files_entry, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in files_entry.items()
            ):
                raise ValueError(
                    "Manifest 'files' field must be a dictionary of string hashes"
                )
            old_manifest_files = files_entry
        except Exception as exc:
            print(
                f"Preflight error: Corrupted or invalid manifest at {manifest_file}: {exc}",
                file=sys.stderr,
            )
            return 1

    # Preflight Phase 2: Validate AGENTS.md instruction block and encoding
    agents_file = codex_home / "AGENTS.md"
    try:
        agents_changed, planned_agents_bytes, _ = plan_agents_md_update(
            agents_file, runtime_dir / "bridge.py"
        )
    except ValueError as exc:
        print(f"Preflight check failed: {exc}", file=sys.stderr)
        return 1

    # Preflight Phase 3: Check all source and destination targets
    file_targets: list[tuple[Path, Path]] = [
        (REPO_ROOT / "bridge" / "bridge.py", runtime_dir / "bridge.py"),
        (REPO_ROOT / "bridge" / "worker.md", runtime_dir / "worker.md"),
        (
            REPO_ROOT / "bridge" / "report.schema.json",
            runtime_dir / "report.schema.json",
        ),
        (
            REPO_ROOT / "skills" / "codex-orchestrator" / "SKILL.md",
            codex_skills_dir / "codex-orchestrator" / "SKILL.md",
        ),
        (
            REPO_ROOT / "skills" / "codex-reviewer" / "SKILL.md",
            codex_skills_dir / "codex-reviewer" / "SKILL.md",
        ),
        (
            REPO_ROOT / "skills" / "antigravity-worker" / "SKILL.md",
            agy_skills_dir / "antigravity-worker" / "SKILL.md",
        ),
    ]

    conflicts: list[str] = []
    actions_to_take: list[tuple[str, Path, Path]] = []

    for src, dst in file_targets:
        if not src.exists():
            print(f"Preflight error: Missing source file {src}", file=sys.stderr)
            return 1
        src_bytes = src.read_bytes()
        src_hash = get_sha256(src_bytes)

        if dst.exists():
            dst_bytes = dst.read_bytes()
            dst_hash = get_sha256(dst_bytes)
            if dst_hash == src_hash:
                continue

            rel_key = str(dst)
            is_owned = (
                rel_key in old_manifest_files
                and old_manifest_files[rel_key] == dst_hash
            )
            if not is_owned and not args.force:
                conflicts.append(
                    f"Destination exists and differs: {dst}. Use --force to overwrite non-owned files."
                )
            else:
                actions_to_take.append(("update", src, dst))
        else:
            actions_to_take.append(("create", src, dst))

    if conflicts:
        print("Preflight check failed with conflicts:", file=sys.stderr)
        for conf in conflicts:
            print(f"  - {conf}", file=sys.stderr)
        return 1

    # Preflight Phase 4: Dry-run exit
    if args.dry_run:
        print("[DRY RUN] Installation plan:")
        print(f"  Runtime Directory: {runtime_dir}")
        print(f"  Codex Skills Directory: {codex_skills_dir}")
        print(f"  Antigravity Skills Directory: {agy_skills_dir}")
        print(f"  Codex Home: {codex_home}")
        for action, src, dst in actions_to_take:
            print(f"  [{action.upper()}] {dst} (from {src.name})")
        if agents_changed:
            print(f"  [UPDATE] {agents_file} with managed instructions block")
        else:
            print(f"  [UNCHANGED] {agents_file}")
        return 0

    # Execution Phase: All validations passed, perform mutations atomically
    new_manifest_files = dict(old_manifest_files)

    for action, src, dst in actions_to_take:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and args.backup:
            backup_file(dst)
        temp_dst = dst.with_name(dst.name + ".tmp")
        shutil.copy2(src, temp_dst)
        os.replace(temp_dst, dst)
        new_manifest_files[str(dst)] = get_file_sha256(dst)
        print(f"Installed: {dst}")

    for src, dst in file_targets:
        if dst.exists():
            new_manifest_files[str(dst)] = get_file_sha256(dst)

    runtime_dir.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": new_manifest_files,
    }
    temp_manifest = manifest_file.with_name(manifest_file.name + ".tmp")
    temp_manifest.write_text(
        json.dumps(manifest_data, indent=2), encoding="utf-8"
    )
    os.replace(temp_manifest, manifest_file)

    if agents_changed:
        agents_file.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if agents_file.exists() and args.backup:
            backup_path = str(backup_file(agents_file))
        temp_agents = agents_file.with_name(agents_file.name + ".tmp")
        temp_agents.write_bytes(planned_agents_bytes)
        os.replace(temp_agents, agents_file)
        print(f"Updated global instructions in: {agents_file}")
        if backup_path:
            print(f"Backup created: {backup_path}")

    override_file = codex_home / "AGENTS.override.md"
    if override_file.exists() and override_file.read_text(
        encoding="utf-8-sig"
    ).strip():
        print(
            "WARNING: AGENTS.override.md overrides global AGENTS.md; merge the agreement into that override."
        )

    print("\nInstallation complete.")
    print("Start a new Codex session in VS Code. No repeated setup is needed.")
    return 0


if __name__ == "__main__":
    sys.exit(install())
