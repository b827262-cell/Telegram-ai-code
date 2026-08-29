"""Environment-backed configuration for the v2 job runner."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from .workspaces import (
    WorkspaceRoute,
    WorkspaceRoutingError,
    resolve_route,
    resolve_workspace_path,
    validate_workspace_alias,
)


class ConfigurationError(ValueError):
    """Raised when required runner configuration is missing or unsafe."""


_CHAT_ID_RE = re.compile(r"^-?[0-9]+$")


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _int_env(env: dict[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}")
    return value


def _float_env(env: dict[str, str], name: str, default: float, *, minimum: float = 0.1) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}")
    return value


def _bool_env(env: dict[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")


@dataclass(frozen=True)
class Settings:
    """Validated settings shared by adapters, queue, worker, and runner."""

    root_dir: Path
    data_dir: Path
    queue_db: Path
    report_dir: Path
    allowed_workspaces: tuple[Path, ...]
    default_workspace: Path
    schema_path: Path
    codex_bin: str
    codex_timeout_seconds: float
    worker_poll_seconds: float
    max_prompt_length: int
    telegram_bot_token: str | None
    telegram_allowed_chat_id: str | None
    telegram_api_base: str
    telegram_poll_timeout_seconds: int
    telegram_request_timeout_seconds: int
    meeting_room_url: str
    meeting_api_token: str | None = field(repr=False)
    meeting_connect_timeout_seconds: float
    meeting_read_timeout_seconds: float
    claude_bin: str = "claude"
    claude_timeout_seconds: float = 3600.0
    agy_bin: str = "agy"
    agy_timeout_seconds: float = 3600.0
    github_report_enabled: bool = False
    github_cli_bin: str = "gh"
    github_report_branch: str = "main"
    github_report_directory: str = "reports/auto-loop"
    github_report_timeout_seconds: float = 30.0
    configured_secret_values: tuple[str, ...] = field(default=(), repr=False)
    workspace_aliases: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        root_dir: Path | None = None,
        require_telegram: bool = False,
    ) -> "Settings":
        values = dict(os.environ if env is None else env)
        root = (root_dir or Path(__file__).resolve().parent.parent).resolve()

        raw_workspaces = values.get("CODEX_ALLOWED_WORKSPACES", "")
        workspace_values = [item for item in raw_workspaces.split(os.pathsep) if item]
        if not workspace_values:
            raise ConfigurationError("CODEX_ALLOWED_WORKSPACES is required")
        workspaces = tuple(_path(item) for item in workspace_values)
        if len(set(workspaces)) != len(workspaces):
            raise ConfigurationError("CODEX_ALLOWED_WORKSPACES contains duplicates")
        for workspace in workspaces:
            if not workspace.is_absolute():
                raise ConfigurationError("allowed workspaces must be absolute paths")
            if not workspace.is_dir():
                raise ConfigurationError(f"allowed workspace is not a directory: {workspace}")

        configured_default = values.get("CODEX_DEFAULT_WORKSPACE", "")
        if configured_default:
            default_workspace = _path(configured_default)
        elif len(workspaces) == 1:
            default_workspace = workspaces[0]
        else:
            raise ConfigurationError(
                "CODEX_DEFAULT_WORKSPACE is required when multiple workspaces are allowed"
            )
        if default_workspace not in workspaces:
            raise ConfigurationError("CODEX_DEFAULT_WORKSPACE is not in CODEX_ALLOWED_WORKSPACES")

        # An alias may only ever name a directory the allowlist already permits;
        # resolution here is convenience, never capability expansion.
        workspace_aliases: dict[str, Path] = {}
        raw_aliases = values.get("CODEX_WORKSPACE_ALIASES", "")
        for entry in [item for item in raw_aliases.split(os.pathsep) if item.strip()]:
            alias_token, separator, target = entry.partition("=")
            if not separator:
                raise ConfigurationError(
                    "CODEX_WORKSPACE_ALIASES entries must be alias=/absolute/path"
                )
            try:
                alias_key = validate_workspace_alias(alias_token.strip())
                alias_path = resolve_workspace_path(alias_key, target.strip())
            except WorkspaceRoutingError as exc:
                raise ConfigurationError(f"CODEX_WORKSPACE_ALIASES: {exc}") from exc
            if alias_key in workspace_aliases:
                raise ConfigurationError(f"CODEX_WORKSPACE_ALIASES contains duplicate alias: {alias_key}")
            if alias_path not in workspaces:
                raise ConfigurationError(
                    f"workspace alias {alias_key} is not in CODEX_ALLOWED_WORKSPACES"
                )
            workspace_aliases[alias_key] = alias_path

        data_dir = _path(
            values.get(
                "CODEX_BRIDGE_DATA_DIR",
                str(Path.home() / ".local" / "state" / "gpt-codex-bridge"),
            )
        )
        queue_db = _path(values.get("CODEX_QUEUE_DB", str(data_dir / "jobs.sqlite3")))
        report_dir = _path(values.get("CODEX_REPORT_DIR", str(data_dir / "reports")))
        schema_path = _path(
            values.get("CODEX_REPORT_SCHEMA", str(root / "schemas" / "codex_report.schema.json"))
        )

        token = values.get("TELEGRAM_BOT_TOKEN") or None
        allowed_chat_id = values.get("TELEGRAM_ALLOWED_CHAT_ID") or None
        if require_telegram and not token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is required for Telegram adapter")
        if require_telegram and not allowed_chat_id:
            raise ConfigurationError("TELEGRAM_ALLOWED_CHAT_ID is required for Telegram adapter")
        if allowed_chat_id and not _CHAT_ID_RE.fullmatch(allowed_chat_id):
            raise ConfigurationError("TELEGRAM_ALLOWED_CHAT_ID must be a numeric Telegram chat id")

        configured_secret_values = tuple(
            dict.fromkeys(
                value
                for name, value in values.items()
                if value
                and (
                    "TOKEN" in name
                    or "API_KEY" in name
                    or "PASSWORD" in name
                    or "SECRET" in name
                    or "PRIVATE_KEY" in name
                    or "OAUTH" in name
                )
            )
        )

        return cls(
            root_dir=root,
            data_dir=data_dir,
            queue_db=queue_db,
            report_dir=report_dir,
            allowed_workspaces=workspaces,
            default_workspace=default_workspace,
            schema_path=schema_path,
            codex_bin=values.get("CODEX_BIN", "codex"),
            codex_timeout_seconds=_float_env(values, "CODEX_JOB_TIMEOUT_SECONDS", 3600.0),
            claude_bin=values.get("CLAUDE_BIN", "claude"),
            claude_timeout_seconds=_float_env(values, "CLAUDE_JOB_TIMEOUT_SECONDS", 3600.0),
            agy_bin=values.get("AGY_BIN", "agy"),
            agy_timeout_seconds=_float_env(values, "AGY_JOB_TIMEOUT_SECONDS", 3600.0),
            github_report_enabled=_bool_env(values, "GITHUB_REPORT_ENABLED", False),
            github_cli_bin=values.get("GITHUB_CLI_BIN", "gh"),
            github_report_branch=values.get("GITHUB_REPORT_BRANCH", "main"),
            github_report_directory=values.get("GITHUB_REPORT_DIRECTORY", "reports/auto-loop"),
            github_report_timeout_seconds=_float_env(
                values, "GITHUB_REPORT_TIMEOUT_SECONDS", 30.0
            ),
            worker_poll_seconds=_float_env(values, "CODEX_WORKER_POLL_SECONDS", 1.0),
            max_prompt_length=_int_env(values, "CODEX_MAX_PROMPT_LENGTH", 12000, minimum=1),
            telegram_bot_token=token,
            telegram_allowed_chat_id=allowed_chat_id,
            telegram_api_base=values.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
            telegram_poll_timeout_seconds=_int_env(
                values, "TELEGRAM_POLL_TIMEOUT_SECONDS", 30, minimum=0
            ),
            telegram_request_timeout_seconds=_int_env(
                values, "TELEGRAM_REQUEST_TIMEOUT_SECONDS", 45, minimum=1
            ),
            meeting_room_url=(values.get("MEETING_ROOM_URL") or "http://10.0.3.67:8000").rstrip("/"),
            meeting_api_token=values.get("MEETING_API_TOKEN") or None,
            meeting_connect_timeout_seconds=_float_env(
                values, "MEETING_CONNECT_TIMEOUT_SECONDS", 5.0
            ),
            meeting_read_timeout_seconds=_float_env(
                values, "MEETING_READ_TIMEOUT_SECONDS", 330.0
            ),
            configured_secret_values=configured_secret_values,
            workspace_aliases=workspace_aliases,
        )

    def ensure_runtime_dirs(self) -> None:
        """Create private state directories and verify required static files."""

        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.data_dir, 0o700)
            os.chmod(self.report_dir, 0o700)
        except OSError:
            pass
        if not self.schema_path.is_file():
            raise ConfigurationError(f"report schema does not exist: {self.schema_path}")

    def validate_workspace(self, workspace: Path) -> Path:
        """Normalize and enforce the configured workspace allowlist."""

        normalized = _path(str(workspace))
        if normalized not in self.allowed_workspaces:
            raise ConfigurationError("job workspace is not in CODEX_ALLOWED_WORKSPACES")
        return normalized

    def resolve_workspace_route(
        self,
        *,
        provider: str,
        sandbox_mode: str,
        external_publication_requested: bool = False,
        alias: object = None,
    ) -> WorkspaceRoute:
        """Map one opaque workspace alias to an allowlisted path and enforce policy.

        Every adapter resolves workspaces through here, so an HTTP request and a
        Telegram command cannot disagree about what a named workspace may do.
        """

        route = resolve_route(
            aliases=self.workspace_aliases,
            allowed_workspaces=self.allowed_workspaces,
            default_workspace=self.default_workspace,
            provider=provider,
            sandbox_mode=sandbox_mode,
            external_publication_requested=external_publication_requested,
            alias=alias,
        )
        # Re-check rather than trust the alias table: the allowlist is the
        # containment boundary and stays the authority on every dispatch.
        return WorkspaceRoute(
            alias=route.alias,
            workspace=self.validate_workspace(route.workspace),
            policy=route.policy,
        )

    @property
    def secret_values(self) -> tuple[str, ...]:
        """Known secret values used for report redaction and child-env filtering."""

        values = list(self.configured_secret_values) + [
            self.telegram_bot_token,
            os.environ.get("MCP_BEARER_TOKEN"),
            os.environ.get("CODEX_API_KEY"),
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"),
            os.environ.get("CLAUDE_API_KEY"),
            self.meeting_api_token,
        ]
        return tuple(dict.fromkeys(value for value in values if value))

    def redact_text(self, text: str) -> str:
        """Redact configured credentials and common credential-shaped output."""

        redacted = text
        for secret in self.secret_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        redacted = re.sub(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
            "[REDACTED_PRIVATE_KEY]",
            redacted,
            flags=re.DOTALL,
        )
        redacted = re.sub(
            r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
            r"\1[REDACTED]",
            redacted,
        )
        redacted = re.sub(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            redacted,
        )
        return redacted

    def codex_environment(self) -> dict[str, str]:
        """Return a child environment without inbound adapter credentials."""

        child_env = dict(os.environ)
        for name in ("TELEGRAM_BOT_TOKEN", "MCP_BEARER_TOKEN", "MEETING_API_TOKEN"):
            child_env.pop(name, None)
        return child_env

    def claude_environment(self) -> dict[str, str]:
        """Return a Claude child environment without Telegram/Meeting credentials."""

        child_env = dict(os.environ)
        for name in ("TELEGRAM_BOT_TOKEN", "MCP_BEARER_TOKEN", "MEETING_API_TOKEN"):
            child_env.pop(name, None)
        return child_env

    def agy_environment(self) -> dict[str, str]:
        """Return an agy child environment that relies on its existing OAuth state."""

        child_env = dict(os.environ)
        for name in (
            "TELEGRAM_BOT_TOKEN",
            "MCP_BEARER_TOKEN",
            "MEETING_API_TOKEN",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ):
            child_env.pop(name, None)
        return child_env
