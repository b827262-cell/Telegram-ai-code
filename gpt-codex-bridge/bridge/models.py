"""Data models used by the persistent job runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .sandbox import DEFAULT_SANDBOX_MODE, validate_sandbox_mode

DEFAULT_PROVIDER = "codex"
SUPPORTED_PROVIDERS = frozenset({"agy", "codex", "claude"})
DEFAULT_EXECUTION_KIND = "bridge"
DC_EXECUTION_KIND = "dc"
SUPPORTED_EXECUTION_KINDS = frozenset({DEFAULT_EXECUTION_KIND, DC_EXECUTION_KIND})
WORKFLOW_STAGES = ("gpt", "agy", "claude")


def validate_external_publication_enabled(value: object) -> bool:
    """Accept only an explicit boolean workflow publication policy.

    A malformed value must never be interpreted as permission to publish an
    external report. Database callers use the same rule before invoking the
    GitHub writer.
    """

    if type(value) is not bool:
        raise ValueError("external publication policy must be a boolean")
    return value


def validate_provider(provider: str) -> str:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported job provider: {provider}")
    return provider


def validate_execution_kind(execution_kind: str) -> str:
    if execution_kind not in SUPPORTED_EXECUTION_KINDS:
        raise ValueError(f"unsupported execution kind: {execution_kind}")
    return execution_kind


@dataclass(frozen=True)
class Job:
    id: str
    chat_id: str
    prompt: str
    workspace: Path
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    report_path: Path | None = None
    error: str | None = None
    exit_code: int | None = None
    sandbox_mode: str = DEFAULT_SANDBOX_MODE
    provider: str = DEFAULT_PROVIDER
    workflow_id: str | None = None
    workflow_stage: str | None = None
    workflow_order: int | None = None
    execution_kind: str = DEFAULT_EXECUTION_KIND
    pid: int | None = None
    device_id: str | None = None
    model: str | None = None
    effort: str | None = None
    idempotency_key: str | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        validate_sandbox_mode(self.sandbox_mode)
        validate_provider(self.provider)
        validate_execution_kind(self.execution_kind)

    @property
    def runner(self) -> str:
        """Compatibility name for the executable runner selected by provider."""

        return self.provider

    @classmethod
    def from_row(cls, row: object) -> "Job":
        data = dict(row)
        report = data.get("report_path")
        return cls(
            id=data["id"],
            chat_id=str(data["chat_id"]),
            prompt=data["prompt"],
            workspace=Path(data["workspace"]),
            status=data["status"],
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            report_path=Path(report) if report else None,
            error=data.get("error"),
            exit_code=int(data["exit_code"]) if data.get("exit_code") is not None else None,
            sandbox_mode=data["sandbox_mode"],
            provider=data.get("provider", DEFAULT_PROVIDER),
            workflow_id=data.get("workflow_id"),
            workflow_stage=data.get("workflow_stage"),
            workflow_order=(
                int(data["workflow_order"])
                if data.get("workflow_order") is not None
                else None
            ),
            execution_kind=data.get("execution_kind", DEFAULT_EXECUTION_KIND),
            pid=int(data["pid"]) if data.get("pid") is not None else None,
            device_id=data.get("device_id"),
            model=data.get("model"),
            effort=data.get("effort"),
            idempotency_key=data.get("idempotency_key"),
            attempts=int(data.get("attempts", 0) or 0),
        )


@dataclass(frozen=True)
class Workflow:
    id: str
    chat_id: str
    prompt: str
    workspace: Path
    status: str
    current_stage: str
    created_at: str
    finished_at: str | None = None
    github_url: str | None = None
    github_status: str | None = None
    error: str | None = None
    external_publication_enabled: bool = True

    @classmethod
    def from_row(cls, row: object) -> "Workflow":
        data = dict(row)
        raw_external_publication = data.get("external_publication_enabled", 1)
        if raw_external_publication not in (0, 1, False, True):
            raise ValueError("persisted external publication policy is invalid")
        return cls(
            id=data["id"],
            chat_id=str(data["chat_id"]),
            prompt=data["prompt"],
            workspace=Path(data["workspace"]),
            status=data["status"],
            current_stage=data["current_stage"],
            created_at=data["created_at"],
            finished_at=data.get("finished_at"),
            github_url=data.get("github_url"),
            github_status=data.get("github_status"),
            error=data.get("error"),
            external_publication_enabled=bool(raw_external_publication),
        )


@dataclass(frozen=True)
class Notification:
    id: int
    job_id: str
    chat_id: str
    event_type: str
    status: str
    attempts: int
    created_at: str
    sent_at: str | None = None
    last_error: str | None = None
    dead_lettered_at: str | None = None

    @classmethod
    def from_row(cls, row: object) -> "Notification":
        data = dict(row)
        dead_lettered_at = data.get("dead_lettered_at")
        return cls(
            id=int(data["id"]),
            job_id=data["job_id"],
            chat_id=str(data["chat_id"]),
            event_type=data["event_type"],
            status="dead_lettered" if dead_lettered_at else data["status"],
            attempts=int(data["attempts"]),
            created_at=data["created_at"],
            sent_at=data.get("sent_at"),
            last_error=data.get("last_error"),
            dead_lettered_at=dead_lettered_at,
        )
