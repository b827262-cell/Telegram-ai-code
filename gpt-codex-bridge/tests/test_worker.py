from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bridge.codex_runner import FAILURE_NEEDS_ATTENTION, RunOutcome
from bridge.models import Job
from bridge.worker import Worker, WorkerAlreadyRunning, WorkerLock


def _job() -> Job:
    return Job(
        id="job-worker00000001",
        chat_id="12345",
        prompt="inspect the repo",
        workspace=Path("/tmp"),
        status="running",
        created_at="now",
        provider="codex",
    )


class FakeRunner:
    def __init__(self, outcome: RunOutcome) -> None:
        self.outcome = outcome

    def run(self, job: Job) -> RunOutcome:
        return self.outcome

    def internal_report(self, job: Job, **kwargs: object) -> Path:
        return self.outcome.report_path


class RecordingQueue:
    def __init__(self, job: Job) -> None:
        self.job = job
        self.db_path = Path("/tmp/worker-test.sqlite3")
        self.finished: list[dict[str, object]] = []
        self._claimed = False

    def claim_next(self) -> Job | None:
        if self._claimed:
            return None
        self._claimed = True
        return self.job

    def finish(self, job_id: str, **kwargs: object) -> None:
        self.finished.append({"job_id": job_id, **kwargs})

    def advance_workflow(self, job_id: str, **kwargs: object):
        return None, None, False


class WorkerFailureClassificationTests(unittest.TestCase):
    def _run_with(self, outcome: RunOutcome) -> dict[str, object]:
        job = _job()
        queue = RecordingQueue(job)
        worker = Worker(queue, FakeRunner(outcome))
        self.assertTrue(worker.run_once())
        self.assertEqual(len(queue.finished), 1)
        return queue.finished[0]

    def test_needs_attention_error_names_the_actionable_class(self) -> None:
        report = {
            "status": "partial",
            "summary": "target path is outside the writable sandbox",
            "changed_files": [],
            "tests": [],
            "git_status": "",
            "needs_attention": True,
            "sandbox_mode": "workspace-write",
        }
        outcome = RunOutcome(Path("/tmp/job-worker00000001.json"), report, 0)
        self.assertEqual(outcome.failure_reason, FAILURE_NEEDS_ATTENTION)
        recorded = self._run_with(outcome)
        self.assertFalse(recorded["succeeded"])
        self.assertEqual(
            recorded["error"],
            "Codex completed but flagged needs_attention: target path is"
            " outside the writable sandbox",
        )
        self.assertNotEqual(recorded["error"], "Codex job failed")

    def test_successful_run_stores_no_error(self) -> None:
        report = {
            "status": "success",
            "summary": "done",
            "changed_files": [],
            "tests": [],
            "git_status": "",
            "needs_attention": False,
            "sandbox_mode": "workspace-write",
        }
        recorded = self._run_with(RunOutcome(Path("/tmp/ok.json"), report, 0))
        self.assertTrue(recorded["succeeded"])
        self.assertIsNone(recorded["error"])


class WorkerLockTests(unittest.TestCase):
    def test_only_one_worker_lock_can_be_held(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "worker.lock"
            with WorkerLock(lock_path):
                with self.assertRaises(WorkerAlreadyRunning):
                    with WorkerLock(lock_path):
                        pass


if __name__ == "__main__":
    unittest.main()
