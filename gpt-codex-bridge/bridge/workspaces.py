"""Server-side workspace alias resolution and per-workspace routing policy.

Callers select a workspace by opaque alias only; filesystem paths never cross the
adapter boundary. An alias target is resolved once when configuration loads, and
every dispatch re-checks the chosen path against the workspace allowlist.

The per-workspace ceilings below are admission control at the dispatch boundary:
they decide which workspace, provider, sandbox mode and publication a job may be
created with. They do not confine a job that is already running — that remains
whatever the provider CLI itself enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from .models import SUPPORTED_PROVIDERS
from .sandbox import validate_sandbox_mode


# Higher rank means more capability. A workspace policy caps the mode a job may
# request; it never raises it.
SANDBOX_MODE_RANK: dict[str, int] = {
    "read-only": 0,
    "workspace-write": 1,
    "danger-full-access": 2,
}

WORKSPACE_ALIAS_PATTERN: re.Pattern[str] = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


class WorkspaceRoutingError(ValueError):
    """Raised when a requested workspace route is not permitted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspacePolicy:
    """Server-owned capability ceiling for one workspace alias."""

    max_sandbox_mode: str
    allowed_providers: frozenset[str]
    publication_allowed: bool

    def __post_init__(self) -> None:
        validate_sandbox_mode(self.max_sandbox_mode)
        unknown = self.allowed_providers - SUPPORTED_PROVIDERS
        if unknown:
            raise ValueError(f"unknown providers in workspace policy: {sorted(unknown)}")


# Ceiling for an alias the operator configured but the server has no reviewed
# policy for. Deliberately the floor: adding an alias to configuration grants
# read-only analysis, never the ability to modify a tree or publish.
DEFAULT_WORKSPACE_POLICY: WorkspacePolicy = WorkspacePolicy(
    max_sandbox_mode="read-only",
    allowed_providers=frozenset(SUPPORTED_PROVIDERS),
    publication_allowed=False,
)

# Ceiling applied when no alias is available for a workspace at all, which is the
# pre-routing behavior of every entry point. Keeping it distinct means routing
# support cannot change existing dispatches that omit the field.
LEGACY_WORKSPACE_POLICY: WorkspacePolicy = WorkspacePolicy(
    max_sandbox_mode="danger-full-access",
    allowed_providers=frozenset(SUPPORTED_PROVIDERS),
    publication_allowed=True,
)

# Reviewed per-alias ceilings. Self-modifying control-plane work is capped below
# full access and never gains report publication as a side effect of editing the
# bridge itself.
WORKSPACE_POLICIES: dict[str, WorkspacePolicy] = {
    "bridge": WorkspacePolicy(
        max_sandbox_mode="workspace-write",
        allowed_providers=frozenset(SUPPORTED_PROVIDERS),
        publication_allowed=False,
    ),
    "meeting-room": LEGACY_WORKSPACE_POLICY,
}


def workspace_policy(alias: str) -> WorkspacePolicy:
    return WORKSPACE_POLICIES.get(alias, DEFAULT_WORKSPACE_POLICY)


def validate_workspace_alias(alias: object) -> str:
    """Accept only an opaque alias token, never a caller-supplied path.

    The character class excludes ``/``, ``.``, ``~`` and whitespace, so absolute
    paths, traversal and encoded traversal cannot reach the resolver.
    """

    if not isinstance(alias, str):
        raise WorkspaceRoutingError(
            "WORKSPACE_ALIAS_INVALID", "workspace alias must be a string"
        )
    normalized = alias.strip().lower()
    if not normalized:
        raise WorkspaceRoutingError("WORKSPACE_ALIAS_INVALID", "workspace alias must not be empty")
    if not WORKSPACE_ALIAS_PATTERN.fullmatch(normalized):
        raise WorkspaceRoutingError(
            "WORKSPACE_ALIAS_INVALID",
            "workspace alias must be a lowercase slug using letters, digits and hyphens",
        )
    return normalized


def resolve_workspace_path(alias: str, target: str) -> Path:
    """Resolve one configured alias target to a canonical directory path."""

    normalized_alias = validate_workspace_alias(alias)
    # Checked before resolve(): resolving a relative target would silently bind it
    # to whichever working directory the service happens to start in.
    expanded = Path(target).expanduser()
    if not expanded.is_absolute():
        raise WorkspaceRoutingError(
            "WORKSPACE_ALIAS_TARGET_INVALID",
            f"workspace alias {normalized_alias!r} target must be absolute",
        )
    resolved = expanded.resolve(strict=False)
    if not resolved.is_dir():
        raise WorkspaceRoutingError(
            "WORKSPACE_ALIAS_TARGET_INVALID",
            f"workspace alias {normalized_alias!r} target is not a directory",
        )
    return resolved


def enforce_route(
    *,
    alias: str,
    policy: WorkspacePolicy,
    provider: str,
    sandbox_mode: str,
    external_publication_requested: bool,
) -> None:
    """Reject any request exceeding the server policy for this workspace."""

    mode = validate_sandbox_mode(sandbox_mode)
    if provider not in policy.allowed_providers:
        raise WorkspaceRoutingError(
            "WORKSPACE_PROVIDER_DENIED",
            f"provider {provider!r} is not permitted for workspace {alias!r}",
        )
    if SANDBOX_MODE_RANK[mode] > SANDBOX_MODE_RANK[policy.max_sandbox_mode]:
        raise WorkspaceRoutingError(
            "WORKSPACE_SANDBOX_DENIED",
            f"workspace {alias!r} allows at most {policy.max_sandbox_mode!r}, "
            f"not {mode!r}",
        )
    if external_publication_requested and not policy.publication_allowed:
        raise WorkspaceRoutingError(
            "WORKSPACE_PUBLICATION_DENIED",
            f"workspace {alias!r} does not permit external report publication; "
            "dispatch with noExternalWrite=true",
        )


@dataclass(frozen=True)
class WorkspaceRoute:
    alias: str
    workspace: Path
    policy: WorkspacePolicy


def resolve_route(
    *,
    aliases: dict[str, Path],
    allowed_workspaces: tuple[Path, ...],
    default_workspace: Path,
    provider: str,
    sandbox_mode: str,
    external_publication_requested: bool,
    alias: object = None,
) -> WorkspaceRoute:
    """Choose one workspace and enforce its policy. Single source of truth for
    every adapter, so HTTP and Telegram cannot drift apart."""

    if alias is None:
        # Pre-routing contract: the caller named no workspace, so the default
        # applies. Its policy still governs, which keeps a future change of
        # CODEX_DEFAULT_WORKSPACE from silently inheriting full capability.
        selected = next((name for name, path in aliases.items() if path == default_workspace), None)
        policy = workspace_policy(selected) if selected else LEGACY_WORKSPACE_POLICY
        enforce_route(
            alias=selected or "default",
            policy=policy,
            provider=provider,
            sandbox_mode=sandbox_mode,
            external_publication_requested=external_publication_requested,
        )
        return WorkspaceRoute(alias=selected or "", workspace=default_workspace, policy=policy)

    normalized = validate_workspace_alias(alias)
    if normalized not in aliases:
        raise WorkspaceRoutingError(
            "WORKSPACE_ALIAS_UNKNOWN", f"unknown workspace alias: {normalized}"
        )
    workspace = aliases[normalized]
    if workspace not in allowed_workspaces:
        raise WorkspaceRoutingError(
            "WORKSPACE_NOT_ALLOWED",
            f"workspace alias {normalized} is not present in CODEX_ALLOWED_WORKSPACES",
        )
    policy = workspace_policy(normalized)
    enforce_route(
        alias=normalized,
        policy=policy,
        provider=provider,
        sandbox_mode=sandbox_mode,
        external_publication_requested=external_publication_requested,
    )
    return WorkspaceRoute(alias=normalized, workspace=workspace, policy=policy)
