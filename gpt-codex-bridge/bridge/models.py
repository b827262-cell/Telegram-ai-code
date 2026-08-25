"""Data models used by the persistent job runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .sandbox import DEFAULT_SANDBOX_MODE, validate_sandbox_mode

DEFAULT_PROVIDER = "codex"
SUPPORTED_PROVIDERS = frozenset({"agy", "codex", "claude"})
WORKFLOW_STAGES = ("gpt", "agy", "claude")


def validate_provider(provider: str) -> str:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported job provider: {provider}")
    return provider


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

    def __post_init__(self) -> None:
        validate_sandbox_mode(self.sandbox_mode)
        validate_provider(self.provider)

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

    @classmethod
    def from_row(cls, row: object) -> "Workflow":
        data = dict(row)
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
