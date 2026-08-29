from __future__ import annotations

import os
import subprocess
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
            self.assertEqual(job.execution_kind, "bridge")
            self.assertIsNone(job.pid)
            self.assertIsNone(job.device_id)
            self.assertIsNone(job.model)
            self.assertIsNone(job.effort)
            self.assertEqual(job.attempts, 0)

    def test_submit_persists_registry_metadata_and_deduplicates_active_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            job = queue.submit(
                chat_id=1,
                prompt="modelled task",
                workspace=Path(directory),
                model="gpt-5.6-luna",
                effort="high",
                idempotency_key="request-001",
            )
            duplicate = queue.submit(
                chat_id=1,
                prompt="different text is ignored for the same request key",
                workspace=Path(directory),
                model="other-model",
                effort="low",
                idempotency_key="request-001",
            )

            self.assertEqual(duplicate.id, job.id)
            persisted = queue.get(job.id)
            self.assertEqual(persisted.model, "gpt-5.6-luna")
            self.assertEqual(persisted.effort, "high")
            self.assertEqual(persisted.idempotency_key, "request-001")
            self.assertEqual(queue.counts()["queued"], 1)
            self.assertEqual(queue.claim_next().id, job.id)
            queue.finish(job.id, succeeded=True, report_path=None, error=None)
            replacement = queue.submit(
                chat_id=1,
                prompt="modelled task",
                workspace=Path(directory),
                idempotency_key="request-001",
            )
            self.assertNotEqual(replacement.id, job.id)
            self.assertEqual(queue.counts()["queued"], 1)

    def test_explicit_idempotency_key_never_returns_another_chats_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            first = queue.submit(
                chat_id=1,
                prompt="first chat",
                workspace=Path(directory),
                idempotency_key="shared-request-id",
            )

            with self.assertRaisesRegex(QueueTransitionConflict, "already active"):
                queue.submit(
                    chat_id=2,
                    prompt="second chat",
                    workspace=Path(directory),
                    idempotency_key="shared-request-id",
                )

            self.assertEqual(queue.get(first.id).chat_id, "1")
            self.assertEqual(queue.recent_for_chat(2), [])

    def test_derived_idempotency_key_is_scoped_to_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            first = queue.submit(chat_id=1, prompt="same task", workspace=Path(directory))
            second = queue.submit(chat_id=2, prompt="same task", workspace=Path(directory))

            self.assertNotEqual(first.id, second.id)
            self.assertNotEqual(first.idempotency_key, second.idempotency_key)
            self.assertEqual(first.chat_id, "1")
            self.assertEqual(second.chat_id, "2")

    def test_derived_workflow_idempotency_key_is_scoped_to_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            first_flow, first_job = queue.submit_workflow(
                chat_id=1, prompt="same workflow", workspace=Path(directory)
            )
            second_flow, second_job = queue.submit_workflow(
                chat_id=2, prompt="same workflow", workspace=Path(directory)
            )

            self.assertNotEqual(first_flow.id, second_flow.id)
            self.assertNotEqual(first_job.id, second_job.id)
            self.assertEqual(first_job.chat_id, "1")
            self.assertEqual(second_job.chat_id, "2")

    def test_no_external_write_workflow_persists_across_queue_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            workflow, _job = JobQueue(db).submit_workflow(
                chat_id=1,
                prompt="safe smoke",
                workspace=Path(directory),
                external_publication_enabled=False,
            )

            recovered = JobQueue(db).get_workflow(workflow.id)

            self.assertIsNotNone(recovered)
            self.assertFalse(recovered.external_publication_enabled)

    def test_workflow_idempotency_does_not_drop_external_write_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            normal, _job = queue.submit_workflow(
                chat_id=1, prompt="same", workspace=Path(directory)
            )
            # A queued workflow is idempotent only for its own policy. A safe
            # retry must not silently inherit the normal workflow's permission.
            safe, _job = queue.submit_workflow(
                chat_id=1,
                prompt="same",
                workspace=Path(directory),
                external_publication_enabled=False,
            )
            self.assertNotEqual(normal.id, safe.id)
            self.assertTrue(queue.get_workflow(normal.id).external_publication_enabled)
            self.assertFalse(queue.get_workflow(safe.id).external_publication_enabled)

    def test_no_external_write_finishes_without_github_publish_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, job = queue.submit_workflow(
                chat_id=1,
                prompt="safe smoke",
                workspace=Path(directory),
                external_publication_enabled=False,
            )
            for _ in range(3):
                claimed = queue.claim_next()
                self.assertIsNotNone(claimed)
                queue.finish(claimed.id, succeeded=True, report_path=None, error=None)
                workflow, next_job, needs_publish = queue.advance_workflow(
                    claimed.id, succeeded=True
                )

            self.assertIsNone(next_job)
            self.assertFalse(needs_publish)
            self.assertEqual(workflow.status, "succeeded")
            self.assertEqual(workflow.github_status, "skipped_no_external_write")
            self.assertFalse(workflow.external_publication_enabled)

    def test_malformed_persisted_external_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            workflow, _job = queue.submit_workflow(
                chat_id=1, prompt="safe smoke", workspace=Path(directory)
            )
            with queue._connection() as connection:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute(
                    "UPDATE workflows SET external_publication_enabled = 2 WHERE id = ?",
                    (workflow.id,),
                )
            # SQLite's CHECK normally rejects this; an old/corrupt DB must still
            # never be able to authorize publication when advanced.
            self.assertRaises(ValueError, queue.get_workflow, workflow.id)

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

    def test_plain_submit_rejects_mutating_job_running_in_same_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            running = queue.submit(chat_id=1, prompt="first", workspace=Path(directory))
            self.assertEqual(queue.claim_next().id, running.id)

            with self.assertRaises(CodexExecAlreadyRunning) as raised:
                queue.submit(chat_id=1, prompt="second", workspace=Path(directory))

            self.assertEqual([job.id for job in raised.exception.jobs], [running.id])
            self.assertEqual(queue.counts(), {"queued": 0, "running": 1, "succeeded": 0, "failed": 0})

    def test_read_only_submit_is_exempt_from_running_mutating_job_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            running = queue.submit(chat_id=1, prompt="write", workspace=Path(directory))
            queue.claim_next()

            inspection = queue.submit(
                chat_id=1,
                prompt="inspect",
                workspace=Path(directory),
                sandbox_mode="read-only",
            )
            self.assertEqual(inspection.sandbox_mode, "read-only")
            self.assertIsNone(queue.claim_next())
            queue.finish(running.id, succeeded=True, report_path=None, error=None)
            self.assertEqual(queue.claim_next().id, inspection.id)

    def test_mutating_submit_rejects_running_read_only_job_in_same_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            inspection = queue.submit(
                chat_id=1,
                prompt="inspect",
                workspace=Path(directory),
                sandbox_mode="read-only",
            )
            queue.claim_next()

            with self.assertRaises(CodexExecAlreadyRunning) as raised:
                queue.submit(chat_id=1, prompt="write", workspace=Path(directory))
            self.assertEqual([job.id for job in raised.exception.jobs], [inspection.id])

    def test_read_only_jobs_are_compatible_in_same_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            first = queue.submit(
                chat_id=1, prompt="inspect one", workspace=Path(directory), sandbox_mode="read-only"
            )
            queue.claim_next()
            second = queue.submit(
                chat_id=1, prompt="inspect two", workspace=Path(directory), sandbox_mode="read-only"
            )
            self.assertNotEqual(first.id, second.id)

    def test_external_registration_persists_pid_and_blocks_same_workspace_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            external = queue.register_external_job(
                chat_id="rdc",
                prompt="external workspace mutation",
                workspace=Path(directory),
                pid=os.getpid(),
                device_id="001ffb71-77d5-45b9-b736-e061e77af3ca",
                model="gpt-5.6-luna",
                effort="medium",
            )

            self.assertEqual(external.status, "running")
            self.assertEqual(external.execution_kind, "dc")
            self.assertEqual(external.pid, os.getpid())
            self.assertEqual(external.device_id, "001ffb71-77d5-45b9-b736-e061e77af3ca")
            self.assertEqual(external.model, "gpt-5.6-luna")
            self.assertEqual(external.effort, "medium")
            self.assertIsNone(queue.claim_next())
            with self.assertRaises(CodexExecAlreadyRunning) as raised:
                queue.submit(chat_id=1, prompt="bridge mutation", workspace=Path(directory))
            self.assertEqual([job.id for job in raised.exception.jobs], [external.id])

            duplicate = queue.register_external_job(
                chat_id="rdc",
                prompt="external workspace mutation",
                workspace=Path(directory),
                pid=os.getpid(),
                device_id="other-device",
            )
            self.assertEqual(duplicate.id, external.id)
            self.assertEqual(queue.get(external.id).pid, os.getpid())

            self.assertEqual(queue.requeue_running(), 0)
            self.assertEqual(queue.get(external.id).status, "running")

    def test_exited_external_pid_is_reaped_and_does_not_block_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            queued = queue.submit(chat_id=1, prompt="bridge work", workspace=Path(directory))
            process = subprocess.Popen(["sleep", "30"])
            try:
                external = queue.register_external_job(
                    chat_id="rdc",
                    prompt="external work",
                    workspace=Path(directory),
                    pid=process.pid,
                )
            finally:
                process.terminate()
                process.wait(timeout=5)

            claimed = queue.claim_next()

            self.assertEqual(claimed.id, queued.id)
            stale = queue.get(external.id)
            self.assertEqual(stale.status, "failed")
            self.assertIn("PID identity", stale.error)
            self.assertEqual(queue.pending_notifications(), [])

    def test_external_completion_api_releases_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            queued = queue.submit(chat_id=1, prompt="bridge work", workspace=Path(directory))
            external = queue.register_external_job(
                chat_id="rdc",
                prompt="external work",
                workspace=Path(directory),
                pid=os.getpid(),
            )

            self.assertTrue(queue.finish_external_job(external.id, succeeded=True))
            self.assertEqual(queue.get(external.id).status, "succeeded")
            self.assertEqual(queue.pending_notifications(), [])
            self.assertEqual(queue.claim_next().id, queued.id)

    def test_concurrent_plain_submissions_all_see_running_workspace_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.sqlite3"
            creator = JobQueue(db)
            running = creator.submit(chat_id=1, prompt="already running", workspace=Path(directory))
            creator.claim_next()
            contenders = [JobQueue(db), JobQueue(db)]
            barrier = threading.Barrier(2)
            conflicts: list[Exception] = []

            def attempt(index: int) -> None:
                barrier.wait()
                try:
                    contenders[index].submit(
                        chat_id=1,
                        prompt=f"contender-{index}",
                        workspace=Path(directory),
                    )
                except Exception as exc:  # noqa: BLE001 - assert the typed conflict below
                    conflicts.append(exc)

            threads = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(conflicts), 2)
            self.assertTrue(all(isinstance(error, CodexExecAlreadyRunning) for error in conflicts))
            self.assertEqual(creator.get(running.id).status, "running")
            self.assertEqual(creator.counts(), {"queued": 0, "running": 1, "succeeded": 0, "failed": 0})

    def test_active_counts_by_provider_are_uncapped_queue_truth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            codex = queue.submit(chat_id=1, prompt="codex", workspace=Path(directory))
            queue.submit(
                chat_id=1,
                prompt="claude",
                workspace=Path(directory),
                provider="claude",
            )
            self.assertEqual(queue.claim_next().id, codex.id)

            self.assertEqual(
                queue.active_counts_by_provider(),
                {
                    "codex": {"queued": 0, "running": 1},
                    "claude": {"queued": 1, "running": 0},
                },
            )

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

    def test_advance_workflow_keeps_job_reason_and_matches_reconcile_shape(self) -> None:
        # Production specimen job-0fd43861826e434b proved advance_workflow dropped
        # the terminal job error, so the operator only ever saw "gpt stage failed".
        with tempfile.TemporaryDirectory() as directory:
            reason = (
                "Codex completed but flagged needs_attention: "
                "target path is outside the writable sandbox"
            )

            direct = JobQueue(Path(directory) / "advance.sqlite3")
            first, first_job = direct.submit_workflow(
                chat_id=123, prompt="p1", workspace=Path(directory)
            )
            self.assertEqual(direct.claim_next().id, first_job.id)
            direct.finish(
                first_job.id,
                succeeded=False,
                report_path=None,
                error=reason,
                exit_code=0,
            )
            advanced, next_job, needs_report = direct.advance_workflow(
                first_job.id, succeeded=False
            )
            self.assertIsNone(next_job)
            self.assertFalse(needs_report)
            self.assertEqual(advanced.id, first.id)
            self.assertEqual(advanced.error, f"gpt stage failed: {reason}")

            stale = JobQueue(Path(directory) / "reconcile.sqlite3")
            second, second_job = stale.submit_workflow(
                chat_id=123, prompt="p2", workspace=Path(directory)
            )
            self.assertEqual(stale.claim_next().id, second_job.id)
            stale.finish(
                second_job.id,
                succeeded=False,
                report_path=None,
                error=reason,
                exit_code=-1,
            )
            with sqlite3.connect(Path(directory) / "reconcile.sqlite3") as connection:
                connection.execute(
                    "UPDATE workflows SET status = 'queued' WHERE id = ?",
                    (second.id,),
                )
            reconciled, _terminal = stale.reconcile_failed_workflow(second.id)

            self.assertEqual(advanced.error, reconciled.error)

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
