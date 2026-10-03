"""Persistent Codex -> Antigravity bridge, Python standard library only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parent


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def safe_print(data: Any, ensure_ascii: bool = False, file=None) -> None:
    text = json.dumps(data, ensure_ascii=ensure_ascii, indent=2)
    target = file or sys.stdout
    try:
        print(text, file=target)
    except UnicodeEncodeError:
        print(json.dumps(data, ensure_ascii=True, indent=2), file=target)


def get_default_state_dir() -> Path:
    env_dir = os.environ.get("CODEX_AGY_STATE_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return Path.home() / ".gemini" / "antigravity-cli" / "codex-bridge" / "workspaces"


def resolve_agy_executable(custom: str | None = None) -> str:
    if custom:
        target = Path(custom).resolve()
        if target.is_file():
            return str(target)
        raise ValueError(f"Specified Antigravity executable not found: {custom}")

    env_exec = os.environ.get("CODEX_AGY_EXECUTABLE")
    if env_exec:
        target = Path(env_exec).resolve()
        if target.is_file():
            return str(target)
        raise ValueError(f"Environment CODEX_AGY_EXECUTABLE not found: {env_exec}")

    found = shutil.which("agy")
    if found and Path(found).is_file():
        return found

    if os.name == "nt":
        candidates = [
            Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "agy.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe",
            Path(os.environ.get("APPDATA", "")) / "npm" / "agy.cmd",
        ]
        for candidate in candidates:
            if candidate and str(candidate) != "." and candidate.is_file():
                return str(candidate)

    raise ValueError("Antigravity executable not found. Ensure 'agy' is on PATH or configure --agy-executable.")


def terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=15,
        )
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, BaseException):
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=10)
        except (subprocess.TimeoutExpired, BaseException):
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass

    if process.poll() is None:
        raise RuntimeError(
            f"Failed to terminate worker process PID={process.pid}. "
            "Worker may still be running; verify PID and recover lock manually."
        )


def snapshot(workspace: Path) -> dict[str, Any]:
    if not shutil.which("git"):
        return {"available": False}
    result: dict[str, Any] = {}
    for key, args in (
        ("head", ["rev-parse", "HEAD"]),
        ("status", ["status", "--porcelain=v1"]),
        ("diff", ["diff", "--no-ext-diff"]),
        ("staged_diff", ["diff", "--cached", "--no-ext-diff"]),
    ):
        try:
            run = subprocess.run(
                ["git", *args],
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            result[key] = {
                "exit_code": run.returncode,
                "output": run.stdout,
                "error": run.stderr,
            }
        except subprocess.TimeoutExpired:
            result[key] = {"error": "snapshot timed out"}
        except OSError as err:
            result[key] = {"error": f"git execution failed: {err}"}
    return result


def validate_task(task: Any) -> None:
    if (
        not isinstance(task, dict)
        or not isinstance(task.get("objective"), str)
        or not task["objective"].strip()
    ):
        raise ValueError("Task requires a nonempty objective")
    for key in ("scope", "acceptance_checks", "commands", "decisions"):
        if not isinstance(task.get(key), list) or not all(
            isinstance(x, str) for x in task[key]
        ):
            raise ValueError(f"Task requires {key}: array of strings")
    if not task["scope"] or not task["acceptance_checks"]:
        raise ValueError("scope and acceptance_checks must not be empty")


def validate_report(report: Any) -> None:
    required = {
        "status",
        "summary",
        "changed_files",
        "tests",
        "risks",
        "blockers",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("Invalid worker report shape")
    if report["status"] not in ("completed", "blocked") or not isinstance(
        report["summary"], str
    ):
        raise ValueError("Invalid report status/summary")
    for key in ("changed_files", "risks", "blockers"):
        if not isinstance(report[key], list) or not all(
            isinstance(x, str) for x in report[key]
        ):
            raise ValueError(f"Invalid report {key}")
    if not isinstance(report["tests"], list):
        raise ValueError("Invalid tests")
    for test in report["tests"]:
        if (
            not isinstance(test, dict)
            or set(test) != {"command", "outcome", "details"}
            or not all(isinstance(x, str) for x in test.values())
            or test["outcome"] not in ("passed", "failed", "blocked", "not_run")
        ):
            raise ValueError("Invalid test evidence")


def run_worker(
    workspace: Path,
    folder: Path,
    state: dict[str, Any],
    task: dict[str, Any],
    feedback: str | None,
    timeout: int,
    executable_override: str | None = None,
) -> None:
    # Clear per-run mutable fields so stale status/denied_actions do not leak
    state.pop("error", None)
    state.pop("report", None)
    state.pop("denied_actions", None)
    state.pop("permission_notice", None)
    state.pop("exit_code", None)
    state.pop("pid", None)

    run_dir: Path | None = None
    process: subprocess.Popen[Any] | None = None

    try:
        executable = resolve_agy_executable(executable_override)
        worker_template = ROOT / "worker.md"
        if not worker_template.exists():
            raise FileNotFoundError(f"Worker contract template missing: {worker_template}")
        prompt = worker_template.read_text(encoding="utf-8")

        schema_path = ROOT / "report.schema.json"
        if not schema_path.exists():
            raise FileNotFoundError(f"Report schema file missing: {schema_path}")

        run_dir = (
            folder
            / "runs"
            / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                + "-"
                + uuid4().hex[:8]
            )
        )
        run_dir.mkdir(parents=True)
        prompt += (
            "\nWorkspace: "
            + str(workspace)
            + "\nTask from Codex:\n"
            + json.dumps(task, ensure_ascii=False)
        )
        if feedback:
            prompt += (
                "\nCodex review corrections within the original scope:\n" + feedback
            )
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        save(run_dir / "before.json", snapshot(workspace))

        # Write stdin message to a file to prevent pipe write deadlocks with large inputs on all OS/Python versions
        message = {"event": "user", "message": {"content": prompt}}
        stdin_file_path = run_dir / "stdin.ndjson"
        stdin_file_path.write_bytes(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )

        args = [
            executable,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--json-schema",
            str(schema_path),
            "--mode",
            "accept-edits",
            "--print-timeout",
            f"{timeout}s",
        ]
        if feedback:
            args += ["--conversation", state["conversation_id"]]

        state.update(status="running", run_dir=str(run_dir), updated_at=now())
        save(folder / "state.json", state)

        popen_kwargs: dict[str, Any] = {
            "cwd": workspace,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        wait_timeout = timeout + (15 if timeout >= 15 else 2)

        with (
            stdin_file_path.open("rb") as stdin_handle,
            (run_dir / "events.ndjson").open("wb") as stdout,
            (run_dir / "stderr.log").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                args, stdin=stdin_handle, stdout=stdout, stderr=stderr, **popen_kwargs
            )
            state["pid"] = process.pid
            save(folder / "state.json", state)
            try:
                process.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                terminate_process(process)
                raise TimeoutError(
                    f"Antigravity worker timed out after {timeout}s"
                )
            except BaseException:
                terminate_process(process)
                raise

        state["exit_code"] = process.returncode
        result = None
        events_path = run_dir / "events.ndjson"
        if events_path.exists():
            for line in events_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "init":
                    state["conversation_id"] = event.get("conversation_id")
                if event.get("event") == "result":
                    result = event.get("result")

        if not isinstance(result, dict):
            stderr_file = run_dir / "stderr.log"
            diag = (
                stderr_file.read_text(encoding="utf-8", errors="replace")
                if stderr_file.exists()
                else ""
            )
            raise ValueError(
                f"No final result event in events.ndjson (exit code={process.returncode}). "
                f"Stderr: {diag.strip() or 'None'}. Inspect stderr.log/events.ndjson; do not retry blindly"
            )

        save(run_dir / "result.json", result)
        state["conversation_id"] = result.get("conversation_id") or state.get(
            "conversation_id"
        )
        if process.returncode != 0 or result.get("status") != "SUCCESS":
            stderr_file = run_dir / "stderr.log"
            diag = (
                stderr_file.read_text(encoding="utf-8", errors="replace")
                if stderr_file.exists()
                else ""
            )
            raise ValueError(
                f"Antigravity failed: exit={process.returncode}, status={result.get('status')}; "
                f"stderr: {diag.strip() or 'None'}"
            )

        stderr_file = run_dir / "stderr.log"
        diagnostic = (
            stderr_file.read_text(encoding="utf-8", errors="replace")
            if stderr_file.exists()
            else ""
        )
        denied = result.get("denied_actions", [])
        if denied or re.search(
            r"auto.denied|soft.denied|permission.denied|requires approval",
            diagnostic,
            re.I,
        ):
            state.update(
                status="blocked",
                permission_notice=True,
                denied_actions=denied,
                error="AGY tool permission denied in headless mode; inspect stderr.log and grant only the authorized command before revising.",
            )
            state.pop("report", None)
            return

        report = result.get("structured_output")
        if report is None:
            response = result.get("response", "")
            if not isinstance(response, str) or not response.strip():
                raise ValueError(
                    "AGY returned no report. Inspect result.json and stderr.log; completion has not been verified."
                )
            try:
                report = json.loads(response)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AGY response could not be parsed as JSON: {exc}. Inspect result.json and stderr.log."
                ) from exc

        validate_report(report)
        save(run_dir / "report.json", report)
        state["report"] = report
        state["permission_notice"] = bool(
            re.search(
                r"soft.denied|permission.denied|not allowed|requires approval",
                diagnostic,
                re.I,
            )
        )
        incomplete = (
            report["status"] == "blocked"
            or bool(report["blockers"])
            or any(
                test["outcome"] in ("failed", "blocked", "not_run")
                for test in report["tests"]
            )
        )
        state["status"] = (
            "blocked"
            if incomplete or state["permission_notice"]
            else "awaiting_review"
        )
        state.pop("error", None)
    except BaseException as exc:
        state["status"] = (
            "interrupted"
            if isinstance(exc, (KeyboardInterrupt, TimeoutError))
            or "timed out" in str(exc).lower()
            or "terminate worker process" in str(exc).lower()
            else "failed"
        )
        state["error"] = str(exc) or type(exc).__name__
        if run_dir and (run_dir / "events.ndjson").exists():
            for line in (run_dir / "events.ndjson").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("event") == "init":
                        state["conversation_id"] = event.get("conversation_id")
                except json.JSONDecodeError:
                    continue
        save(folder / "state.json", state)
        raise
    finally:
        if process is not None and process.poll() is not None:
            state.pop("pid", None)
        state["updated_at"] = now()
        if run_dir and run_dir.exists():
            save(run_dir / "after.json", snapshot(workspace))
        save(folder / "state.json", state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("dispatch", "revise", "retry", "status", "collect", "accept"),
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-file")
    parser.add_argument("--feedback-file")
    parser.add_argument(
        "--review-file",
        help="Codex review evidence required for acceptance",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--agy-executable",
        help="Path to agy executable (overrides PATH and defaults)",
    )
    parser.add_argument(
        "--state-dir",
        help="Directory to store persistent state outside repo",
    )
    options = parser.parse_args(argv)
    workspace = Path(options.workspace).resolve(strict=True)
    if not workspace.is_dir() or options.timeout < 1:
        raise ValueError(
            "Workspace must be a directory and timeout must be positive"
        )

    state_root = (
        Path(options.state_dir).resolve()
        if options.state_dir
        else get_default_state_dir()
    )
    key = hashlib.sha256(
        os.path.normcase(str(workspace)).encode("utf-8")
    ).hexdigest()[:24]
    folder = state_root / key
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"

    # Read-only actions do not require locking
    if options.action in ("status", "collect"):
        state = (
            read_json(state_path)
            if state_path.exists()
            else {"workspace": str(workspace), "status": "idle"}
        )
        safe_print({**state, "state_dir": str(folder)})
        return 0

    # Mutating actions acquire lock first before reading mutable state
    lock = folder / "active.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError(
            f"Workspace locked: {lock}. Verify stopped PIDs before recovering a stale lock"
        ) from None

    state: dict[str, Any] = {}
    preserve_lock = False

    try:
        handle.write(str(os.getpid()))
        handle.flush()

        # Re-read state under the lock to prevent lock race
        state = (
            read_json(state_path)
            if state_path.exists()
            else {"workspace": str(workspace), "status": "idle"}
        )

        if options.action == "dispatch":
            if state["status"] not in ("idle", "accepted"):
                raise ValueError(
                    "Unresolved task: review/accept or revise before dispatching another"
                )
            if not options.task_file:
                raise ValueError("dispatch requires --task-file")
            task = read_json(Path(options.task_file))
            validate_task(task)
            if state_path.exists():
                save(folder / "archive" / (uuid4().hex + ".json"), state)
            state = {
                "workspace": str(workspace),
                "task_id": uuid4().hex,
                "task": task,
                "created_at": now(),
                "status": "idle",
            }
            # Save initialized state immediately so failed launch attempts preserve task metadata
            save(state_path, state)
            run_worker(
                workspace,
                folder,
                state,
                task,
                None,
                options.timeout,
                options.agy_executable,
            )
        elif options.action == "retry":
            if (
                state["status"] not in ("failed", "interrupted")
                or state.get("conversation_id")
                or not state.get("task")
            ):
                raise ValueError(
                    "retry is only for failed starts without conversation ID; otherwise use revise"
                )
            if not options.feedback_file:
                raise ValueError(
                    "retry requires --feedback-file documenting diagnosis and inspection of partial changes"
                )
            note = Path(options.feedback_file).read_text(encoding="utf-8-sig")
            if not note.strip():
                raise ValueError("Retry diagnosis must not be empty")
            state["retry_diagnosis"] = note
            run_worker(
                workspace,
                folder,
                state,
                state["task"],
                None,
                options.timeout,
                options.agy_executable,
            )
        elif options.action == "revise":
            if (
                not options.feedback_file
                or not state.get("conversation_id")
                or not state.get("task")
            ):
                raise ValueError(
                    "revise requires --feedback-file and an existing conversation/task"
                )
            if state["status"] in ("accepted", "running"):
                raise ValueError("Cannot revise an accepted or running task")
            feedback = Path(options.feedback_file).read_text(
                encoding="utf-8-sig"
            )
            if not feedback.strip():
                raise ValueError("Feedback must not be empty")
            run_worker(
                workspace,
                folder,
                state,
                state["task"],
                feedback,
                options.timeout,
                options.agy_executable,
            )
        else:
            if state["status"] != "awaiting_review" or not options.review_file:
                raise ValueError(
                    "accept requires awaiting_review and --review-file"
                )
            review = Path(options.review_file).read_text(encoding="utf-8-sig")
            if not review.strip():
                raise ValueError("Review evidence must not be empty")
            state.update(status="accepted", review=review, updated_at=now())
            save(state_path, state)
    except BaseException:
        current_state = read_json(state_path) if state_path.exists() else state
        if current_state.get("pid"):
            preserve_lock = True
        raise
    finally:
        try:
            handle.close()
        except Exception:
            pass
        if not preserve_lock and lock.exists():
            try:
                lock.unlink()
            except OSError:
                pass

    safe_print({**state, "state_dir": str(folder)})
    return 0 if state["status"] in ("accepted", "awaiting_review") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError, RuntimeError, TimeoutError) as error:
        safe_print({"error": str(error)}, ensure_ascii=False, file=sys.stderr)
        sys.exit(1)
