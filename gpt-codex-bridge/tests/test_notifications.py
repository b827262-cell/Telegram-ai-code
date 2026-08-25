from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from adapters.telegram import TelegramAdapter, TelegramClient
from bridge.config import Settings
from bridge.queue import JobQueue


class FakeNotificationClient:
    def __init__(self, *, failures: int = 0, error: str = "temporary network failure") -> None:
        self.failures = failures
        self.error = error
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError(self.error)
        self.sent.append((chat_id, text))


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


def finish_job(
    queue: JobQueue,
    settings: Settings,
    *,
    succeeded: bool,
    sandbox_mode: str = "workspace-write",
    error: str | None = None,
    exit_code: int = 0,
    report: dict | None = None,
):
    job = queue.submit(
        chat_id=42,
        prompt="test notification",
        workspace=settings.default_workspace,
        sandbox_mode=sandbox_mode,
    )
    assert queue.claim_next() is not None
    report_path = None
    if report is not None:
        report_path = queue.db_path.parent / f"{job.id}.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
    queue.finish(
        job.id,
        succeeded=succeeded,
        report_path=report_path,
        error=error,
        exit_code=exit_code,
    )
    return job


class NotificationOutboxTests(unittest.TestCase):
    def test_legacy_notification_schema_adds_dead_letter_marker_without_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.executescript(
                    """
                    CREATE TABLE jobs (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        workspace TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        report_path TEXT,
                        error TEXT
                    );
                    INSERT INTO jobs (
                        id, chat_id, prompt, workspace, status, created_at
                    ) VALUES (
                        'job-0123456789abcdef', '42', 'legacy', '/tmp', 'failed', 'old'
                    );
                    CREATE TABLE notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        event_type TEXT NOT NULL CHECK (event_type IN ('succeeded', 'failed')),
                        status TEXT NOT NULL CHECK (status IN ('pending', 'sent')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        sent_at TEXT,
                        last_error TEXT,
                        FOREIGN KEY (job_id) REFERENCES jobs(id),
                        UNIQUE (job_id, chat_id, event_type)
                    );
                    INSERT INTO notifications (
                        job_id, chat_id, event_type, status, attempts, created_at, last_error
                    ) VALUES (
                        'job-0123456789abcdef', '42', 'failed', 'pending', 9, 'old',
                        'HTTPError: HTTP Error 400: Bad Request'
                    );
                    """
                )

            queue = JobQueue(db)

            with sqlite3.connect(db) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(notifications)")
                }
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
                ).fetchone()[0]
            self.assertIn("dead_lettered_at", columns)
            self.assertIn("CHECK (status IN ('pending', 'sent'))", table_sql)
            notification = queue.get_notification(1)
            self.assertEqual(notification.status, "pending")
            self.assertEqual(notification.attempts, 9)
            self.assertEqual(
                notification.last_error,
                "HTTPError: HTTP Error 400: Bad Request",
            )

    def test_historical_terminal_jobs_are_not_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.execute(
                    """
                    CREATE TABLE jobs (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        workspace TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        report_path TEXT,
                        error TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO jobs (id, chat_id, prompt, workspace, status, created_at) "
                    "VALUES ('job-0123456789abcdef', '42', 'old', ?, 'succeeded', 'old')",
                    (directory,),
                )
                connection.commit()

            queue = JobQueue(db)
            self.assertEqual(queue.pending_notifications(), [])

    def test_succeeded_creates_exactly_one_pending_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(queue, settings, succeeded=True, exit_code=0)

            notifications = queue.pending_notifications()
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].job_id, job.id)
            self.assertEqual(notifications[0].event_type, "succeeded")
            self.assertEqual(notifications[0].status, "pending")
            self.assertFalse(
                queue.finish(
                    job.id,
                    succeeded=True,
                    report_path=None,
                    error=None,
                    exit_code=0,
                )
            )
            self.assertEqual(len(queue.pending_notifications()), 1)

    def test_failed_creates_exactly_one_pending_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=False,
                sandbox_mode="read-only",
                error="Codex job failed",
                exit_code=7,
            )

            notifications = queue.pending_notifications()
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].job_id, job.id)
            self.assertEqual(notifications[0].event_type, "failed")
            self.assertEqual(queue.get(job.id).status, "failed")

    def test_worker_restart_does_not_lose_pending_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            db = Path(directory) / "jobs.sqlite3"
            queue = JobQueue(db)
            finish_job(queue, settings, succeeded=True, exit_code=0)

            restarted_queue = JobQueue(db)
            notifications = restarted_queue.pending_notifications()
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].status, "pending")

    def test_telegram_success_marks_notification_sent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=True,
                sandbox_mode="read-only",
                exit_code=0,
                report={
                    "status": "success",
                    "summary": "implemented AUTO-RESULT-001",
                    "changed_files": ["adapters/telegram.py", "tests/test_notifications.py"],
                    "tests": [],
                    "git_status": "",
                    "needs_attention": False,
                    "sandbox_mode": "read-only",
                },
            )
            client = FakeNotificationClient()
            adapter = TelegramAdapter(settings, queue, client)

            self.assertEqual(adapter.drain_notifications(), 1)
            notification = queue.get_notification(1)
            self.assertEqual(notification.status, "sent")
            self.assertIsNotNone(notification.sent_at)
            self.assertEqual(queue.pending_notifications(), [])
            self.assertEqual(len(client.sent), 1)
            chat_id, text = client.sent[0]
            self.assertEqual(chat_id, "42")
            self.assertIn("✅ Codex job completed", text)
            self.assertIn(f"Job: {job.id}", text)
            self.assertIn("Status: succeeded", text)
            self.assertIn("Sandbox: read-only", text)
            self.assertIn("Result:\nimplemented AUTO-RESULT-001", text)
            self.assertIn("Changed files:\nadapters/telegram.py, tests/test_notifications.py", text)
            self.assertIn("Needs attention: no", text)
            self.assertIn("Exit code: 0", text)
            self.assertIn(f"/result {job.id}", text)

    def test_failed_notification_includes_failure_report_and_redacts_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=False,
                error="Codex failed while handling bot-secret",
                exit_code=7,
                report={
                    "status": "failed",
                    "summary": "failure summary contains bot-secret",
                    "changed_files": ["private-key-check.txt"],
                    "tests": [],
                    "git_status": "",
                    "needs_attention": True,
                    "sandbox_mode": "workspace-write",
                },
            )
            client = FakeNotificationClient()
            adapter = TelegramAdapter(settings, queue, client)

            self.assertEqual(adapter.drain_notifications(), 1)
            text = client.sent[0][1]
            self.assertIn("❌ Codex job failed", text)
            self.assertIn(f"Job: {job.id}", text)
            self.assertIn("Status: failed", text)
            self.assertIn("Result:\nfailure summary contains [REDACTED]", text)
            self.assertIn("Changed files:\nprivate-key-check.txt", text)
            self.assertIn("Needs attention: yes", text)
            self.assertIn("Error: Codex failed while handling [REDACTED]", text)
            self.assertNotIn("bot-secret", text)

    def test_operator_cancellation_notification_is_not_reported_as_provider_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=False,
                error=(
                    "CANCELLED_BY_OPERATOR: Prompt length boundary test only; "
                    "not authorized for execution."
                ),
                exit_code=None,
            )
            client = FakeNotificationClient()
            adapter = TelegramAdapter(settings, queue, client)

            self.assertEqual(adapter.drain_notifications(), 1)
            text = client.sent[0][1]
            self.assertIn("⏹️ Codex job cancelled", text)
            self.assertIn("Status: cancelled", text)
            self.assertIn("not authorized for execution", text)
            self.assertNotIn("❌ Codex job failed", text)
            self.assertNotIn("execution started", text)

    def test_notification_splits_long_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=True,
                report={
                    "status": "success",
                    "summary": "s" * 1000,
                    "changed_files": [f"{'x' * 300}-{index}.py" for index in range(10)],
                    "tests": [],
                    "git_status": "",
                    "needs_attention": False,
                    "sandbox_mode": "workspace-write",
                },
            )
            client = FakeNotificationClient()
            adapter = TelegramAdapter(settings, queue, client)

            self.assertEqual(adapter.drain_notifications(), 1)
            self.assertGreater(len(client.sent), 1)
            self.assertTrue(all(len(text) <= 4096 for _, text in client.sent))
            self.assertTrue(any(job.id in text for _, text in client.sent))

    def test_temporary_telegram_failure_remains_retryable_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=False,
                sandbox_mode="danger-full-access",
                error="failure contains bot-secret",
                exit_code=9,
            )
            client = FakeNotificationClient(
                failures=1,
                error="temporary failure contains bot-secret",
            )
            adapter = TelegramAdapter(settings, queue, client)

            self.assertEqual(adapter.drain_notifications(), 0)
            notification = queue.get_notification(1)
            self.assertEqual(notification.status, "pending")
            self.assertEqual(notification.attempts, 1)
            self.assertNotIn("bot-secret", notification.last_error)
            self.assertIn("[REDACTED]", notification.last_error)

            self.assertEqual(adapter.drain_notifications(), 1)
            self.assertEqual(queue.get_notification(1).status, "sent")
            self.assertNotIn("bot-secret", client.sent[0][1])
            self.assertIn("Sandbox: danger-full-access", client.sent[0][1])
            self.assertIn("Error: failure contains [REDACTED]", client.sent[0][1])

    def test_permanent_telegram_400_is_dead_lettered_and_stops_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            finish_job(queue, settings, succeeded=False, error="provider failed", exit_code=7)

            def transport(_method: str, _payload: dict) -> dict:
                return {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request contains bot-secret",
                }

            adapter = TelegramAdapter(
                settings,
                queue,
                TelegramClient("bot-secret", transport=transport),
            )

            self.assertEqual(adapter.drain_notifications(), 0)
            notification = queue.get_notification(1)
            self.assertEqual(notification.status, "dead_lettered")
            self.assertEqual(notification.attempts, 1)
            self.assertIsNotNone(notification.dead_lettered_at)
            self.assertEqual(queue.pending_notifications(), [])
            self.assertNotIn("bot-secret", notification.last_error)

            self.assertEqual(adapter.drain_notifications(), 0)
            self.assertEqual(queue.get_notification(1).attempts, 1)

    def test_telegram_429_remains_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            finish_job(queue, settings, succeeded=True, exit_code=0)
            client = TelegramClient(
                "bot-secret",
                transport=lambda _method, _payload: {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 30},
                },
            )

            self.assertEqual(TelegramAdapter(settings, queue, client).drain_notifications(), 0)
            notification = queue.get_notification(1)
            self.assertEqual(notification.status, "pending")
            self.assertEqual(notification.attempts, 1)
            self.assertIsNone(notification.dead_lettered_at)
            self.assertEqual(len(queue.pending_notifications()), 1)

    def test_telegram_5xx_and_network_failures_remain_retryable(self) -> None:
        for failure_kind in ("5xx", "network"):
            with self.subTest(failure_kind=failure_kind), tempfile.TemporaryDirectory() as directory:
                settings = make_settings(directory)
                queue = JobQueue(Path(directory) / "jobs.sqlite3")
                finish_job(queue, settings, succeeded=False, error="provider failed", exit_code=7)

                def transport(_method: str, _payload: dict) -> dict:
                    if failure_kind == "network":
                        raise OSError("network unavailable")
                    return {
                        "ok": False,
                        "error_code": 500,
                        "description": "Internal Server Error",
                    }

                adapter = TelegramAdapter(
                    settings,
                    queue,
                    TelegramClient("bot-secret", transport=transport),
                )
                self.assertEqual(adapter.drain_notifications(), 0)
                notification = queue.get_notification(1)
                self.assertEqual(notification.status, "pending")
                self.assertEqual(notification.attempts, 1)
                self.assertIsNone(notification.dead_lettered_at)
                self.assertEqual(len(queue.pending_notifications()), 1)

    def test_operator_dead_letter_preserves_legacy_audit_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = finish_job(
                queue,
                settings,
                succeeded=False,
                error="Codex runner failed unexpectedly",
                exit_code=-1,
            )
            diagnostic = "HTTPError: HTTP Error 400: Bad Request"
            queue.mark_notification_failed(1, diagnostic)
            before = queue.get_notification(1)

            repaired = queue.dead_letter_notification(
                1,
                increment_attempt=False,
                expected_job_id=job.id,
                expected_event_type="failed",
                expected_last_error=diagnostic,
            )

            self.assertEqual(repaired.status, "dead_lettered")
            self.assertEqual(repaired.attempts, before.attempts)
            self.assertEqual(repaired.created_at, before.created_at)
            self.assertEqual(repaired.last_error, before.last_error)
            self.assertEqual(queue.pending_notifications(), [])

    def test_adapter_restart_resends_pending_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = make_settings(directory)
            db = Path(directory) / "jobs.sqlite3"
            queue = JobQueue(db)
            job = finish_job(queue, settings, succeeded=True, exit_code=0)
            first_adapter = TelegramAdapter(settings, queue, FakeNotificationClient())
            self.assertEqual(first_adapter.queue.pending_notifications()[0].job_id, job.id)

            restarted_queue = JobQueue(db)
            restarted_client = FakeNotificationClient()
            restarted_adapter = TelegramAdapter(settings, restarted_queue, restarted_client)
            self.assertEqual(restarted_adapter.drain_notifications(), 1)
            self.assertEqual(restarted_queue.get_notification(1).status, "sent")
            self.assertEqual(len(restarted_client.sent), 1)


if __name__ == "__main__":
    unittest.main()
