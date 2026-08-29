"""Validated per-job sandbox modes and provider sandbox launch enforcement.

A job's sandbox_mode is a dispatch-level capability label. Whether a provider
can genuinely enforce it at runtime is decided here: codex enforces natively
through its own CLI flag, claude and agy are wrapped in a bubblewrap
filesystem sandbox, and any combination that cannot be genuinely enforced
fails closed before a process is launched. The mode is never silently
downgraded or upgraded.

Isolation boundary of the bubblewrap wrapper (claude/agy): this is
filesystem and mount-namespace isolation ONLY. It does NOT provide network,
PID, IPC, UTS, seccomp/syscall-filtering, or resource-limit isolation, and
it is not a full container. A wrapped child keeps full network access and
inherits the caller's environment minus the adapter secrets the runner
environment helpers strip. Statements about what a sandboxed job cannot do
must be limited to filesystem writes outside the allowlisted write targets.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Final


DEFAULT_SANDBOX_MODE: Final = "workspace-write"
SANDBOX_MODES: Final = frozenset(
    {
        "read-only",
        "workspace-write",
        "danger-full-access",
    }
)

DEFAULT_BWRAP_BIN: Final = "bwrap"

# Single source of truth for how each provider enforces a requested mode.
SANDBOX_MECHANISM_BY_PROVIDER: Final[dict[str, str]] = {
    "codex": "provider CLI --sandbox flag (native landlock/seccomp)",
    "claude": "bubblewrap filesystem isolation",
    "agy": "bubblewrap filesystem isolation",
}

# Writable harness-state carve-outs relative to the child's HOME, observed as
# the runtime-write targets of each CLI on this host. Everything else on the
# filesystem stays read-only except the workspace (workspace-write only) and a
# fresh private /tmp.
_HARNESS_STATE_DIRS: Final[dict[str, tuple[str, ...]]] = {
    "claude": (".claude", ".cache/claude-cli-nodejs", ".local/state/claude"),
    "agy": (".gemini",),
}

# OAuth credential files are re-bound read-only on top of their writable state
# directories: a sandboxed job can authenticate but never replace credentials.
_CREDENTIAL_FILES_READ_ONLY: Final[dict[str, tuple[str, ...]]] = {
    "claude": (".claude/.credentials.json",),
    "agy": (".gemini/oauth_creds.json",),
}


class SandboxModeError(ValueError):
    """Raised when a job requests an unsupported sandbox mode."""


def validate_sandbox_mode(mode: str) -> str:
    """Return a supported mode, rejecting unknown values without fallback."""

    if not isinstance(mode, str) or mode not in SANDBOX_MODES:
        raise SandboxModeError(f"unsupported sandbox mode: {mode!r}")
    return mode


class SandboxEnforcementError(RuntimeError):
    """A provider cannot genuinely enforce the requested mode; fail closed."""

    def __init__(self, provider: str, sandbox_mode: str, reason: str) -> None:
        self.provider = provider
        self.sandbox_mode = sandbox_mode
        self.reason = reason
        super().__init__(
            f"provider {provider!r} cannot enforce sandbox mode "
            f"{sandbox_mode!r}: {reason}"
        )


@dataclass(frozen=True)
class SandboxLaunchPlan:
    """The verified way one job's provider argv must be launched."""

    provider: str
    sandbox_mode: str
    mechanism: str
    argv_prefix: tuple[str, ...]

    def enforce_argv(self, argv: list[str]) -> list[str]:
        return [*self.argv_prefix, *argv]


def _execute_probe(argv: list[str]) -> Any:
    return subprocess.run(argv, capture_output=True, timeout=10)


_which = shutil.which


def _bwrap_unavailable_reason(
    bwrap_bin: str,
    probe: Callable[[list[str]], Any] | None,
) -> str | None:
    if _which(bwrap_bin) is None:
        return f"sandbox wrapper {bwrap_bin!r} is not installed"
    execute = probe or _execute_probe
    try:
        completed = execute(
            [
                bwrap_bin,
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                "/tmp",
                "--die-with-parent",
                "--",
                "/bin/true",
            ]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"sandbox wrapper self-test failed: {exc.__class__.__name__}"
    returncode = getattr(completed, "returncode", None)
    if returncode != 0:
        return f"sandbox wrapper self-test failed with exit code {returncode}"
    return None


def _bwrap_argv(
    provider: str,
    mode: str,
    workspace: Path,
    home: Path,
    bwrap_bin: str,
) -> tuple[str, ...]:
    argv: list[str] = [
        bwrap_bin,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
    ]
    for relative in _HARNESS_STATE_DIRS[provider]:
        state = home / relative
        if state.is_dir():
            argv += ["--bind", str(state), str(state)]
    for relative in _CREDENTIAL_FILES_READ_ONLY[provider]:
        credential = home / relative
        if credential.is_file():
            argv += ["--ro-bind", str(credential), str(credential)]
    # The workspace bind comes last so it takes precedence over the /tmp tmpfs
    # (workspaces may live under /tmp) and grants writes only in
    # workspace-write mode.
    workspace_flag = "--ro-bind" if mode == "read-only" else "--bind"
    argv += [workspace_flag, str(workspace), str(workspace), "--die-with-parent", "--"]
    return tuple(argv)


def sandbox_launch_plan(
    provider: str,
    sandbox_mode: str,
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
    bwrap_bin: str = DEFAULT_BWRAP_BIN,
    probe: Callable[[list[str]], Any] | None = None,
) -> SandboxLaunchPlan:
    """Resolve how a provider must be launched to enforce a sandbox mode.

    Raises SandboxEnforcementError before any process is launched when the
    requested provider/mode combination cannot be genuinely enforced.
    """

    mode = validate_sandbox_mode(sandbox_mode)
    mechanism = SANDBOX_MECHANISM_BY_PROVIDER.get(provider)
    if mechanism is None:
        raise SandboxEnforcementError(
            provider, mode, "no sandbox enforcement mapping for this provider"
        )
    if provider == "codex":
        return SandboxLaunchPlan(provider, mode, mechanism, ())
    if mode == "danger-full-access":
        return SandboxLaunchPlan(provider, mode, "none (unrestricted by definition)", ())
    reason = _bwrap_unavailable_reason(bwrap_bin, probe)
    if reason is not None:
        raise SandboxEnforcementError(provider, mode, reason)
    environment = os.environ if env is None else env
    home_value = environment.get("HOME") or str(Path.home())
    prefix = _bwrap_argv(provider, mode, Path(workspace), Path(home_value), bwrap_bin)
    return SandboxLaunchPlan(provider, mode, mechanism, prefix)
