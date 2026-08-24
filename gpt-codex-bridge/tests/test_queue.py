from __future__ import annotations

import tempfile
from pathlib import Path
import sqlite3
import unittest

from bridge.queue import CodexExecAlreadyRunning, JobQueue
from bridge.sandbox import SandboxModeError


class QueueTests(unittest.TestCase):
    def test_queue_persists_and_recovers_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            queue = JobQueue(db)
            job = queue.submit(
                chat_id=123,
                prompt="run tests",
                workspace=Path(directory),
                sandbox_mode="read-only",
            )
            self.assertEqual(job.sandbox_mode, "read-only")
            claimed = queue.claim_next()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, job.id)
            self.assertEqual(queue.get(job.id).status, "running")

            restarted = JobQueue(db)
            self.assertEqual(restarted.requeue_running(), 1)
            recovered = restarted.get(job.id)
            self.assertEqual(recovered.status, "queued")
            self.assertEqual(recovered.sandbox_mode, "read-only")
            self.assertEqual(recovered.provider, "codex")
            self.assertEqual(restarted.claim_next().id, job.id)

    def test_legacy_schema_migrates_to_default_mode(self) -> None:
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
                    "VALUES ('job-0123456789abcdef', '123', 'old', ?, 'queued', 'now')",
                    (directory,),
                )
                connection.commit()

            queue = JobQueue(db)
            job = queue.get("job-0123456789abcdef")
            self.assertEqual(job.sandbox_mode, "workspace-write")
            self.assertEqual(job.provider, "codex")

    def test_provider_is_persisted_and_unknown_provider_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = queue.submit(
                chat_id=1,
                prompt="claude task",
                workspace=Path(directory),
                provider="claude",
            )
            self.assertEqual(queue.get(job.id).provider, "claude")
            with self.assertRaises(ValueError):
                queue.submit(
                    chat_id=1,
                    prompt="unknown task",
                    workspace=Path(directory),
                    provider="unknown",
                )

    def test_unknown_sandbox_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            with self.assertRaises(SandboxModeError):
                queue.submit(
                    chat_id=1,
                    prompt="unsafe mode",
                    workspace=Path(directory),
                    sandbox_mode="unrestricted",
                )

    def test_claim_next_enforces_concurrency_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            first = queue.submit(chat_id=1, prompt="first", workspace=Path(directory))
            second = queue.submit(chat_id=1, prompt="second", workspace=Path(directory))
            self.assertEqual(queue.claim_next().id, first.id)
            self.assertIsNone(queue.claim_next())
            queue.finish(first.id, succeeded=True, report_path=None, error=None)
            self.assertEqual(queue.claim_next().id, second.id)

    def test_workflow_rejects_when_codex_exec_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            running = queue.submit(chat_id=1, prompt="existing", workspace=Path(directory))
            self.assertEqual(queue.claim_next().id, running.id)

            with self.assertRaises(CodexExecAlreadyRunning) as raised:
                queue.submit_workflow(
                    chat_id=1,
                    prompt="new workflow",
                    workspace=Path(directory),
                )

            self.assertEqual([job.id for job in raised.exception.jobs], [running.id])
            self.assertEqual(queue.recent_workflows_for_chat(1), [])

    def test_result_is_scoped_to_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = queue.submit(chat_id=123, prompt="private task", workspace=Path(directory))
            self.assertIsNotNone(queue.get_for_chat(job.id, 123))
            self.assertIsNone(queue.get_for_chat(job.id, 999))


if __name__ == "__main__":
    unittest.main()
