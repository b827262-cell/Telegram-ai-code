"""Single worker process for durable Codex jobs."""

from __future__ import annotations

from contextlib import AbstractContextManager
import fcntl
import os
from pathlib import Path
import signal
import time

from .agy_runner import AgyRunner
from .claude_runner import ClaudeRunner
from .codex_runner import CodexRunner
from .config import Settings
from .github_reporter import GitHubReportError, GitHubReportPublisher
from .models import Job
from .queue import JobQueue


class WorkerAlreadyRunning(RuntimeError):
    """Raised when another worker owns the runner lock."""


class WorkerLock(AbstractContextManager["WorkerLock"]):
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "WorkerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise WorkerAlreadyRunning("another worker already owns the lock") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        runner: CodexRunner,
        *,
        claude_runner: ClaudeRunner | None = None,
        agy_runner: AgyRunner | None = None,
        poll_seconds: float = 1.0,
        lock_path: Path | None = None,
        sleep=time.sleep,
        github_reporter: GitHubReportPublisher | None = None,
    ):
        self.queue = queue
        self.runner = runner
        self.claude_runner = claude_runner
        self.agy_runner = agy_runner
        self.poll_seconds = poll_seconds
        self.lock_path = lock_path or queue.db_path.with_suffix(".worker.lock")
        self._sleep = sleep
        self.github_reporter = github_reporter

    def _runner_for(self, job: Job) -> CodexRunner | ClaudeRunner | AgyRunner:
        if job.provider == "codex":
            return self.runner
        if job.provider == "claude" and self.claude_runner is not None:
            return self.claude_runner
        if job.provider == "agy" and self.agy_runner is not None:
            return self.agy_runner
        raise RuntimeError(f"runner is not configured for provider={job.provider}")

    def run_once(self) -> bool:
        job = self.queue.claim_next()
        if job is None:
            return False
        selected_runner: CodexRunner | ClaudeRunner | AgyRunner | None = None
        try:
            selected_runner = self._runner_for(job)
            outcome = selected_runner.run(job)
            self.queue.finish(
                job.id,
                succeeded=outcome.succeeded,
                report_path=outcome.report_path,
                error=None if outcome.succeeded else f"{job.provider.title()} job failed",
                exit_code=outcome.exit_code,
            )
            workflow, _next_job, needs_github_report = self.queue.advance_workflow(
                job.id,
                succeeded=outcome.succeeded,
            )
            if needs_github_report and workflow is not None:
                if self.github_reporter is None:
                    self.queue.finish_workflow(
                        workflow.id,
                        succeeded=False,
                        github_status="not_configured",
                        error="GitHub report publisher is not configured",
                    )
                else:
                    try:
                        github_url = self.github_reporter.publish(
                            workflow,
                            self.queue.workflow_jobs(workflow.id),
                        )
                    except GitHubReportError as exc:
                        self.queue.finish_workflow(
                            workflow.id,
                            succeeded=False,
                            github_status="failed",
                            error=str(exc),
                        )
                    else:
                        self.queue.finish_workflow(
                            workflow.id,
                            succeeded=True,
                            github_url=github_url,
                            github_status="uploaded",
                        )
        except Exception:
            if selected_runner is None:
                selected_runner = self._runner_for(job)
            report_path = selected_runner.internal_report(
                job,
                summary=f"{job.provider.title()} runner failed unexpectedly",
            )
            self.queue.finish(
                job.id,
                succeeded=False,
                report_path=report_path,
                error=f"{job.provider.title()} runner failed unexpectedly",
                exit_code=-1,
            )
            self.queue.advance_workflow(job.id, succeeded=False)
        return True

    def run_forever(self, stop_event=None) -> None:
        with WorkerLock(self.lock_path):
            self.queue.requeue_running()
            while stop_event is None or not stop_event.is_set():
                if not self.run_once():
                    self._sleep(self.poll_seconds)


def main() -> int:
    settings = Settings.from_env()
    settings.ensure_runtime_dirs()
    queue = JobQueue(settings.queue_db)
    runner = CodexRunner(settings)
    claude_runner = ClaudeRunner(settings)
    agy_runner = AgyRunner(settings)
    github_reporter = GitHubReportPublisher(settings)
    stop = __import__("threading").Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Worker(
        queue,
        runner,
        claude_runner=claude_runner,
        agy_runner=agy_runner,
        poll_seconds=settings.worker_poll_seconds,
        github_reporter=github_reporter,
    ).run_forever(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
