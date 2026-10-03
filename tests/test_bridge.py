"""Unit tests for bridge.py dispatch, reporting, isolation, resume, locking, and recovery."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import bridge.bridge as bridge

VALID_REPORT = {
    "status": "completed",
    "summary": "Implemented feature successfully",
    "changed_files": ["src/module.py"],
    "tests": [
        {
            "command": "python -m unittest discover -s tests -v",
            "outcome": "passed",
            "details": "10 tests passed",
        }
    ],
    "risks": [],
    "blockers": [],
}

VALID_TASK = {
    "objective": "Add retry mechanism with Vietnamese Unicode tiếng Việt & special chars $(pwd)",
    "scope": ["src/module.py", "tests/test_module.py"],
    "acceptance_checks": ["All unit tests pass"],
    "commands": ["python -m unittest discover -s tests -v"],
    "decisions": ["Use standard library only"],
}


class MockSubprocess:
    report = VALID_REPORT
    diagnostic = b""
    result_status = "SUCCESS"
    cli_exit = 0
    denied_actions = []
    last_args = []
    simulate_hang = False
    simulate_terminate_failure = False
    simulate_keyboard_interrupt = False

    def __init__(self, args, **kwargs):
        self.args = args
        type(self).last_args = args
        self.pid = 99999
        self.returncode = self.cli_exit
        self._stdout_file = kwargs.get("stdout")
        self._stderr_file = kwargs.get("stderr")

        if args and args[0] == "taskkill":
            if not type(self).simulate_terminate_failure:
                type(self).simulate_hang = False
                type(self).simulate_keyboard_interrupt = False
                self.returncode = 0
            else:
                self.returncode = 1
            return

        if not self.simulate_hang and not self.simulate_keyboard_interrupt:
            result = {
                "conversation_id": "test-session-uuid-1234",
                "status": self.result_status,
                "structured_output": self.report,
                "denied_actions": self.denied_actions,
            }
            if hasattr(self._stdout_file, "write"):
                events = (
                    json.dumps(
                        {"event": "init", "conversation_id": "test-session-uuid-1234"}
                    )
                    + "\n"
                    + "invalid non-json line to test malformed ndjson tolerance\n"
                    + json.dumps({"event": "result", "result": result})
                    + "\n"
                )
                self._stdout_file.write(events.encode("utf-8"))
                self._stdout_file.flush()

            if hasattr(self._stderr_file, "write") and self.diagnostic:
                self._stderr_file.write(self.diagnostic)
                self._stderr_file.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def communicate(self, input=None, timeout=None):
        return b"", b""

    def wait(self, timeout=None):
        if self.simulate_keyboard_interrupt:
            raise KeyboardInterrupt("Simulated user interrupt")
        if self.simulate_hang:
            raise bridge.subprocess.TimeoutExpired(self.last_args, timeout)
        return self.returncode

    def poll(self):
        return None if (self.simulate_hang or self.simulate_keyboard_interrupt) else self.returncode

    def kill(self):
        if not type(self).simulate_terminate_failure:
            type(self).simulate_hang = False
            type(self).simulate_keyboard_interrupt = False
            self.returncode = -9

    def terminate(self):
        if not type(self).simulate_terminate_failure:
            type(self).simulate_hang = False
            type(self).simulate_keyboard_interrupt = False
            self.returncode = -15


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace1 = self.root / "workspace one with spaces"
        self.workspace1.mkdir(parents=True)
        self.workspace2 = self.root / "workspace two with spaces"
        self.workspace2.mkdir(parents=True)

        self.state_dir = self.root / "custom_state"
        self.state_dir.mkdir(parents=True)

        self.task_file = self.root / "task.json"
        bridge.save(self.task_file, VALID_TASK)

        self.review_file = self.root / "review.txt"
        self.review_file.write_text("Codex reviewed diff and verified tests passed.", encoding="utf-8")

        self.feedback_file = self.root / "feedback.txt"
        self.feedback_file.write_text("Corrections needed for edge case.", encoding="utf-8")

        MockSubprocess.report = json.loads(json.dumps(VALID_REPORT))
        MockSubprocess.diagnostic = b""
        MockSubprocess.result_status = "SUCCESS"
        MockSubprocess.cli_exit = 0
        MockSubprocess.denied_actions = []
        MockSubprocess.simulate_hang = False
        MockSubprocess.simulate_terminate_failure = False
        MockSubprocess.simulate_keyboard_interrupt = False

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_bridge(self, action: str, workspace: Path, *extra_args: str) -> int:
        args = [
            action,
            "--workspace",
            str(workspace),
            "--state-dir",
            str(self.state_dir),
            "--agy-executable",
            sys.executable,
            *extra_args,
        ]
        with (
            patch.object(bridge, "snapshot", return_value={"git": "mocked"}),
            patch.object(bridge.subprocess, "Popen", MockSubprocess),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return bridge.main(args)

    def get_state(self, workspace: Path) -> dict:
        key = bridge.hashlib.sha256(
            os.path.normcase(str(workspace)).encode("utf-8")
        ).hexdigest()[:24]
        state_file = self.state_dir / key / "state.json"
        return bridge.read_json(state_file)

    def test_dispatch_and_accept_workflow(self):
        exit_code = self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.assertEqual(exit_code, 0)
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "awaiting_review")
        self.assertEqual(state["conversation_id"], "test-session-uuid-1234")

        # Accepting without review file fails
        with self.assertRaises(ValueError):
            self.run_bridge("accept", self.workspace1)

        # Accepting with review evidence succeeds
        exit_code = self.run_bridge("accept", self.workspace1, "--review-file", str(self.review_file))
        self.assertEqual(exit_code, 0)
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "accepted")
        self.assertIn("Codex reviewed", state["review"])

    def test_per_workspace_isolation(self):
        self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.run_bridge("dispatch", self.workspace2, "--task-file", str(self.task_file))

        state1 = self.get_state(self.workspace1)
        state2 = self.get_state(self.workspace2)

        self.assertNotEqual(state1["task_id"], state2["task_id"])
        self.assertEqual(state1["workspace"], str(self.workspace1))
        self.assertEqual(state2["workspace"], str(self.workspace2))

    def test_duplicate_locking_blocks_concurrent_dispatch(self):
        self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace1)).encode("utf-8")
        ).hexdigest()[:24]
        lock_file = self.state_dir / key / "active.lock"
        lock_file.write_text("12345", encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            self.run_bridge("revise", self.workspace1, "--feedback-file", str(self.feedback_file))
        self.assertIn("Workspace locked", str(ctx.exception))
        self.assertTrue(lock_file.exists())

    def test_lock_race_detects_updated_state_after_lock_acquired(self):
        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace1)).encode("utf-8")
        ).hexdigest()[:24]
        folder = self.state_dir / key
        folder.mkdir(parents=True, exist_ok=True)
        state_file = folder / "state.json"
        state_file.write_text(json.dumps({"workspace": str(self.workspace1), "status": "idle"}), encoding="utf-8")

        real_open = Path.open

        def hook_open(path_obj, mode="r", *args, **kwargs):
            handle = real_open(path_obj, mode, *args, **kwargs)
            if path_obj.name == "active.lock" and "x" in mode:
                state_file.write_text(
                    json.dumps({
                        "workspace": str(self.workspace1),
                        "status": "awaiting_review",
                        "task_id": "concurrent-uuid",
                    }),
                    encoding="utf-8",
                )
            return handle

        with patch.object(Path, "open", hook_open):
            with self.assertRaises(ValueError) as ctx:
                self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
            self.assertIn("Unresolved task", str(ctx.exception))

    def test_unicode_stdin_file_contains_full_prompt(self):
        self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        state = self.get_state(self.workspace1)
        run_dir = Path(state["run_dir"])
        stdin_content = json.loads((run_dir / "stdin.ndjson").read_text(encoding="utf-8"))
        prompt_text = stdin_content["message"]["content"]
        self.assertIn("tiếng Việt", prompt_text)
        self.assertIn("$(pwd)", prompt_text)
        self.assertNotIn(VALID_TASK["objective"], MockSubprocess.last_args)

    def test_revision_resumes_exact_conversation(self):
        self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.run_bridge("revise", self.workspace1, "--feedback-file", str(self.feedback_file))

        self.assertIn("--conversation", MockSubprocess.last_args)
        conv_idx = MockSubprocess.last_args.index("--conversation")
        self.assertEqual(MockSubprocess.last_args[conv_idx + 1], "test-session-uuid-1234")
        self.assertNotIn("--continue", MockSubprocess.last_args)

    def test_failed_report_check_blocks_acceptance(self):
        MockSubprocess.report["tests"][0]["outcome"] = "failed"
        exit_code = self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.assertEqual(exit_code, 2)
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "blocked")

        with self.assertRaises(ValueError):
            self.run_bridge("accept", self.workspace1, "--review-file", str(self.review_file))

    def test_deny_then_success_clears_stale_denied_actions(self):
        MockSubprocess.report = None
        MockSubprocess.denied_actions = [
            {"action": "run_command", "command": "restricted-cmd"}
        ]
        exit_code = self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.assertEqual(exit_code, 2)
        state1 = self.get_state(self.workspace1)
        self.assertEqual(state1["status"], "blocked")
        self.assertTrue(state1["permission_notice"])
        self.assertEqual(state1["denied_actions"], MockSubprocess.denied_actions)

        # Revised run succeeds without any denied actions
        MockSubprocess.report = VALID_REPORT
        MockSubprocess.denied_actions = []
        exit_code2 = self.run_bridge("revise", self.workspace1, "--feedback-file", str(self.feedback_file))
        self.assertEqual(exit_code2, 0)
        state2 = self.get_state(self.workspace1)
        self.assertEqual(state2["status"], "awaiting_review")
        self.assertFalse(state2["permission_notice"])
        self.assertNotIn("denied_actions", state2)

    def test_headless_success_with_empty_response_and_denied_actions_is_blocked(self):
        MockSubprocess.report = None
        MockSubprocess.denied_actions = [
            {"action": "run_command", "command": "rm -rf /"}
        ]
        exit_code = self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.assertEqual(exit_code, 2)
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "blocked")
        self.assertTrue(state["permission_notice"])
        self.assertEqual(state["denied_actions"], MockSubprocess.denied_actions)
        self.assertIn("permission denied", state["error"])
        self.assertNotIn("report", state)

    def test_empty_response_without_denied_actions_fails(self):
        MockSubprocess.report = None
        MockSubprocess.denied_actions = []
        with self.assertRaisesRegex(ValueError, "AGY returned no report"):
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "failed")

    def test_malformed_report_shape_fails(self):
        MockSubprocess.report = {"status": "completed"}
        with self.assertRaises(ValueError):
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "failed")

    def test_nonzero_exit_code_persisted(self):
        MockSubprocess.cli_exit = 5
        with self.assertRaises(ValueError):
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["exit_code"], 5)

    def test_missing_executable_preserves_task_in_fresh_workspace(self):
        nonexistent = self.root / "nonexistent_agy_binary"
        args = [
            "dispatch",
            "--workspace",
            str(self.workspace1),
            "--task-file",
            str(self.task_file),
            "--state-dir",
            str(self.state_dir),
            "--agy-executable",
            str(nonexistent),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError) as ctx:
                bridge.main(args)
            self.assertIn("not found", str(ctx.exception))

        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["task"], VALID_TASK)
        self.assertNotIn("conversation_id", state)
        self.assertIn("not found", state["error"])

        retry_args = [
            "retry",
            "--workspace",
            str(self.workspace1),
            "--feedback-file",
            str(self.feedback_file),
            "--state-dir",
            str(self.state_dir),
            "--agy-executable",
            sys.executable,
        ]
        with patch.object(bridge, "snapshot", return_value={"git": "mocked"}):
            with patch.object(bridge.subprocess, "Popen", MockSubprocess):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = bridge.main(retry_args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.get_state(self.workspace1)["status"], "awaiting_review")

    def test_missing_worker_template_preserves_task(self):
        with patch.object(bridge, "ROOT", self.root / "empty_dir"):
            args = [
                "dispatch",
                "--workspace",
                str(self.workspace1),
                "--task-file",
                str(self.task_file),
                "--state-dir",
                str(self.state_dir),
                "--agy-executable",
                sys.executable,
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(FileNotFoundError):
                    bridge.main(args)
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["task"], VALID_TASK)

    def test_retry_rules(self):
        self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        with self.assertRaises(ValueError):
            self.run_bridge("retry", self.workspace1, "--feedback-file", str(self.feedback_file))

        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace1)).encode("utf-8")
        ).hexdigest()[:24]
        state_file = self.state_dir / key / "state.json"
        state = self.get_state(self.workspace1)
        state.update(status="failed", conversation_id=None)
        bridge.save(state_file, state)

        exit_code = self.run_bridge("retry", self.workspace1, "--feedback-file", str(self.feedback_file))
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.get_state(self.workspace1)["status"], "awaiting_review")

    def test_timeout_cleanup_terminates_process(self):
        MockSubprocess.simulate_hang = True
        with self.assertRaises(TimeoutError):
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file), "--timeout", "1")
        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "interrupted")
        self.assertNotIn("pid", state)
        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace1)).encode("utf-8")
        ).hexdigest()[:24]
        lock_file = self.state_dir / key / "active.lock"
        self.assertFalse(lock_file.exists())

    def test_keyboard_interrupt_with_successful_termination_cleans_up(self):
        MockSubprocess.simulate_keyboard_interrupt = True
        with self.assertRaises(KeyboardInterrupt):
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))

        state = self.get_state(self.workspace1)
        self.assertEqual(state["task"], VALID_TASK)
        self.assertEqual(state["status"], "interrupted")
        self.assertNotIn("pid", state)

        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace1)).encode("utf-8")
        ).hexdigest()[:24]
        lock_file = self.state_dir / key / "active.lock"
        self.assertFalse(lock_file.exists())

    def test_termination_failure_preserves_pid_and_active_lock(self):
        MockSubprocess.simulate_hang = True
        MockSubprocess.simulate_terminate_failure = True

        with self.assertRaises(RuntimeError) as ctx:
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file), "--timeout", "1")
        self.assertIn("Failed to terminate worker process", str(ctx.exception))

        state = self.get_state(self.workspace1)
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["pid"], 99999)

        # Lock must be retained on disk because worker PID could not be confirmed stopped!
        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace1)).encode("utf-8")
        ).hexdigest()[:24]
        lock_file = self.state_dir / key / "active.lock"
        self.assertTrue(lock_file.exists())

        # Subsequent dispatch must be refused due to retained active.lock
        with self.assertRaises(ValueError) as lock_ctx:
            self.run_bridge("dispatch", self.workspace1, "--task-file", str(self.task_file))
        self.assertIn("Workspace locked", str(lock_ctx.exception))


class TestSubprocessHermetic(unittest.TestCase):
    """Hermetic tests using real Python subprocesses to verify pipe, timeout, and NDJSON behaviors."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "hermetic_ws"
        self.workspace.mkdir(parents=True)
        self.state_dir = self.root / "hermetic_state"
        self.state_dir.mkdir(parents=True)
        self.real_popen = subprocess.Popen

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_fake_cli(self, python_code: str) -> Path:
        launcher = self.root / "fake_cli.py"
        launcher.write_text(python_code, encoding="utf-8")
        return launcher

    def test_large_utf8_input_does_not_deadlock(self):
        cli_code = (
            "import sys, json\n"
            "data = sys.stdin.read()\n"
            "assert len(data) > 100000\n"
            "print(json.dumps({'event': 'init', 'conversation_id': 'hermetic-conv'}))\n"
            "report = {'status': 'completed', 'summary': 'ok', 'changed_files': [], "
            "'tests': [{'command': 'c', 'outcome': 'passed', 'details': 'd'}], 'risks': [], 'blockers': []}\n"
            "result = {'status': 'SUCCESS', 'conversation_id': 'hermetic-conv', 'structured_output': report}\n"
            "print(json.dumps({'event': 'result', 'result': result}))\n"
        )
        fake_cli = self.create_fake_cli(cli_code)

        large_objective = "Tiếng Việt có dấu " + ("A" * 150000)
        large_task = {
            "objective": large_objective,
            "scope": ["file.py"],
            "acceptance_checks": ["pass"],
            "commands": ["test"],
            "decisions": ["decision"],
        }
        task_file = self.root / "large_task.json"
        bridge.save(task_file, large_task)

        args = [
            "dispatch",
            "--workspace",
            str(self.workspace),
            "--task-file",
            str(task_file),
            "--state-dir",
            str(self.state_dir),
            "--agy-executable",
            sys.executable,
        ]

        def fake_popen(cmd_args, **kwargs):
            if cmd_args and cmd_args[0] not in ("taskkill", "git"):
                return self.real_popen([sys.executable, str(fake_cli), *cmd_args[1:]], **kwargs)
            return self.real_popen(cmd_args, **kwargs)

        with patch.object(bridge, "snapshot", return_value={"git": "mocked"}):
            with patch.object(bridge, "ROOT", self.root):
                shutil.copy2(Path(__file__).resolve().parent.parent / "bridge" / "worker.md", self.root / "worker.md")
                shutil.copy2(Path(__file__).resolve().parent.parent / "bridge" / "report.schema.json", self.root / "report.schema.json")
                with patch.object(bridge.subprocess, "Popen", fake_popen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        exit_code = bridge.main(args)
                    self.assertEqual(exit_code, 0)

    def test_large_utf8_prompt_to_nonreading_cli_times_out_and_cleans_up(self):
        cli_code = (
            "import time\n"
            "time.sleep(30)\n"
        )
        fake_cli = self.create_fake_cli(cli_code)

        large_objective = "Large prompt nonreading test " + ("X" * 150000)
        large_task = {
            "objective": large_objective,
            "scope": ["file.py"],
            "acceptance_checks": ["pass"],
            "commands": ["test"],
            "decisions": ["decision"],
        }
        task_file = self.root / "large_task.json"
        bridge.save(task_file, large_task)

        args = [
            "dispatch",
            "--workspace",
            str(self.workspace),
            "--task-file",
            str(task_file),
            "--state-dir",
            str(self.state_dir),
            "--agy-executable",
            sys.executable,
            "--timeout",
            "1",
        ]

        spawned_processes: list[subprocess.Popen] = []

        def fake_popen(cmd_args, **kwargs):
            if cmd_args and cmd_args[0] not in ("taskkill", "git"):
                proc = self.real_popen([sys.executable, str(fake_cli), *cmd_args[1:]], **kwargs)
                spawned_processes.append(proc)
                return proc
            return self.real_popen(cmd_args, **kwargs)

        with patch.object(bridge, "snapshot", return_value={"git": "mocked"}):
            with patch.object(bridge, "ROOT", self.root):
                shutil.copy2(Path(__file__).resolve().parent.parent / "bridge" / "worker.md", self.root / "worker.md")
                shutil.copy2(Path(__file__).resolve().parent.parent / "bridge" / "report.schema.json", self.root / "report.schema.json")
                with patch.object(bridge.subprocess, "Popen", fake_popen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        start_time = time.monotonic()
                        with self.assertRaises(TimeoutError):
                            bridge.main(args)
                        elapsed = time.monotonic() - start_time
                        self.assertLess(elapsed, 35.0)

        for proc in spawned_processes:
            self.assertIsNotNone(proc.poll())

        key = bridge.hashlib.sha256(
            os.path.normcase(str(self.workspace)).encode("utf-8")
        ).hexdigest()[:24]
        state_file = self.state_dir / key / "state.json"
        state = bridge.read_json(state_file)
        self.assertNotIn("pid", state)
        self.assertFalse((self.state_dir / key / "active.lock").exists())

    def test_cli_missing_result_event_raises_value_error(self):
        cli_code = (
            "import sys, json\n"
            "sys.stdin.read()\n"
            "print(json.dumps({'event': 'init', 'conversation_id': 'no-result-conv'}))\n"
            "sys.stderr.write('Diagnostic note in stderr\\n')\n"
        )
        fake_cli = self.create_fake_cli(cli_code)
        task_file = self.root / "task.json"
        bridge.save(task_file, VALID_TASK)

        args = [
            "dispatch",
            "--workspace",
            str(self.workspace),
            "--task-file",
            str(task_file),
            "--state-dir",
            str(self.state_dir),
            "--agy-executable",
            sys.executable,
        ]

        def fake_popen(cmd_args, **kwargs):
            if cmd_args and cmd_args[0] not in ("taskkill", "git"):
                return self.real_popen([sys.executable, str(fake_cli), *cmd_args[1:]], **kwargs)
            return self.real_popen(cmd_args, **kwargs)

        with patch.object(bridge, "snapshot", return_value={"git": "mocked"}):
            with patch.object(bridge, "ROOT", self.root):
                shutil.copy2(Path(__file__).resolve().parent.parent / "bridge" / "worker.md", self.root / "worker.md")
                shutil.copy2(Path(__file__).resolve().parent.parent / "bridge" / "report.schema.json", self.root / "report.schema.json")
                with patch.object(bridge.subprocess, "Popen", fake_popen):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaises(ValueError) as ctx:
                            bridge.main(args)
                    self.assertIn("No final result event in events.ndjson", str(ctx.exception))
                    self.assertIn("Diagnostic note in stderr", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
