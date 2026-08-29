from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from adapters.telegram import TelegramAdapter
from bridge.config import Settings
from bridge.queue import JobQueue


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def update(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": "42"}, "text": text}}


def routing_settings(
    directory: str, *, default_alias: str = "meeting-room"
) -> tuple[Settings, Path, Path]:
    root = Path(directory)
    bridge = root / "bridge"
    meeting_room = root / "ai-meeting-room"
    bridge.mkdir(exist_ok=True)
    meeting_room.mkdir(exist_ok=True)
    settings = Settings.from_env(
        {
            "CODEX_ALLOWED_WORKSPACES": os.pathsep.join([str(meeting_room), str(bridge)]),
            "CODEX_DEFAULT_WORKSPACE": str({"bridge": bridge, "meeting-room": meeting_room}[default_alias]),
            "CODEX_WORKSPACE_ALIASES": os.pathsep.join(
                [f"meeting-room={meeting_room}", f"bridge={bridge}"]
            ),
            "CODEX_BRIDGE_DATA_DIR": str(root / "state"),
            "TELEGRAM_BOT_TOKEN": "bot-secret",
            "TELEGRAM_ALLOWED_CHAT_ID": "42",
        },  # fmt: skip
        root_dir=Path(__file__).resolve().parents[1],
    )
    return settings, bridge.resolve(), meeting_room.resolve()


class TelegramWorkspaceRoutingTests(unittest.TestCase):
    """Telegram names no workspace, so it must inherit exactly the default route
    the HTTP API computes when a caller omits the alias."""

    def test_run_still_enqueues_on_the_default_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, _, meeting_room = routing_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())

            reply = adapter.handle_update(update("/run inspect telegram.py"))

            self.assertTrue(reply.startswith("queued job-"), reply)
            job = queue.claim_next()
            self.assertEqual(Path(job.workspace), meeting_room)
            self.assertEqual(job.sandbox_mode, "workspace-write")

    def test_gpt_workflow_keeps_publication_available_on_the_default_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, _, meeting_room = routing_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())

            reply = adapter.handle_update(update("/gpt inspect telegram.py"))

            self.assertTrue(reply.startswith("workflow queued flow-"), reply)
            workflow = queue.get_workflow(reply.split()[2])
            assert workflow is not None
            self.assertEqual(Path(workflow.workspace), meeting_room)
            self.assertTrue(workflow.external_publication_enabled)

    def test_run_full_is_still_permitted_on_the_legacy_default_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, _, meeting_room = routing_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())

            reply = adapter.handle_update(update("/run-full raise everything"))

            self.assertTrue(reply.startswith("queued job-"), reply)
            self.assertEqual(queue.claim_next().sandbox_mode, "danger-full-access")

    def test_workflow_denied_publication_when_default_workspace_is_the_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, bridge, _ = routing_settings(directory, default_alias="bridge")
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)

            reply = adapter.handle_update(update("/gpt edit the bridge"))

            self.assertIn("WORKSPACE_PUBLICATION_DENIED", reply)
            self.assertIn("workflow 未建立", reply)
            self.assertEqual(queue.counts(), {"queued": 0, "running": 0, "succeeded": 0, "failed": 0})

            contained = adapter.handle_update(
                update("/gpt --no-external-write edit the bridge")
            )
            self.assertTrue(contained.startswith("workflow queued flow-"), contained)
            workflow = queue.get_workflow(contained.split()[2])
            assert workflow is not None
            self.assertEqual(Path(workflow.workspace), bridge)
            self.assertFalse(workflow.external_publication_enabled)

    def test_run_full_denied_when_default_workspace_is_the_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, bridge, _ = routing_settings(directory, default_alias="bridge")
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())

            reply = adapter.handle_update(update("/run-full escalate"))

            self.assertIn("WORKSPACE_SANDBOX_DENIED", reply)
            self.assertIn("未建立 job", reply)
            self.assertEqual(queue.counts()["queued"], 0)
            self.assertEqual(queue.counts()["failed"], 0)

            contained = adapter.handle_update(update("/run inspect the bridge"))
            self.assertTrue(contained.startswith("queued job-"), contained)
            self.assertEqual(Path(queue.claim_next().workspace), bridge)

    def test_policy_refusal_replies_never_contain_a_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings, bridge, _ = routing_settings(directory, default_alias="bridge")
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)

            adapter.handle_update(update("/gpt edit the bridge"))
            adapter.handle_update(update("/run-full escalate"))

            self.assertEqual(len(client.sent), 2)
            for _, text in client.sent:
                self.assertNotIn(str(bridge), text)


if __name__ == "__main__":
    unittest.main()
