from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from adapters.telegram import TelegramAdapter
from bridge.claude_runner import CLAUDE_EFFORT, CLAUDE_MODEL, ClaudeRunner
from bridge.config import Settings
from bridge.queue import JobQueue
from bridge.worker import Worker


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class ExplodingMeetingClient:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Meeting Room should not be called for /claude: {name}")


class FakeProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout.encode()
        self.stderr = stderr.encode()
        self.returncode: int | None = None
        self.final_returncode = returncode
        self.pid = 1

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        self.returncode = self.final_returncode
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.returncode = -9


def make_settings(directory: str) -> Settings:
    workspace = Path(directory) / "repo"
    workspace.mkdir()
    return Settings.from_env(
        {
            "CODEX_ALLOWED_WORKSPACES": str(workspace),
            "CODEX_DEFAULT_WORKSPACE": str(workspace),
            "CODEX_BRIDGE_DATA_DIR": str(Path(directory) / "state"),
            "TELEGRAM_BOT_TOKEN": "bot-secret",
            "TELEGRAM_ALLOWED_CHAT_ID": "42",
            "ANTHROPIC_API_KEY": "anthropic-secret",
        },
        root_dir=Path(__file__).resolve().parents[1],
    )


def update(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": 42}, "text": text}}


class ClaudeRouterTests(unittest.TestCase):
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

    def test_claude_constants(self) -> None:
        self.assertEqual(CLAUDE_MODEL, "claude-opus-5")
        self.assertEqual(CLAUDE_EFFORT, "medium")

    def test_claude_command_enqueues_claude_provider_without_meeting_room(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeTelegramClient()
            adapter = TelegramAdapter(
                settings,
                queue,
                client,
                meeting_client=ExplodingMeetingClient(),
            )

            reply = adapter.handle_update(update("/claude inspect the queue"))

            job = queue.claim_next()
            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job.provider, "claude")
            self.assertEqual(job.runner, "claude")
            self.assertEqual(job.prompt, "inspect the queue")
            self.assertIn("provider=claude runner=claude", reply)
            self.assertEqual(client.sent[-1][1], reply)

    def test_claude_runner_command_uses_print_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeTelegramClient())
            adapter.handle_update(update("/claude report status"))
            job = queue.claim_next()
            assert job is not None
            captured: dict[str, object] = {}

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                captured["argv"] = argv
                captured["kwargs"] = kwargs
                return FakeProcess(stdout="CLAUDE_OK")

            outcome = ClaudeRunner(settings, popen_factory=factory).run(job)

            argv = captured["argv"]
            self.assertEqual(argv[0], settings.sandbox_bwrap_bin)
            self.assertIn("--", argv)
            provider_argv = argv[argv.index("--") + 1 :]
            self.assertEqual(
                provider_argv,
                [
                    "claude",
                    "-p",
                    "report status",
                    "--model",
                    "claude-opus-5",
                    "--effort",
                    "medium",
                ],
            )
            self.assertEqual(captured["kwargs"]["cwd"], str(settings.default_workspace))
            self.assertFalse(captured["kwargs"]["shell"])
            self.assertNotIn("--dangerously-skip-permissions", argv)
            self.assertNotIn("--channels", argv)
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["summary"], "CLAUDE_OK")
            self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")

    def test_claude_runner_command_uses_explicit_model_and_effort_if_provided(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = queue.submit(
                chat_id="42",
                prompt="custom run",
                workspace=settings.default_workspace,
                provider="claude",
                model="claude-sonnet-4-6",
                effort="high",
            )
            runner = ClaudeRunner(settings)
            argv = runner.command_for(job)
            self.assertEqual(
                argv[argv.index("--") + 1 :],
                [
                    "claude",
                    "-p",
                    "custom run",
                    "--model",
                    "claude-sonnet-4-6",
                    "--effort",
                    "high",
                ],
            )

    def test_claude_runner_environment_and_credential_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            child_env = settings.claude_environment()
            self.assertNotIn("TELEGRAM_BOT_TOKEN", child_env)
            self.assertNotIn("MEETING_API_TOKEN", child_env)
            self.assertNotIn("MCP_BEARER_TOKEN", child_env)

    def test_worker_dispatches_claude_and_success_is_auto_notified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeTelegramClient()
            adapter = TelegramAdapter(settings, queue, client)
            adapter.handle_update(update("/claude Reply exactly CLAUDE_OK"))

            class ExplodingCodexRunner:
                def run(self, job: object) -> object:
                    raise AssertionError("Claude job must not use CodexRunner")

            claude = ClaudeRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(stdout="CLAUDE_OK"),
            )
            worker = Worker(queue, ExplodingCodexRunner(), claude_runner=claude)

            self.assertTrue(worker.run_once())
            self.assertEqual(adapter.drain_notifications(), 1)
            notification = client.sent[-1][1]
            self.assertIn("✅ Claude job completed", notification)
            self.assertIn("Provider: claude", notification)
            self.assertIn("Runner: claude", notification)
            self.assertIn("CLAUDE_OK", notification)

    def test_failed_claude_is_auto_notified_with_safe_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeTelegramClient()
            adapter = TelegramAdapter(settings, queue, client)
            adapter.handle_update(update("/claude trigger a failure"))
            claude = ClaudeRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(
                    stderr="failure contains bot-secret", returncode=7
                ),
            )
            worker = Worker(queue, object(), claude_runner=claude)

            self.assertTrue(worker.run_once())
            self.assertEqual(adapter.drain_notifications(), 1)
            notification = client.sent[-1][1]
            self.assertIn("❌ Claude job failed", notification)
            self.assertIn("Claude CLI failed (exit code 7): failure contains [REDACTED]", notification)
            self.assertNotIn("bot-secret", notification)

    def test_claude_long_result_is_split_for_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeTelegramClient()
            adapter = TelegramAdapter(settings, queue, client)
            adapter.handle_update(update("/claude produce a long report"))
            claude = ClaudeRunner(
                settings,
                popen_factory=lambda argv, **kwargs: FakeProcess(stdout="x" * 14000),
            )
            Worker(queue, object(), claude_runner=claude).run_once()

            self.assertEqual(adapter.drain_notifications(), 1)
            self.assertGreater(len(client.sent), 1)
            self.assertTrue(all(len(text) <= 4096 for _, text in client.sent))


if __name__ == "__main__":
    unittest.main()
