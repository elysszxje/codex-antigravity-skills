"""Unit tests for scripts/install.py."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import scripts.install as installer


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name).resolve()
        self.codex_home = self.home / ".codex"
        self.codex_home.mkdir(parents=True)
        self.runtime_dir = self.home / ".gemini" / "antigravity-cli" / "codex-bridge"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_installer(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        args = ["--home", str(self.home), *extra_args]
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = installer.install(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_leaves_filesystem_untouched(self):
        code, stdout, stderr = self.run_installer("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[DRY RUN]", stdout)
        self.assertFalse(self.runtime_dir.exists())
        self.assertFalse((self.codex_home / "AGENTS.md").exists())

    def test_fresh_installation_and_idempotence(self):
        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 0)
        self.assertTrue((self.runtime_dir / "bridge.py").exists())
        self.assertTrue((self.runtime_dir / ".manifest.json").exists())
        self.assertTrue((self.home / ".agents" / "skills" / "codex-orchestrator" / "SKILL.md").exists())
        self.assertTrue((self.home / ".gemini" / "antigravity-cli" / "skills" / "antigravity-worker" / "SKILL.md").exists())

        agents_file = self.codex_home / "AGENTS.md"
        self.assertTrue(agents_file.exists())
        agents_text = agents_file.read_text(encoding="utf-8")
        self.assertIn("<!-- codex-antigravity:start -->", agents_text)
        self.assertIn("<!-- codex-antigravity:end -->", agents_text)

        # Second run is completely idempotent
        code2, stdout2, stderr2 = self.run_installer()
        self.assertEqual(code2, 0)
        agents_text_after = agents_file.read_text(encoding="utf-8")
        self.assertEqual(agents_text, agents_text_after)

    def test_preserves_bom_and_crlf_and_unrelated_content(self):
        agents_file = self.codex_home / "AGENTS.md"
        original_content = (
            "Custom Prefix Rule\r\n"
            "<!-- codex-antigravity:start -->\r\n"
            "Old block to be replaced\r\n"
            "<!-- codex-antigravity:end -->\r\n"
            "Custom Suffix Rule\r\n"
        )
        agents_file.write_bytes(b"\xef\xbb\xbf" + original_content.encode("utf-8"))

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 0)

        updated_bytes = agents_file.read_bytes()
        self.assertTrue(updated_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", updated_bytes)
        updated_text = updated_bytes.decode("utf-8-sig")
        self.assertTrue(updated_text.startswith("Custom Prefix Rule\r\n"))
        self.assertTrue(updated_text.endswith("Custom Suffix Rule\r\n"))
        self.assertNotIn("Old block to be replaced", updated_text)
        self.assertIn("Codex and Antigravity working agreement", updated_text)

    def test_unmatched_managed_marker_start_raises_without_deleting(self):
        agents_file = self.codex_home / "AGENTS.md"
        malformed_content = (
            "# User rules\r\n"
            "<!-- codex-antigravity:start -->\r\n"
            "KEEP_THIS_UNRELATED_RULE\r\n"
        )
        original_bytes = malformed_content.encode("utf-8")
        agents_file.write_bytes(original_bytes)

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 1)
        self.assertIn("Malformed or unmatched managed markers", stderr)
        # Verify content was preserved exactly and not deleted
        self.assertEqual(agents_file.read_bytes(), original_bytes)
        # Verify runtime directory was NOT created
        self.assertFalse(self.runtime_dir.exists())

    def test_reversed_managed_markers_raises_without_deleting(self):
        agents_file = self.codex_home / "AGENTS.md"
        malformed_content = (
            "<!-- codex-antigravity:end -->\r\n"
            "<!-- codex-antigravity:start -->\r\n"
        )
        original_bytes = malformed_content.encode("utf-8")
        agents_file.write_bytes(original_bytes)

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 1)
        self.assertIn("Reversed managed markers", stderr)
        self.assertEqual(agents_file.read_bytes(), original_bytes)
        self.assertFalse(self.runtime_dir.exists())

    def test_invalid_utf8_agents_md_fails_preflight_without_partial_install(self):
        agents_file = self.codex_home / "AGENTS.md"
        bad_bytes = b"\xff\xfeinvalid utf8\x80\x81"
        agents_file.write_bytes(bad_bytes)

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 1)
        self.assertIn("Invalid UTF-8 encoding", stderr)
        self.assertFalse(self.runtime_dir.exists())
        self.assertEqual(agents_file.read_bytes(), bad_bytes)

    def test_corrupted_manifest_fails_preflight(self):
        self.runtime_dir.mkdir(parents=True)
        manifest_file = self.runtime_dir / ".manifest.json"
        manifest_file.write_text(json.dumps({"files": "invalid_not_dict"}), encoding="utf-8")

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 1)
        self.assertIn("Corrupted or invalid manifest", stderr)

    def test_preflight_conflict_without_force_fails(self):
        self.runtime_dir.mkdir(parents=True)
        foreign_bridge = self.runtime_dir / "bridge.py"
        foreign_bridge.write_text("print('foreign code')", encoding="utf-8")

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 1)
        self.assertIn("Preflight check failed with conflicts", stderr)
        self.assertIn("Use --force to overwrite", stderr)

        # With --force, installation succeeds
        code_force, stdout_force, stderr_force = self.run_installer("--force")
        self.assertEqual(code_force, 0)
        self.assertIn("Installed:", stdout_force)
        self.assertNotEqual(foreign_bridge.read_text(encoding="utf-8"), "print('foreign code')")

    def test_real_source_update_upgrades_cleanly(self):
        # Create a mock source repository checkout
        mock_repo = self.home / "mock_repo"
        shutil.copytree(installer.REPO_ROOT, mock_repo)

        with patch.object(installer, "REPO_ROOT", mock_repo):
            # Step 1: Initial clean install
            code, stdout, stderr = self.run_installer()
            self.assertEqual(code, 0)

            worker_target = self.runtime_dir / "worker.md"
            self.assertTrue(worker_target.exists())
            initial_content = worker_target.read_text(encoding="utf-8")

            # Step 2: Modify package source in mock_repo
            source_worker = mock_repo / "bridge" / "worker.md"
            source_worker.write_text(initial_content + "\n# Updated Rule v2\n", encoding="utf-8")

            # Step 3: Reinstall without --force; should succeed because destination matches manifest hash
            code2, stdout2, stderr2 = self.run_installer()
            self.assertEqual(code2, 0)
            self.assertIn("Installed:", stdout2)
            self.assertIn("Updated Rule v2", worker_target.read_text(encoding="utf-8"))

            # Step 4: Verify user-modified destination is rejected without --force
            worker_target.write_text("User manually modified this file", encoding="utf-8")
            code3, stdout3, stderr3 = self.run_installer()
            self.assertEqual(code3, 1)
            self.assertIn("Destination exists and differs", stderr3)

            # Step 5: Overwrite with --force succeeds
            code4, stdout4, stderr4 = self.run_installer("--force")
            self.assertEqual(code4, 0)
            self.assertIn("Updated Rule v2", worker_target.read_text(encoding="utf-8"))

    def test_warns_on_agents_override(self):
        override_file = self.codex_home / "AGENTS.override.md"
        override_file.write_text("Project specific rules override", encoding="utf-8")

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 0)
        self.assertIn("WARNING: AGENTS.override.md overrides global AGENTS.md", stdout)

    def test_runtime_state_workspaces_never_deleted(self):
        workspaces_dir = self.runtime_dir / "workspaces" / "project_hash"
        workspaces_dir.mkdir(parents=True)
        state_file = workspaces_dir / "state.json"
        state_file.write_text(json.dumps({"status": "running"}), encoding="utf-8")

        code, stdout, stderr = self.run_installer()
        self.assertEqual(code, 0)
        self.assertTrue(state_file.exists())
        self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["status"], "running")


if __name__ == "__main__":
    unittest.main()
