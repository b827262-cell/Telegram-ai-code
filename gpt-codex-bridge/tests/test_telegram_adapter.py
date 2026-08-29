from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from adapters.telegram import TelegramAdapter, TelegramClient
from bridge.config import Settings
from bridge.queue import JobQueue


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class ExplodingMeetingClient:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Meeting Room should not be called for /ping: {name}")


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
        },
        root_dir=Path(__file__).resolve().parents[1],
    )


def update(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": 42}, "text": text}}


class TelegramAdapterTests(unittest.TestCase):
    def test_ping_replies_pong_without_queue_or_meeting_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(
                settings,
                queue,
                client,
                meeting_client=ExplodingMeetingClient(),
            )

            reply = adapter.handle_update(update("/ping"))

            self.assertEqual(reply, "PONG")
            self.assertEqual(client.sent, [("42", "PONG")])
            self.assertEqual(queue.counts(), {"queued": 0, "running": 0, "succeeded": 0, "failed": 0})

    def test_authorized_run_enqueues_without_starting_codex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            reply = adapter.handle_update(update("/run fix the pytest failure"))
            self.assertTrue(reply.startswith("queued job-"))
            self.assertEqual(queue.counts()["queued"], 1)
            self.assertIn("queued", client.sent[-1][1])
            self.assertEqual(queue.claim_next().sandbox_mode, "workspace-write")

    def test_authorized_run_reports_running_workspace_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            running = queue.submit(
                chat_id=42,
                prompt="existing workspace task",
                workspace=settings.default_workspace,
            )
            queue.claim_next()

            reply = adapter.handle_update(update("/run start another task"))

            self.assertIn("尚未派送", reply)
            self.assertIn(running.id, reply)
            self.assertEqual(queue.counts()["running"], 1)
            self.assertEqual(queue.counts()["queued"], 0)
            self.assertEqual(client.sent[-1], ("42", reply))

    def test_gpt_starts_automated_workflow_without_meeting_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(
                settings,
                queue,
                client,
                meeting_client=ExplodingMeetingClient(),
            )

            reply = adapter.handle_update(update("/gpt inspect telegram.py"))

            self.assertTrue(reply.startswith("workflow queued flow-"))
            workflow_id = reply.splitlines()[0].split()[-1]
            workflow = queue.get_workflow_for_chat(workflow_id, "42")
            self.assertIsNotNone(workflow)
            jobs = queue.workflow_jobs(workflow_id)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].workflow_stage, "gpt")
            self.assertEqual(jobs[0].prompt, "inspect telegram.py")
            self.assertEqual(jobs[0].sandbox_mode, "workspace-write")
            self.assertEqual(client.sent[-1], ("42", reply))

    def test_gpt_smoke_persists_no_external_write_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())

            reply = adapter.handle_update(update("/gpt-smoke inspect safely"))
            workflow_id = reply.splitlines()[0].split()[-1]
            workflow = queue.get_workflow(workflow_id)

            self.assertFalse(workflow.external_publication_enabled)
            self.assertIn("no-external-write", reply)

    def test_gpt_malformed_no_external_write_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())

            reply = adapter.handle_update(update("/gpt --no-external-write=false inspect"))

            self.assertIn("workflow 已拒絕", reply)
            self.assertEqual(queue.recent_workflows_for_chat("42"), [])

    def test_gpt_reports_running_codex_job_without_enqueuing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            running = queue.submit(chat_id=42, prompt="existing codex task", workspace=settings.default_workspace)
            queue.claim_next()

            reply = adapter.handle_update(update("/gpt start another workflow"))

            self.assertIn("Codex exec 執行中", reply)
            self.assertIn(running.id, reply)
            self.assertEqual(queue.recent_workflows_for_chat("42"), [])
            self.assertEqual(client.sent[-1], ("42", reply))

    def test_workflow_schedules_agy_then_claude_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, gpt_job = queue.submit_workflow(
                chat_id=42,
                prompt="inspect telegram.py",
                workspace=settings.default_workspace,
            )

            self.assertEqual(queue.claim_next().id, gpt_job.id)
            queue.finish(gpt_job.id, succeeded=True, report_path=None, error=None)
            workflow, agy_job, needs_report = queue.advance_workflow(gpt_job.id, succeeded=True)
            self.assertFalse(needs_report)
            self.assertEqual(agy_job.workflow_stage, "agy")
            self.assertEqual(workflow.current_stage, "agy")

            self.assertEqual(queue.claim_next().id, agy_job.id)
            queue.finish(agy_job.id, succeeded=True, report_path=None, error=None)
            workflow, claude_job, needs_report = queue.advance_workflow(agy_job.id, succeeded=True)
            self.assertFalse(needs_report)
            self.assertEqual(claude_job.workflow_stage, "claude")
            self.assertEqual(workflow.current_stage, "claude")

            self.assertEqual(queue.claim_next().id, claude_job.id)
            queue.finish(claude_job.id, succeeded=True, report_path=None, error=None)
            workflow, next_job, needs_report = queue.advance_workflow(claude_job.id, succeeded=True)
            self.assertTrue(needs_report)
            self.assertIsNone(next_job)
            self.assertEqual(workflow.current_stage, "github")

    def test_run_read_selects_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())
            adapter.handle_update(update("/run-read inspect files"))
            self.assertEqual(queue.claim_next().sandbox_mode, "read-only")

    def test_run_full_selects_danger_full_access_for_authorized_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())
            adapter.handle_update(update("/run-full inspect and repair"))
            self.assertEqual(queue.claim_next().sandbox_mode, "danger-full-access")

    def test_status_displays_job_sandbox_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            adapter = TelegramAdapter(settings, queue, FakeClient())
            adapter.handle_update(update("/run-read inspect files"))
            reply = adapter.handle_update(update("/status"))
            self.assertIn("sandbox_mode=read-only", reply)

    def test_result_is_human_readable_and_chat_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            client = FakeClient()
            adapter = TelegramAdapter(settings, queue, client)
            job = queue.submit(chat_id=42, prompt="task", workspace=settings.default_workspace)
            report_path = Path(directory) / "report.json"
            report_path.write_text(
                '{"status":"success","summary":"done","changed_files":["a.py"],'
                '"tests":[],"git_status":"","needs_attention":false,'
                '"sandbox_mode":"workspace-write"}',
                encoding="utf-8",
            )
            claimed = queue.claim_next()
            queue.finish(job.id, succeeded=True, report_path=report_path, error=None)
            reply = adapter.handle_update(update(f"/result {claimed.id}"))
            self.assertIn("summary: done", reply)
            self.assertIn("a.py", reply)
            self.assertIn("sandbox_mode: workspace-write", reply)

    def test_mock_telegram_api_uses_long_polling_methods(self) -> None:
        calls: list[tuple[str, dict]] = []

        def transport(method: str, payload: dict) -> dict:
            calls.append((method, payload))
            if method == "getUpdates":
                return {"ok": True, "result": []}
            return {"ok": True, "result": True}

        client = TelegramClient("bot-secret", transport=transport)
        client.delete_webhook()
        client.get_updates(offset=7, timeout=30)
        client.send_message("42", "queued")
        self.assertEqual([item[0] for item in calls], ["deleteWebhook", "getUpdates", "sendMessage"])
        self.assertEqual(calls[1][1]["offset"], 7)
        self.assertEqual(calls[1][1]["timeout"], 30)
        self.assertNotIn("bot-secret", repr(calls))


if __name__ == "__main__":
    unittest.main()
