"""Structured, secret-safe logging for the Bridge HTTP API.

Every field emitted here is an allowlisted scalar: an event name, an HTTP
method/path/status, a queue identifier, or a code-defined error class/frame.
Request bodies, prompts, Authorization headers, bot tokens and chat IDs are
never accepted by these helpers, so they cannot leak through a log line.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from typing import Any


LOGGER_NAME = "bridge.api"

# Bridge job/workflow identifiers are generated as `job-<hex16>` / `flow-<hex16>`
# (see JobQueue.submit / submit_workflow). Anything not matching that shape is
# dropped rather than logged, so a caller-controlled string can never be echoed.
_SAFE_ID = re.compile(r"^(?:job|flow)-[0-9a-f]{1,32}$")

# Only these request paths are ever logged verbatim. A path outside the
# allowlist is reduced to a constant so unknown/probing URLs (which may carry
# injected content or tokens in a query string) never reach the log.
_KNOWN_PATHS = frozenset(
    {"/health", "/status", "/run", "/workflow", "/result", "/workflow/", "/result/"}
)

_SAFE_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SAFE_METHODS = frozenset({"GET", "POST"})
_SAFE_PATHS = frozenset(
    {
        "/health",
        "/status",
        "/run",
        "/workflow",
        "/result",
        "/result/",
        "/workflow/",
        "/result/<id>",
        "/workflow/<id>",
        "/workflow/<id>/cancel",
        "<other>",
        "<invalid>",
    }
)
_SAFE_EVENTS: dict[str, frozenset[str]] = {
    "http_response": frozenset({"method", "path", "status", "code"}),
    "workflow_dispatched": frozenset({"workflow_id", "job_id", "stage", "sandbox_mode"}),
    "workflow_cancelled": frozenset({"workflow_id", "job_id"}),
    "run_dispatched": frozenset({"job_id", "provider", "sandbox_mode"}),
    "codex_exec_running_rejected": frozenset({"path", "running_job_ids", "running_count"}),
    "request_invalid": frozenset({"path", "error_class"}),
    "bridge_error": frozenset({"path", "error_class", "traceback_frames"}),
}


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def safe_id(value: Any) -> str | None:
    """Return an identifier only when it matches the generated ID shape."""

    if not isinstance(value, str):
        return None
    return value if _SAFE_ID.match(value) else None


def safe_path(path: Any) -> str:
    """Collapse a request path to an allowlisted, parameter-free form."""

    if not isinstance(path, str):
        return "<invalid>"
    if path in _KNOWN_PATHS:
        return path
    # Keep the route family but drop the caller-supplied identifier segment.
    for prefix in ("/result/", "/workflow/"):
        if path.startswith(prefix):
            return f"{prefix}<id>"
    return "<other>"


def safe_error_metadata(exc: BaseException) -> dict[str, str]:
    """Return code-defined exception metadata without calling ``str(exc)``.

    Exception messages can contain arbitrary request body or prompt text.  No
    amount of pattern redaction can prove that such text is non-secret, so the
    structured log records only the exception class.
    """

    error_class = type(exc).__name__
    return {"error_class": error_class if _SAFE_TOKEN.fullmatch(error_class) else "Exception"}


def _safe_traceback_frames() -> list[dict[str, str | int]]:
    """Capture diagnostic stack locations, never exception text or locals."""

    frames = []
    for frame in traceback.extract_tb(sys.exc_info()[2])[-20:]:
        filename = frame.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        frames.append(
            {
                "file": filename[:128] if _SAFE_TOKEN.fullmatch(filename) else "<other>",
                "line": int(frame.lineno),
                "function": frame.name[:128] if _SAFE_TOKEN.fullmatch(frame.name) else "<other>",
            }
        )
    return frames


def _safe_field(key: str, value: Any) -> Any | None:
    if key in {"status", "running_count"}:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    if key in {"job_id", "workflow_id"}:
        return safe_id(value)
    if key == "running_job_ids" and isinstance(value, list):
        return [item for item in (safe_id(candidate) for candidate in value) if item]
    if key == "method":
        return value if value in _SAFE_METHODS else None
    if key == "path":
        return value if value in _SAFE_PATHS else None
    if key in {"code", "provider", "sandbox_mode", "stage", "error_class"}:
        return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) else None
    return None


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    include_traceback: bool = False,
    **fields: Any,
) -> None:
    """Emit one JSON log line built only from allowlisted, non-secret fields.

    When ``include_traceback`` is set, only code locations are recorded. Raw
    traceback rendering is forbidden because exception text can contain request
    bodies, prompts, credentials, or other caller-controlled content.
    """

    if event not in _SAFE_EVENTS:
        return
    payload: dict[str, Any] = {"event": event}
    for key in _SAFE_EVENTS[event]:
        value = _safe_field(key, fields.get(key))
        if value is not None:
            payload[key] = value

    if include_traceback and "traceback_frames" in _SAFE_EVENTS[event]:
        payload["traceback_frames"] = _safe_traceback_frames()

    logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def configure_logging() -> logging.Logger:
    """Attach a stderr handler once so structured lines survive in the journal."""

    logger = get_logger()
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
