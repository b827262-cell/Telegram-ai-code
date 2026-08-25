from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from adapters.telegram import TelegramAdapter
from bridge.config import ConfigurationError, Settings
from bridge.queue import JobQueue


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def make_settings(directory: str, **extra: str) -> Settings:
    workspace = Path(directory) / "allowed-repo"
    workspace.mkdir()
    env = {
        "CODEX_ALLOWED_WORKSPACES": str(workspace),
        "CODEX_DEFAULT_WORKSPACE": str(workspace),
        "CODEX_BRIDGE_DATA_DIR": str(Path(directory) / "state"),
        "TELEGRAM_ALLOWED_CHAT_ID": "12345",
        **extra,
    }
    return Settings.from_env(env, root_dir=Path(__file__).resolve().parents[1])


def update(chat_id: int, text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class SecurityTests(unittest.TestCase):
    def test_unauthorized_message_is_blocked_before_parse_or_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            self.assertIsNone(adapter.handle_update(update(99999, "/run rm -rf /")))
            self.assertEqual(client.sent, [])
            self.assertEqual(queue.counts()["queued"], 0)

    def test_unauthorized_full_access_is_blocked_before_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            self.assertIsNone(adapter.handle_update(update(99999, "/run-full inspect")))
            self.assertEqual(queue.counts()["queued"], 0)

    def test_telegram_cannot_select_an_arbitrary_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            adapter.handle_update(update(12345, "/run work in /tmp/evil"))
            job = queue.claim_next()
            self.assertEqual(job.workspace, settings.default_workspace)
            self.assertIn("/tmp/evil", job.prompt)

    def test_multiple_workspaces_require_a_configured_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            env = {"CODEX_ALLOWED_WORKSPACES": f"{first}:{second}"}
            with self.assertRaises(ConfigurationError):
                Settings.from_env(env, root_dir=Path(__file__).resolve().parents[1])

    def test_raw_shell_command_is_not_an_adapter_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            adapter.handle_update(update(12345, "/shell rm -rf /"))
            self.assertEqual(queue.counts()["queued"], 0)
            self.assertIn("未知命令", client.sent[-1][1])

    def test_default_workspace_must_be_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowed = Path(directory) / "allowed"
            outside = Path(directory) / "outside"
            allowed.mkdir()
            outside.mkdir()
            env = {
                "CODEX_ALLOWED_WORKSPACES": str(allowed),
                "CODEX_DEFAULT_WORKSPACE": str(outside),
            }
            with self.assertRaises(ConfigurationError):
                Settings.from_env(env, root_dir=Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    unittest.main()
