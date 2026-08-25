from __future__ import annotations

import tempfile
from pathlib import Path
import sqlite3
import threading
import unittest

from bridge.queue import (
    CANCELLED_BY_OPERATOR_PREFIX,
    CodexExecAlreadyRunning,
    JobQueue,
    QueueTransitionConflict,
)
from bridge.sandbox import SandboxModeError
from bridge.worker import Worker


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

    def test_reconcile_stale_failed_job_moves_queued_workflow_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            queue = JobQueue(db)
            workflow, job = queue.submit_workflow(
                chat_id=123,
                prompt="preserve this historical prompt",
                workspace=Path(directory),
            )
            self.assertEqual(queue.claim_next().id, job.id)
            queue.finish(
                job.id,
                succeeded=False,
                report_path=None,
                error="Codex runner failed unexpectedly",
                exit_code=-1,
            )
            with sqlite3.connect(db) as connection:
                connection.execute(
                    "UPDATE workflows SET status = 'queued' WHERE id = ?",
                    (workflow.id,),
                )

            reconciled, terminal_job = queue.reconcile_failed_workflow(
                workflow.id,
                expected_job_id=job.id,
                expected_job_error="Codex runner failed unexpectedly",
                expected_exit_code=-1,
            )

            self.assertEqual(reconciled.status, "failed")
            self.assertEqual(reconciled.current_stage, "gpt")
            self.assertEqual(reconciled.finished_at, terminal_job.finished_at)
            self.assertEqual(
                reconciled.error,
                "gpt stage failed: Codex runner failed unexpectedly",
            )
            self.assertEqual(reconciled.prompt, "preserve this historical prompt")
            self.assertEqual(terminal_job, queue.get(job.id))
            self.assertEqual(terminal_job.status, "failed")
            self.assertEqual(terminal_job.exit_code, -1)
            self.assertEqual(len(queue.pending_notifications()), 1)

    def test_reconcile_failed_workflow_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, job = queue.submit_workflow(
                chat_id=123,
                prompt="idempotent history",
                workspace=Path(directory),
            )
            self.assertEqual(queue.claim_next().id, job.id)
            queue.finish(
                job.id,
                succeeded=False,
                report_path=None,
                error="provider failed",
                exit_code=7,
            )

            first, first_job = queue.reconcile_failed_workflow(workflow.id)
            second, second_job = queue.reconcile_failed_workflow(workflow.id)

            self.assertEqual(second, first)
            self.assertEqual(second_job, first_job)
            self.assertEqual(queue.workflow_jobs(workflow.id), [first_job])
            self.assertEqual(len(queue.pending_notifications()), 1)

    def test_reconcile_failed_workflow_rejects_unsafe_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, job = queue.submit_workflow(
                chat_id=123,
                prompt="must remain unchanged",
                workspace=Path(directory),
            )
            self.assertEqual(queue.claim_next().id, job.id)
            queue.finish(
                job.id,
                succeeded=False,
                report_path=None,
                error="provider failed",
                exit_code=7,
            )

            with self.assertRaises(QueueTransitionConflict):
                queue.reconcile_failed_workflow(
                    workflow.id,
                    expected_job_id="job-ffffffffffffffff",
                )

            unchanged_workflow = queue.get_workflow(workflow.id)
            unchanged_job = queue.get(job.id)
            self.assertEqual(unchanged_workflow.status, "running")
            self.assertIsNone(unchanged_workflow.finished_at)
            self.assertEqual(unchanged_job.status, "failed")
            self.assertEqual(unchanged_job.error, "provider failed")

    def test_cancel_queued_workflow_preserves_history_without_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, job = queue.submit_workflow(
                chat_id=123,
                prompt="historical task",
                workspace=Path(directory),
            )

            cancelled_workflow, cancelled_job = queue.cancel_queued_workflow(
                workflow.id,
                "Historical task superseded and not authorized for execution.",
            )

            self.assertEqual(cancelled_job.status, "failed")
            self.assertEqual(cancelled_workflow.status, "failed")
            self.assertEqual(
                cancelled_job.error,
                f"{CANCELLED_BY_OPERATOR_PREFIX} Historical task superseded and not authorized for execution.",
            )
            self.assertEqual(cancelled_workflow.error, cancelled_job.error)
            self.assertIsNotNone(cancelled_job.finished_at)
            self.assertIsNotNone(cancelled_workflow.finished_at)
            self.assertEqual(queue.get(job.id).prompt, "historical task")
            self.assertEqual(queue.get_workflow(workflow.id).prompt, "historical task")
            self.assertEqual(queue.pending_notifications(), [])
            self.assertIsNone(queue.claim_next())

    def test_cancel_rejects_running_workflow_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, job = queue.submit_workflow(
                chat_id=123,
                prompt="running task",
                workspace=Path(directory),
            )
            self.assertEqual(queue.claim_next().id, job.id)

            with self.assertRaises(QueueTransitionConflict):
                queue.cancel_queued_workflow(workflow.id, "must not cancel running")

            self.assertEqual(queue.get(job.id).status, "running")
            self.assertEqual(queue.get_workflow(workflow.id).status, "running")

    def test_cancel_rejects_succeeded_and_failed_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            succeeded_queue = JobQueue(Path(directory) / "succeeded.sqlite3")
            succeeded_workflow, succeeded_job = succeeded_queue.submit_workflow(
                chat_id=123,
                prompt="succeeded task",
                workspace=Path(directory),
            )
            self.assertEqual(succeeded_queue.claim_next().id, succeeded_job.id)
            succeeded_queue.finish(
                succeeded_job.id,
                succeeded=True,
                report_path=None,
                error=None,
            )
            succeeded_queue.finish_workflow(succeeded_workflow.id, succeeded=True)
            with self.assertRaises(QueueTransitionConflict):
                succeeded_queue.cancel_queued_workflow(succeeded_workflow.id, "too late")
            self.assertEqual(succeeded_queue.get(succeeded_job.id).status, "succeeded")

            failed_queue = JobQueue(Path(directory) / "failed.sqlite3")
            failed_workflow, failed_job = failed_queue.submit_workflow(
                chat_id=123,
                prompt="failed task",
                workspace=Path(directory),
            )
            self.assertEqual(failed_queue.claim_next().id, failed_job.id)
            failed_queue.finish(
                failed_job.id,
                succeeded=False,
                report_path=None,
                error="provider failed",
                exit_code=7,
            )
            failed_queue.advance_workflow(failed_job.id, succeeded=False)
            with self.assertRaises(QueueTransitionConflict):
                failed_queue.cancel_queued_workflow(failed_workflow.id, "too late")
            self.assertEqual(failed_queue.get(failed_job.id).status, "failed")

    def test_cancel_is_race_safe_and_rolls_back_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            creator = JobQueue(db)
            workflow, job = creator.submit_workflow(
                chat_id=123,
                prompt="race task",
                workspace=Path(directory),
            )
            queues = [JobQueue(db), JobQueue(db)]
            barrier = threading.Barrier(2)
            successes: list[tuple[object, object]] = []
            conflicts: list[Exception] = []

            def attempt(queue: JobQueue) -> None:
                barrier.wait()
                try:
                    successes.append(queue.cancel_queued_workflow(workflow.id, "race winner"))
                except Exception as exc:  # noqa: BLE001 - assert the conflict below
                    conflicts.append(exc)

            threads = [threading.Thread(target=attempt, args=(queue,)) for queue in queues]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(successes), 1)
            self.assertEqual(len(conflicts), 1)
            self.assertIsInstance(conflicts[0], QueueTransitionConflict)
            self.assertEqual(creator.get(job.id).status, "failed")
            self.assertEqual(creator.get_workflow(workflow.id).status, "failed")
            self.assertEqual(len(creator.pending_notifications()), 0)

    def test_cancelled_workflow_is_not_claimed_or_run_by_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, _job = queue.submit_workflow(
                chat_id=123,
                prompt="provider must not run",
                workspace=Path(directory),
            )
            queue.cancel_queued_workflow(workflow.id, "provider must not run")

            class ExplodingRunner:
                def run(self, _job: object) -> object:
                    raise AssertionError("provider runner was invoked for cancelled job")

            self.assertFalse(Worker(queue, ExplodingRunner()).run_once())


if __name__ == "__main__":
    unittest.main()
