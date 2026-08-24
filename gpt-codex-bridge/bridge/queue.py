"""SQLite-backed durable queue with a single-running-job guard."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid

from .models import DEFAULT_PROVIDER, WORKFLOW_STAGES, Job, Notification, Workflow, validate_provider
from .sandbox import DEFAULT_SANDBOX_MODE, validate_sandbox_mode


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobQueue:
    """Persistent queue. Every database operation uses a short-lived connection."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'codex',
                    sandbox_mode TEXT NOT NULL DEFAULT 'workspace-write'
                        CHECK (sandbox_mode IN ('read-only', 'workspace-write', 'danger-full-access')),
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    report_path TEXT,
                    error TEXT,
                    exit_code INTEGER
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created_idx
                    ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS jobs_chat_created_idx
                    ON jobs(chat_id, created_at);
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    current_stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    github_url TEXT,
                    github_status TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS workflows_chat_created_idx
                    ON workflows(chat_id, created_at);
                """
            )

            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)")
            }
            if "sandbox_mode" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN sandbox_mode TEXT NOT NULL DEFAULT 'workspace-write'"
                )
            if "provider" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN provider TEXT NOT NULL DEFAULT 'codex'"
                )
            if "exit_code" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN exit_code INTEGER")
            if "workflow_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN workflow_id TEXT")
            if "workflow_stage" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN workflow_stage TEXT")
            if "workflow_order" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN workflow_order INTEGER")
            for row in connection.execute("SELECT DISTINCT sandbox_mode FROM jobs"):
                validate_sandbox_mode(row["sandbox_mode"])
            for row in connection.execute("SELECT DISTINCT provider FROM jobs"):
                validate_provider(row["provider"])
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notifications (
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
                CREATE INDEX IF NOT EXISTS notifications_pending_created_idx
                    ON notifications(status, created_at);
                """
            )

    def submit(
        self,
        *,
        chat_id: str | int,
        prompt: str,
        workspace: Path,
        sandbox_mode: str = DEFAULT_SANDBOX_MODE,
        provider: str = DEFAULT_PROVIDER,
        workflow_id: str | None = None,
        workflow_stage: str | None = None,
        workflow_order: int | None = None,
    ) -> Job:
        validate_sandbox_mode(sandbox_mode)
        validate_provider(provider)
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        created_at = _now()
        normalized_workspace = Path(workspace).resolve(strict=False)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, chat_id, prompt, workspace, provider, sandbox_mode, status, created_at,
                    workflow_id, workflow_stage, workflow_order
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(chat_id),
                    prompt,
                    str(normalized_workspace),
                    provider,
                    sandbox_mode,
                    created_at,
                    workflow_id,
                    workflow_stage,
                    workflow_order,
                ),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def submit_workflow(
        self,
        *,
        chat_id: str | int,
        prompt: str,
        workspace: Path,
        sandbox_mode: str = DEFAULT_SANDBOX_MODE,
    ) -> tuple[Workflow, Job]:
        """Create one durable GPT → AGY → Claude workflow and its first job."""

        validate_sandbox_mode(sandbox_mode)
        validate_provider(DEFAULT_PROVIDER)
        workflow_id = f"flow-{uuid.uuid4().hex[:16]}"
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        created_at = _now()
        normalized_workspace = Path(workspace).resolve(strict=False)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, chat_id, prompt, workspace, status, current_stage, created_at
                    )
                    VALUES (?, ?, ?, ?, 'queued', 'gpt', ?)
                    """,
                    (workflow_id, str(chat_id), prompt, str(normalized_workspace), created_at),
                )
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, chat_id, prompt, workspace, provider, sandbox_mode, status, created_at,
                        workflow_id, workflow_stage, workflow_order
                    )
                    VALUES (?, ?, ?, ?, 'codex', ?, 'queued', ?, ?, 'gpt', 1)
                    """,
                    (
                        job_id,
                        str(chat_id),
                        prompt,
                        str(normalized_workspace),
                        sandbox_mode,
                        created_at,
                        workflow_id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        workflow = self.get_workflow(workflow_id)
        job = self.get(job_id)
        assert workflow is not None and job is not None
        return workflow, job

    def get(self, job_id: str) -> Job | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def get_for_chat(self, job_id: str, chat_id: str | int) -> Job | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ? AND chat_id = ?",
                (job_id, str(chat_id)),
            ).fetchone()
        return Job.from_row(row) if row else None

    def recent_for_chat(self, chat_id: str | int, *, limit: int = 10) -> list[Job]:
        if limit < 1:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE chat_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (str(chat_id), limit),
            ).fetchall()
        return [Job.from_row(row) for row in rows]

    def claim_next(self) -> Job | None:
        """Atomically claim one job; refuses a second concurrent running job."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            running = connection.execute(
                "SELECT 1 FROM jobs WHERE status = 'running' LIMIT 1"
            ).fetchone()
            if running:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            started_at = _now()
            connection.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, error = NULL WHERE id = ?",
                (started_at, row["id"]),
            )
            if row["workflow_id"]:
                connection.execute(
                    """
                    UPDATE workflows
                    SET status = 'running', current_stage = ?, error = NULL
                    WHERE id = ? AND status IN ('queued', 'running')
                    """,
                    (row["workflow_stage"] or "unknown", row["workflow_id"]),
                )
            connection.commit()
        return self.get(row["id"])

    def requeue_running(self) -> int:
        """Recover jobs left running by a daemon crash or restart."""

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', started_at = NULL,
                    error = 'requeued after worker restart'
                WHERE status = 'running'
                """
            )
            return cursor.rowcount

    def finish(
        self,
        job_id: str,
        *,
        succeeded: bool,
        report_path: Path | None,
        error: str | None,
        exit_code: int | None = None,
    ) -> bool:
        """Atomically finish a running job and enqueue exactly one notification."""

        event_type = "succeeded" if succeeded else "failed"
        terminal_status = "succeeded" if succeeded else "failed"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                job = connection.execute(
                    "SELECT chat_id FROM jobs WHERE id = ? AND status = 'running'",
                    (job_id,),
                ).fetchone()
                if job is None:
                    connection.commit()
                    return False
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, finished_at = ?, report_path = ?, error = ?, exit_code = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        terminal_status,
                        _now(),
                        str(report_path) if report_path else None,
                        error,
                        exit_code,
                        job_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO notifications (
                        job_id, chat_id, event_type, status, attempts, created_at
                    )
                    VALUES (?, ?, ?, 'pending', 0, ?)
                    ON CONFLICT (job_id, chat_id, event_type) DO NOTHING
                    """,
                    (job_id, str(job["chat_id"]), event_type, _now()),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        return Workflow.from_row(row) if row else None

    def get_workflow_for_chat(self, workflow_id: str, chat_id: str | int) -> Workflow | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE id = ? AND chat_id = ?",
                (workflow_id, str(chat_id)),
            ).fetchone()
        return Workflow.from_row(row) if row else None

    def recent_workflows_for_chat(self, chat_id: str | int, *, limit: int = 5) -> list[Workflow]:
        if limit < 1:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflows
                WHERE chat_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (str(chat_id), limit),
            ).fetchall()
        return [Workflow.from_row(row) for row in rows]

    def workflow_jobs(self, workflow_id: str) -> list[Job]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE workflow_id = ?
                ORDER BY workflow_order, created_at, id
                """,
                (workflow_id,),
            ).fetchall()
        return [Job.from_row(row) for row in rows]

    @staticmethod
    def _workflow_stage_prompt(original_prompt: str, stage: str, previous_jobs: list[Job]) -> str:
        previous = ", ".join(
            f"{job.workflow_stage}:{job.id}" for job in previous_jobs if job.workflow_stage
        )
        if stage == "agy":
            instruction = (
                "Review the current workspace after the GPT/Codex implementation. "
                "Do not make production changes, commit, or push. Inspect the diff and tests, "
                "then report concrete correctness, regression, security, and edge-case findings."
            )
        else:
            instruction = (
                "Act as the final integrator for the current workspace. Read the GPT implementation "
                "and AGY review, make only necessary in-scope repairs, run the relevant tests, and "
                "do not commit or push; the bridge will publish the workflow report separately."
            )
        return (
            f"AUTOMATED WORKFLOW STAGE: {stage.upper()}\n"
            f"PREVIOUS JOBS: {previous or 'none'}\n"
            f"ORIGINAL TASK:\n{original_prompt}\n\n"
            f"STAGE INSTRUCTIONS:\n{instruction}\n\n"
            "Return a concise structured result with summary, findings/actions, tests, and any "
            "blockers. Never output credentials or secret values."
        )

    def advance_workflow(self, job_id: str, *, succeeded: bool) -> tuple[Workflow | None, Job | None, bool]:
        """Advance a completed workflow stage; return next job or GitHub-publish flag."""

        completed_job = self.get(job_id)
        if completed_job is None or not completed_job.workflow_id:
            return None, None, False
        workflow_id = completed_job.workflow_id
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None, None, False
                if not succeeded:
                    connection.execute(
                        """
                        UPDATE workflows
                        SET status = 'failed', finished_at = ?, error = ?, current_stage = ?
                        WHERE id = ?
                        """,
                        (_now(), f"{completed_job.workflow_stage or 'unknown'} stage failed", completed_job.workflow_stage or "unknown", workflow_id),
                    )
                    connection.commit()
                    return self.get_workflow(workflow_id), None, False

                stage = completed_job.workflow_stage
                if stage not in WORKFLOW_STAGES:
                    connection.commit()
                    return self.get_workflow(workflow_id), None, False
                next_index = WORKFLOW_STAGES.index(stage) + 1
                if next_index >= len(WORKFLOW_STAGES):
                    connection.execute(
                        "UPDATE workflows SET status = 'running', current_stage = 'github', error = NULL WHERE id = ?",
                        (workflow_id,),
                    )
                    connection.commit()
                    return self.get_workflow(workflow_id), None, True

                next_stage = WORKFLOW_STAGES[next_index]
                provider = "codex" if next_stage == "gpt" else next_stage
                jobs = self.workflow_jobs(workflow_id)
                prompt = self._workflow_stage_prompt(row["prompt"], next_stage, jobs)
                next_job_id = f"job-{uuid.uuid4().hex[:16]}"
                created_at = _now()
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, chat_id, prompt, workspace, provider, sandbox_mode, status, created_at,
                        workflow_id, workflow_stage, workflow_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        next_job_id,
                        row["chat_id"],
                        prompt,
                        row["workspace"],
                        provider,
                        completed_job.sandbox_mode,
                        created_at,
                        workflow_id,
                        next_stage,
                        next_index + 1,
                    ),
                )
                connection.execute(
                    "UPDATE workflows SET status = 'running', current_stage = ?, error = NULL WHERE id = ?",
                    (next_stage, workflow_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        workflow = self.get_workflow(workflow_id)
        next_job = self.get(next_job_id)
        assert workflow is not None and next_job is not None
        return workflow, next_job, False

    def finish_workflow(
        self,
        workflow_id: str,
        *,
        succeeded: bool,
        github_url: str | None = None,
        github_status: str | None = None,
        error: str | None = None,
    ) -> Workflow | None:
        status = "succeeded" if succeeded else "failed"
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE workflows
                SET status = ?, current_stage = 'github', finished_at = ?,
                    github_url = ?, github_status = ?, error = ?
                WHERE id = ?
                """,
                (status, _now(), github_url, github_status, error, workflow_id),
            )
        return self.get_workflow(workflow_id)

    def get_notification(self, notification_id: int) -> Notification | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        return Notification.from_row(row) if row else None

    def pending_notifications(self, *, limit: int = 20) -> list[Notification]:
        if limit < 1:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM notifications
                WHERE status = 'pending'
                ORDER BY created_at, id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [Notification.from_row(row) for row in rows]

    def mark_notification_sent(self, notification_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE notifications
                SET status = 'sent', sent_at = ?, last_error = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (_now(), notification_id),
            )
            return cursor.rowcount == 1

    def mark_notification_failed(self, notification_id: int, last_error: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE notifications
                SET attempts = attempts + 1, last_error = ?
                WHERE id = ? AND status = 'pending'
                """,
                (last_error, notification_id),
            )
            return cursor.rowcount == 1

    def counts(self) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
        with self._connection() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")
            for row in rows:
                counts[row["status"]] = int(row["count"])
        return counts
