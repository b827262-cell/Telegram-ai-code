from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from adapters.telegram import TelegramAdapter
from bridge.agy_runner import AGY_MODEL, AgyRunner
from bridge.config import Settings
from bridge.queue import JobQueue
from bridge.worker import Worker
from bridge.models import Job


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class ExplodingMeetingClient:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Meeting Room must not be called for /agy: {name}")


class FakeProcess:
    def __init__(
        self,
        stdout: str,
        *,
        stderr: str = "",
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self.stdout = stdout.encode()
        self.stderr = stderr.encode()
        self.final_returncode = returncode
        self.timeout = timeout
        self.returncode: int | None = None
        self.pid = os.getpid()
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("agy", timeout)
        self.returncode = -15 if self.killed else self.final_returncode
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def make_settings(directory: str, **extra: str) -> Settings:
    workspace = Path(directory) / "repo"
    workspace.mkdir()
    return Settings.from_env(
        {
            "CODEX_ALLOWED_WORKSPACES": str(workspace),
            "CODEX_DEFAULT_WORKSPACE": str(workspace),
            "CODEX_BRIDGE_DATA_DIR": str(Path(directory) / "state"),
            "TELEGRAM_BOT_TOKEN": "bot-secret",
            "TELEGRAM_ALLOWED_CHAT_ID": "42",
            "GEMINI_API_KEY": "gemini-key-secret",
            "GOOGLE_API_KEY": "google-key-secret",
            **extra,
        },
        root_dir=Path(__file__).resolve().parents[1],
    )


def agy_job(settings: Settings) -> Job:
    return Job(
        id="job-0123456789abcdef",
        chat_id="42",
        prompt="Reply exactly AGY_OAUTH_OK",
        workspace=settings.default_workspace,
        status="running",
        created_at="now",
        provider="agy",
    )


class AgyRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Plan building must not depend on the host having bwrap installed;
        # enforcement behavior itself is proven in test_sandbox_enforcement.py.
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch("bridge.sandbox._which", return_value="/usr/bin/bwrap")
        )
        stack.enter_context(
            patch(
                "bridge.sandbox._execute_probe",
                return_value=SimpleNamespace(returncode=0),
            )
        )

    def test_exact_argv_shell_false_and_api_keys_are_removed_from_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "gemini-key-secret",
                "GOOGLE_API_KEY": "google-key-secret",
                "GEMINI_MODEL": "must-not-select-agy-model",
            },
            clear=False,
        ):
            settings = make_settings(directory)
            settings.ensure_runtime_dirs()
            home = Path(directory) / "home"
            (home / ".gemini").mkdir(parents=True)
            (home / ".gemini" / "oauth_creds.json").write_text("credential")
            workspace = settings.default_workspace
            expected_prefix = [
                "bwrap",
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                "/tmp",
                "--bind",
                str(home / ".gemini"),
                str(home / ".gemini"),
                "--ro-bind",
                str(home / ".gemini" / "oauth_creds.json"),
                str(home / ".gemini" / "oauth_creds.json"),
                "--bind",
                str(workspace),
                str(workspace),
                "--die-with-parent",
                "--",
            ]
            captured: dict[str, object] = {}

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                captured["argv"] = argv
                captured["kwargs"] = kwargs
                return FakeProcess(json.dumps({"response": "AGY_OAUTH_OK"}))

            with patch.dict(os.environ, {"HOME": str(home)}):
                outcome = AgyRunner(settings, popen_factory=factory).run(
                    agy_job(settings)
                )

            self.assertEqual(
                captured["argv"],
                [
                    *expected_prefix,
                    "agy",
                    "-p",
                    "Reply exactly AGY_OAUTH_OK",
                    "--model",
                    AGY_MODEL,
                    "--output-format",
                    "json",
                ],
            )
            kwargs = captured["kwargs"]
            self.assertFalse(kwargs["shell"])
            self.assertNotIn("GEMINI_API_KEY", kwargs["env"])
            self.assertNotIn("GOOGLE_API_KEY", kwargs["env"])
            self.assertEqual(kwargs["cwd"], str(settings.default_workspace))
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["summary"], "AGY_OAUTH_OK")
            self.assertNotIn("gemini-key-secret", outcome.report_path.read_text())
            self.assertNotIn("google-key-secret", outcome.report_path.read_text())

    def test_nested_success_json_is_parsed_without_persisting_raw_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            settings.ensure_runtime_dirs()
            payload = {"type": "result", "result": {"text": "AGY_NESTED_OK", "token": "secret"}}
            outcome = AgyRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(json.dumps(payload)),
            ).run(agy_job(settings))
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["summary"], "AGY_NESTED_OK")
            self.assertNotIn('"token"', outcome.report_path.read_text())

    def test_malformed_json_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            settings.ensure_runtime_dirs()
            outcome = AgyRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess("not-json"),
            ).run(agy_job(settings))
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.report["summary"], "AGY CLI did not produce valid JSON")

    def test_nonzero_exit_is_failed_without_forwarding_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            settings.ensure_runtime_dirs()
            outcome = AgyRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(
                    "raw output GEMINI_API_KEY=gemini-key-secret",
                    stderr="provider failure GOOGLE_API_KEY=google-key-secret",
                    returncode=7,
                ),
            ).run(agy_job(settings))
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.exit_code, 7)
            self.assertEqual(outcome.report["summary"], "AGY CLI failed (exit code 7)")
            report_text = outcome.report_path.read_text()
            self.assertNotIn("gemini-key-secret", report_text)
            self.assertNotIn("google-key-secret", report_text)

    def test_timeout_terminates_process_and_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory, AGY_JOB_TIMEOUT_SECONDS="0.1")
            settings.ensure_runtime_dirs()
            processes: list[FakeProcess] = []

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                process = FakeProcess("{}", timeout=True)
                processes.append(process)
                return process

            killed: list[FakeProcess] = []

            def kill(process: FakeProcess) -> None:
                killed.append(process)
                process.killed = True

            outcome = AgyRunner(
                settings,
                popen_factory=factory,
                kill_process_group=kill,
            ).run(agy_job(settings))
            self.assertTrue(outcome.timed_out)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(killed, processes)
            self.assertEqual(outcome.report["summary"], "AGY job timed out")

    def test_auth_relogin_is_reported_as_blocked_auth_without_login_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            settings.ensure_runtime_dirs()
            outcome = AgyRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(
                    "", stderr="Please run agy login to authenticate", returncode=1
                ),
            ).run(agy_job(settings))
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.report["summary"], "BLOCKED_AUTH")

    def test_agy_job_dispatches_and_auto_notifies_then_result_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            settings.ensure_runtime_dirs()
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            telegram = FakeTelegramClient()
            adapter = TelegramAdapter(
                settings,
                queue,
                telegram,
                meeting_client=ExplodingMeetingClient(),
            )

            queued_reply = adapter.handle_update(
                {
                    "message": {
                        "chat": {"id": 42},
                        "text": "/agy Reply exactly AGY_OAUTH_E500_OK",
                    }
                }
            )
            self.assertTrue(queued_reply.startswith("queued job-"))
            queued_job = queue.recent_for_chat("42", limit=1)[0]
            job = queue.get(queued_job.id)
            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job.provider, "agy")

            agy = AgyRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(
                    json.dumps({"response": "AGY_OAUTH_E500_OK"})
                ),
            )
            worker = Worker(queue, object(), agy_runner=agy)
            self.assertTrue(worker.run_once())
            self.assertEqual(adapter.drain_notifications(), 1)
            notification = telegram.sent[-1][1]
            self.assertIn("✅ AGY job completed", notification)
            self.assertIn("AGY_OAUTH_E500_OK", notification)
            self.assertIn(f"/result {job.id}", notification)

            result = adapter.handle_update({"message": {"chat": {"id": 42}, "text": f"/result {job.id}"}})
            self.assertIn("summary: AGY_OAUTH_E500_OK", result)
            status = adapter.handle_update({"message": {"chat": {"id": 42}, "text": "/status"}})
            self.assertIn(f"{job.id}: succeeded provider=agy runner=agy", status)


if __name__ == "__main__":
    unittest.main()
