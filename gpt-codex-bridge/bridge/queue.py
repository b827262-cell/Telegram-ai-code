"""SQLite-backed durable queue with a single-running-job guard."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import uuid

from .models import (
    DC_EXECUTION_KIND,
    DEFAULT_EXECUTION_KIND,
    DEFAULT_PROVIDER,
    WORKFLOW_STAGES,
    Job,
    Notification,
    Workflow,
    validate_external_publication_enabled,
    validate_execution_kind,
    validate_provider,
)
from .sandbox import DEFAULT_SANDBOX_MODE, validate_sandbox_mode


CANCELLED_BY_OPERATOR_PREFIX = "CANCELLED_BY_OPERATOR:"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CodexExecAlreadyRunning(RuntimeError):
    """Raised when a mutating job overlaps an active workspace execution."""

    code = "CODEX_EXEC_RUNNING"

    def __init__(self, jobs: tuple[Job, ...]):
        self.jobs = jobs
        super().__init__("a Codex exec job is already running")


class QueueTransitionConflict(RuntimeError):
    """Raised when an operator transition cannot safely be applied."""


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
                    execution_kind TEXT NOT NULL DEFAULT 'bridge',
                    pid INTEGER,
                    pid_start_token TEXT,
                    device_id TEXT,
                    model TEXT,
                    effort TEXT,
                    idempotency_key TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
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
                    error TEXT,
                    external_publication_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (external_publication_enabled IN (0, 1))
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
            if "execution_kind" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'bridge'"
                )
            if "pid" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")
            if "pid_start_token" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN pid_start_token TEXT")
            if "device_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN device_id TEXT")
            if "model" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN model TEXT")
            if "effort" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN effort TEXT")
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT")
            if "attempts" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
                )
            workflow_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(workflows)")
            }
            if "external_publication_enabled" not in workflow_columns:
                # Existing workflows preserve historic behavior: their reports
                # remain publishable when global reporting is enabled.
                connection.execute(
                    "ALTER TABLE workflows ADD COLUMN external_publication_enabled INTEGER NOT NULL DEFAULT 1"
                )
            for row in connection.execute("SELECT DISTINCT sandbox_mode FROM jobs"):
                validate_sandbox_mode(row["sandbox_mode"])
            for row in connection.execute("SELECT DISTINCT provider FROM jobs"):
                validate_provider(row["provider"])
            for row in connection.execute("SELECT DISTINCT execution_kind FROM jobs"):
                validate_execution_kind(row["execution_kind"])
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS jobs_active_workspace_idx
                    ON jobs(workspace, status);
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_idempotency_key_idx
                    ON jobs(idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                      AND status IN ('queued', 'running');
                """
            )
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
                    dead_lettered_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id),
                    UNIQUE (job_id, chat_id, event_type)
                );
                CREATE INDEX IF NOT EXISTS notifications_pending_created_idx
                    ON notifications(status, created_at);
                """
            )
            notification_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(notifications)")
            }
            if "dead_lettered_at" not in notification_columns:
                connection.execute(
                    "ALTER TABLE notifications ADD COLUMN dead_lettered_at TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS notifications_delivery_created_idx
                ON notifications(status, dead_lettered_at, created_at)
                """
            )

    @staticmethod
    def _derived_idempotency_key(
        *, command: str, chat_id: str | int, prompt: str, workspace: Path, provider: str
    ) -> str:
        material = "\x00".join((command, str(chat_id), provider, prompt, str(workspace)))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_pid(pid: int | None) -> None:
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int) or pid < 1):
            raise ValueError("pid must be a positive integer")

    @staticmethod
    def _active_job_for_idempotency_key(
        connection: sqlite3.Connection, idempotency_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM jobs
            WHERE idempotency_key = ? AND status IN ('queued', 'running')
            ORDER BY created_at, id
            LIMIT 1
            """,
            (idempotency_key,),
        ).fetchone()

    @staticmethod
    def _pid_state(pid: int) -> tuple[bool, str | None]:
        """Return liveness and a Linux process-identity token when available."""

        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except (FileNotFoundError, ProcessLookupError):
            return False, None
        except (OSError, UnicodeError):
            stat = ""
        if stat:
            fields = stat.rsplit(") ", 1)[-1].split()
            if len(fields) >= 20:
                # The first field here is process state (field 3); starttime is field 22.
                if fields[0] == "Z":
                    return False, fields[19]
                return True, fields[19]
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return False, None
            if exc.errno == errno.EPERM:
                return True, None
            return False, None
        return True, None

    @classmethod
    def _reap_stale_external_jobs(cls, connection: sqlite3.Connection) -> int:
        rows = connection.execute(
            """
            SELECT id, pid, pid_start_token
            FROM jobs
            WHERE status = 'running' AND execution_kind = 'dc'
            """
        ).fetchall()
        reaped = 0
        for row in rows:
            pid = row["pid"]
            alive, current_token = cls._pid_state(int(pid)) if pid is not None else (False, None)
            expected_token = row["pid_start_token"]
            if alive and (expected_token is None or current_token == expected_token):
                continue
            finished_at = _now()
            error = "external process exited or PID identity changed"
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', finished_at = ?, error = ?
                WHERE id = ? AND status = 'running' AND execution_kind = 'dc'
                """,
                (finished_at, error, row["id"]),
            )
            if cursor.rowcount != 1:
                continue
            reaped += 1
        return reaped

    @staticmethod
    def _running_jobs_for_workspace(
        connection: sqlite3.Connection, workspace: Path
    ) -> tuple[Job, ...]:
        rows = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'running'
              AND workspace = ?
            ORDER BY started_at, created_at, id
            """,
            (str(workspace),),
        ).fetchall()
        return tuple(Job.from_row(row) for row in rows)

    @staticmethod
    def _raise_if_workspace_running(
        connection: sqlite3.Connection, workspace: Path, sandbox_mode: str
    ) -> None:
        JobQueue._reap_stale_external_jobs(connection)
        if sandbox_mode == "read-only":
            return
        running_jobs = JobQueue._running_jobs_for_workspace(connection, workspace)
        if running_jobs:
            raise CodexExecAlreadyRunning(running_jobs)

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
        execution_kind: str = DEFAULT_EXECUTION_KIND,
        pid: int | None = None,
        device_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
        attempts: int = 0,
    ) -> Job:
        validate_sandbox_mode(sandbox_mode)
        validate_provider(provider)
        validate_execution_kind(execution_kind)
        self._validate_pid(pid)
        if attempts < 0:
            raise ValueError("attempts must not be negative")
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        created_at = _now()
        normalized_workspace = Path(workspace).resolve(strict=False)
        active_key = idempotency_key or self._derived_idempotency_key(
            command=f"submit:{sandbox_mode}",
            chat_id=chat_id,
            provider=provider,
            prompt=prompt,
            workspace=normalized_workspace,
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_stale_external_jobs(connection)
            existing = self._active_job_for_idempotency_key(connection, active_key)
            if existing is not None:
                if existing["chat_id"] != str(chat_id):
                    connection.rollback()
                    raise QueueTransitionConflict("idempotency key is already active")
                connection.commit()
                return Job.from_row(existing)
            try:
                self._raise_if_workspace_running(connection, normalized_workspace, sandbox_mode)
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, chat_id, prompt, workspace, provider, sandbox_mode,
                        execution_kind, pid, device_id, model, effort, idempotency_key,
                        attempts, status, created_at, workflow_id, workflow_stage, workflow_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        str(chat_id),
                        prompt,
                        str(normalized_workspace),
                        provider,
                        sandbox_mode,
                        execution_kind,
                        pid,
                        device_id,
                        model,
                        effort,
                        active_key,
                        attempts,
                        created_at,
                        workflow_id,
                        workflow_stage,
                        workflow_order,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        job = self.get(job_id)
        assert job is not None
        return job

    def register_external_job(
        self,
        *,
        chat_id: str | int,
        prompt: str,
        workspace: Path,
        pid: int,
        device_id: str | None = None,
        sandbox_mode: str = DEFAULT_SANDBOX_MODE,
        provider: str = DEFAULT_PROVIDER,
        model: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """Register an already-running external/RDC process in the job registry."""

        validate_sandbox_mode(sandbox_mode)
        validate_provider(provider)
        if pid is None:
            raise ValueError("pid must be a positive integer")
        self._validate_pid(pid)
        normalized_workspace = Path(workspace).resolve(strict=False)
        active_key = idempotency_key or self._derived_idempotency_key(
            command=f"{DC_EXECUTION_KIND}:{sandbox_mode}",
            chat_id=chat_id,
            provider=provider,
            prompt=prompt,
            workspace=normalized_workspace,
        )
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        started_at = _now()
        alive, pid_start_token = self._pid_state(pid)
        if not alive:
            raise ValueError("pid does not identify a live process")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_stale_external_jobs(connection)
            existing = self._active_job_for_idempotency_key(connection, active_key)
            if existing is not None:
                if existing["chat_id"] != str(chat_id):
                    connection.rollback()
                    raise QueueTransitionConflict("idempotency key is already active")
                connection.commit()
                return Job.from_row(existing)
            try:
                self._raise_if_workspace_running(connection, normalized_workspace, sandbox_mode)
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, chat_id, prompt, workspace, provider, sandbox_mode,
                        execution_kind, pid, pid_start_token, device_id, model, effort, idempotency_key,
                        attempts, status, created_at, started_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'running', ?, ?)
                    """,
                    (
                        job_id,
                        str(chat_id),
                        prompt,
                        str(normalized_workspace),
                        provider,
                        sandbox_mode,
                        DC_EXECUTION_KIND,
                        pid,
                        pid_start_token,
                        device_id,
                        model,
                        effort,
                        active_key,
                        started_at,
                        started_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
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
        model: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
        external_publication_enabled: bool = True,
    ) -> tuple[Workflow, Job]:
        """Create one durable GPT → AGY → Claude workflow and its first job."""

        validate_sandbox_mode(sandbox_mode)
        validate_provider(DEFAULT_PROVIDER)
        validate_external_publication_enabled(external_publication_enabled)
        workflow_id = f"flow-{uuid.uuid4().hex[:16]}"
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        created_at = _now()
        normalized_workspace = Path(workspace).resolve(strict=False)
        active_key = idempotency_key or self._derived_idempotency_key(
            command=(
                f"workflow:{sandbox_mode}:external-publication="
                f"{int(external_publication_enabled)}"
            ),
            chat_id=chat_id,
            provider=DEFAULT_PROVIDER,
            prompt=prompt,
            workspace=normalized_workspace,
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_stale_external_jobs(connection)
            existing = self._active_job_for_idempotency_key(connection, active_key)
            if existing is not None:
                if existing["chat_id"] != str(chat_id):
                    connection.rollback()
                    raise QueueTransitionConflict("idempotency key is already active")
                existing_workflow_id = existing["workflow_id"]
                workflow_row = (
                    connection.execute(
                        "SELECT * FROM workflows WHERE id = ?", (existing_workflow_id,)
                    ).fetchone()
                    if existing_workflow_id
                    else None
                )
                if workflow_row is None:
                    connection.rollback()
                    raise QueueTransitionConflict(
                        "workflow idempotency key belongs to a non-workflow job"
                    )
                connection.commit()
                return Workflow.from_row(workflow_row), Job.from_row(existing)
            try:
                self._raise_if_workspace_running(connection, normalized_workspace, sandbox_mode)
                connection.execute(
                    """
                    INSERT INTO workflows (
                        id, chat_id, prompt, workspace, status, current_stage, created_at,
                        external_publication_enabled
                    )
                    VALUES (?, ?, ?, ?, 'queued', 'gpt', ?, ?)
                    """,
                    (
                        workflow_id,
                        str(chat_id),
                        prompt,
                        str(normalized_workspace),
                        created_at,
                        int(external_publication_enabled),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, chat_id, prompt, workspace, provider, sandbox_mode,
                        execution_kind, model, effort, idempotency_key, status, created_at,
                        workflow_id, workflow_stage, workflow_order
                    )
                    VALUES (?, ?, ?, ?, 'codex', ?, 'bridge', ?, ?, ?, 'queued', ?, ?, 'gpt', 1)
                    """,
                    (
                        job_id,
                        str(chat_id),
                        prompt,
                        str(normalized_workspace),
                        sandbox_mode,
                        model,
                        effort,
                        active_key,
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

    def cancel_queued_workflow(self, workflow_id: str, reason: str) -> tuple[Workflow, Job]:
        """Cancel one untouched queued workflow without claiming or notifying it.

        The persisted status remains ``failed`` because the existing schema does not
        have a cancelled state. ``CANCELLED_BY_OPERATOR:`` in ``error`` is the
        machine-readable cancellation marker used by the UI and notification layer.
        """

        normalized_reason = " ".join(str(reason).split())
        if not normalized_reason:
            raise ValueError("cancellation reason is required")
        cancellation_error = f"{CANCELLED_BY_OPERATOR_PREFIX} {normalized_reason}"
        finished_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                workflow_row = connection.execute(
                    "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
                ).fetchone()
                if workflow_row is None:
                    raise QueueTransitionConflict("workflow does not exist")
                if workflow_row["status"] != "queued":
                    raise QueueTransitionConflict(
                        f"workflow is not queued: {workflow_row['status']}"
                    )

                job_rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE workflow_id = ?
                    ORDER BY workflow_order, created_at, id
                    """,
                    (workflow_id,),
                ).fetchall()
                if len(job_rows) != 1 or job_rows[0]["status"] != "queued":
                    raise QueueTransitionConflict(
                        "workflow must contain exactly one queued job"
                    )
                job_row = job_rows[0]

                job_cursor = connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', finished_at = ?, error = ?, exit_code = NULL
                    WHERE id = ? AND workflow_id = ? AND status = 'queued'
                    """,
                    (
                        finished_at,
                        cancellation_error,
                        job_row["id"],
                        workflow_id,
                    ),
                )
                if job_cursor.rowcount != 1:
                    raise QueueTransitionConflict("job changed before cancellation")

                workflow_cursor = connection.execute(
                    """
                    UPDATE workflows
                    SET status = 'failed', current_stage = ?, finished_at = ?, error = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (
                        workflow_row["current_stage"],
                        finished_at,
                        cancellation_error,
                        workflow_id,
                    ),
                )
                if workflow_cursor.rowcount != 1:
                    raise QueueTransitionConflict("workflow changed before cancellation")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        cancelled_workflow = self.get_workflow(workflow_id)
        cancelled_job = self.get(job_row["id"])
        assert cancelled_workflow is not None and cancelled_job is not None
        return cancelled_workflow, cancelled_job

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
            self._reap_stale_external_jobs(connection)
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

    def reap_stale_external_jobs(self) -> int:
        """Fail exited/reused-PID external jobs so they cannot block the queue."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                reaped = self._reap_stale_external_jobs(connection)
                connection.commit()
                return reaped
            except Exception:
                connection.rollback()
                raise

    def requeue_running(self) -> int:
        """Recover bridge jobs left running by a daemon crash or restart.

        External/RDC rows remain registered as running until their owner reports
        completion; requeueing one into the bridge worker could duplicate the
        external process.
        """

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', started_at = NULL,
                    error = 'requeued after worker restart'
                WHERE status = 'running' AND execution_kind = 'bridge'
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

    def finish_external_job(
        self,
        job_id: str,
        *,
        succeeded: bool,
        report_path: Path | None = None,
        error: str | None = None,
        exit_code: int | None = None,
    ) -> bool:
        """Register completion/failure for one running external/RDC job."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT execution_kind, status FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None or row["status"] != "running":
                    connection.commit()
                    return False
                if row["execution_kind"] != DC_EXECUTION_KIND:
                    raise QueueTransitionConflict("job is not an external/RDC execution")
                cursor = connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, finished_at = ?, report_path = ?, error = ?, exit_code = ?
                    WHERE id = ? AND status = 'running' AND execution_kind = 'dc'
                    """,
                    (
                        "succeeded" if succeeded else "failed",
                        _now(),
                        str(report_path) if report_path else None,
                        error,
                        exit_code,
                        job_id,
                    ),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        return Workflow.from_row(row) if row else None

    def reconcile_failed_workflow(
        self,
        workflow_id: str,
        *,
        expected_job_id: str | None = None,
        expected_job_error: str | None = None,
        expected_exit_code: int | None = None,
    ) -> tuple[Workflow, Job]:
        """Finish a stale workflow whose terminal job is already failed.

        This operator repair never claims, reruns, or modifies a job. It accepts
        only an active workflow with no active jobs, exactly one terminal failed
        job, succeeded prior jobs, and a matching current stage. Repeating the
        same repair after the workflow is failed is a no-op.
        """

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                workflow_row = connection.execute(
                    "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
                ).fetchone()
                if workflow_row is None:
                    raise QueueTransitionConflict("workflow does not exist")
                if workflow_row["status"] not in {"queued", "running", "failed"}:
                    raise QueueTransitionConflict(
                        f"workflow is not reconcilable: {workflow_row['status']}"
                    )

                job_rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE workflow_id = ?
                    ORDER BY workflow_order, created_at, id
                    """,
                    (workflow_id,),
                ).fetchall()
                if not job_rows:
                    raise QueueTransitionConflict("workflow has no jobs")
                if any(row["status"] in {"queued", "running"} for row in job_rows):
                    raise QueueTransitionConflict("workflow still has an active job")

                terminal_job = job_rows[-1]
                if terminal_job["status"] != "failed":
                    raise QueueTransitionConflict("terminal workflow job is not failed")
                if any(row["status"] != "succeeded" for row in job_rows[:-1]):
                    raise QueueTransitionConflict(
                        "workflow history before the terminal job is not succeeded"
                    )
                if terminal_job["finished_at"] is None:
                    raise QueueTransitionConflict("terminal failed job has no finish time")
                terminal_stage = terminal_job["workflow_stage"] or "unknown"
                if workflow_row["current_stage"] != terminal_stage:
                    raise QueueTransitionConflict(
                        "workflow current stage does not match terminal failed job"
                    )
                if expected_job_id is not None and terminal_job["id"] != expected_job_id:
                    raise QueueTransitionConflict("terminal job does not match expectation")
                if (
                    expected_job_error is not None
                    and terminal_job["error"] != expected_job_error
                ):
                    raise QueueTransitionConflict("terminal job error does not match expectation")
                if (
                    expected_exit_code is not None
                    and terminal_job["exit_code"] != expected_exit_code
                ):
                    raise QueueTransitionConflict(
                        "terminal job exit code does not match expectation"
                    )

                if workflow_row["status"] in {"queued", "running"}:
                    failure_error = f"{terminal_stage} stage failed"
                    if terminal_job["error"]:
                        failure_error += f": {terminal_job['error']}"
                    cursor = connection.execute(
                        """
                        UPDATE workflows
                        SET status = 'failed',
                            finished_at = COALESCE(finished_at, ?),
                            error = COALESCE(error, ?)
                        WHERE id = ? AND status = ? AND current_stage = ?
                        """,
                        (
                            terminal_job["finished_at"],
                            failure_error,
                            workflow_id,
                            workflow_row["status"],
                            terminal_stage,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise QueueTransitionConflict(
                            "workflow changed before reconciliation"
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        workflow = self.get_workflow(workflow_id)
        job = self.get(terminal_job["id"])
        assert workflow is not None and job is not None
        return workflow, job

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
                    stage_error = f"{completed_job.workflow_stage or 'unknown'} stage failed"
                    if completed_job.error:
                        stage_error += f": {completed_job.error}"
                    connection.execute(
                        """
                        UPDATE workflows
                        SET status = 'failed', finished_at = ?, error = ?, current_stage = ?
                        WHERE id = ?
                        """,
                        (_now(), stage_error, completed_job.workflow_stage or "unknown", workflow_id),
                    )
                    connection.commit()
                    return self.get_workflow(workflow_id), None, False

                stage = completed_job.workflow_stage
                if stage not in WORKFLOW_STAGES:
                    connection.commit()
                    return self.get_workflow(workflow_id), None, False
                next_index = WORKFLOW_STAGES.index(stage) + 1
                if next_index >= len(WORKFLOW_STAGES):
                    # The policy lives on the durable workflow row, rather than
                    # an adapter/UI hint. Anything other than integer 1 is
                    # fail-closed so a malformed/restored row cannot publish.
                    if row["external_publication_enabled"] != 1:
                        github_status = (
                            "skipped_no_external_write"
                            if row["external_publication_enabled"] == 0
                            else "skipped_invalid_external_write_policy"
                        )
                        connection.execute(
                            """
                            UPDATE workflows
                            SET status = 'succeeded', current_stage = 'github', finished_at = ?,
                                github_url = NULL, github_status = ?, error = NULL
                            WHERE id = ?
                            """,
                            (_now(), github_status, workflow_id),
                        )
                        connection.commit()
                        return self.get_workflow(workflow_id), None, False
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
                        id, chat_id, prompt, workspace, provider, sandbox_mode,
                        execution_kind, model, effort, idempotency_key, status, created_at,
                        workflow_id, workflow_stage, workflow_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        next_job_id,
                        row["chat_id"],
                        prompt,
                        row["workspace"],
                        provider,
                        completed_job.sandbox_mode,
                        completed_job.execution_kind,
                        completed_job.model,
                        completed_job.effort,
                        self._derived_idempotency_key(
                            command=f"workflow:{workflow_id}:{next_stage}",
                            chat_id=row["chat_id"],
                            provider=provider,
                            prompt=prompt,
                            workspace=Path(row["workspace"]),
                        ),
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
                WHERE status = 'pending' AND dead_lettered_at IS NULL
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
                WHERE id = ? AND status = 'pending' AND dead_lettered_at IS NULL
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
                WHERE id = ? AND status = 'pending' AND dead_lettered_at IS NULL
                """,
                (last_error, notification_id),
            )
            return cursor.rowcount == 1

    def dead_letter_notification(
        self,
        notification_id: int,
        *,
        last_error: str | None = None,
        increment_attempt: bool = True,
        expected_job_id: str | None = None,
        expected_event_type: str | None = None,
        expected_last_error: str | None = None,
    ) -> Notification:
        """Durably stop a permanently undeliverable pending notification.

        Existing attempts, timestamps, and diagnostic text are retained unless
        the caller explicitly records the current failed delivery attempt. The
        expected fields support guarded operator repair of a known legacy row.
        """

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM notifications WHERE id = ?", (notification_id,)
                ).fetchone()
                if row is None:
                    raise QueueTransitionConflict("notification does not exist")
                if expected_job_id is not None and row["job_id"] != expected_job_id:
                    raise QueueTransitionConflict("notification job does not match expectation")
                if (
                    expected_event_type is not None
                    and row["event_type"] != expected_event_type
                ):
                    raise QueueTransitionConflict(
                        "notification event does not match expectation"
                    )
                if (
                    expected_last_error is not None
                    and row["last_error"] != expected_last_error
                ):
                    raise QueueTransitionConflict(
                        "notification diagnostic does not match expectation"
                    )
                if row["status"] != "pending":
                    raise QueueTransitionConflict(
                        f"notification is not pending: {row['status']}"
                    )
                if row["dead_lettered_at"] is None:
                    cursor = connection.execute(
                        """
                        UPDATE notifications
                        SET dead_lettered_at = ?,
                            attempts = attempts + ?,
                            last_error = CASE WHEN ? IS NULL THEN last_error ELSE ? END
                        WHERE id = ? AND status = 'pending'
                          AND dead_lettered_at IS NULL
                        """,
                        (
                            _now(),
                            1 if increment_attempt else 0,
                            last_error,
                            last_error,
                            notification_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise QueueTransitionConflict(
                            "notification changed before dead-letter transition"
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        notification = self.get_notification(notification_id)
        assert notification is not None
        return notification

    def counts(self) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
        with self._connection() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")
            for row in rows:
                counts[row["status"]] = int(row["count"])
        return counts

    def active_counts_by_provider(self) -> dict[str, dict[str, int]]:
        """Return uncapped queued/running truth grouped by provider."""

        counts: dict[str, dict[str, int]] = {}
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT provider, status, COUNT(*) AS count
                FROM jobs
                WHERE status IN ('queued', 'running')
                GROUP BY provider, status
                """
            )
            for row in rows:
                provider_counts = counts.setdefault(
                    row["provider"], {"queued": 0, "running": 0}
                )
                provider_counts[row["status"]] = int(row["count"])
        return counts
