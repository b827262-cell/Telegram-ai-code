"""Authenticated HTTP API for the durable Telegram/Codex job queue."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .config import Settings
from .models import SUPPORTED_PROVIDERS
from .observability import (
    configure_logging,
    get_logger,
    log_event,
    safe_id,
    safe_path,
    safe_error_metadata,
)
from .queue import CodexExecAlreadyRunning, JobQueue, QueueTransitionConflict
from .sandbox import validate_sandbox_mode


MAX_BODY_BYTES = 64 * 1024
MODE_TO_SANDBOX = {
    "read": "read-only",
    "write": "workspace-write",
    "full": "danger-full-access",
    "read-only": "read-only",
    "workspace-write": "workspace-write",
    "danger-full-access": "danger-full-access",
}


def _report_values(settings: Settings, report_path: Path | None) -> dict[str, Any] | None:
    if report_path is None or not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict):
        return None
    tests = report.get("tests", [])
    safe_tests = []
    if isinstance(tests, list):
        for item in tests[:20]:
            if isinstance(item, dict):
                safe_tests.append(
                    {
                        "command": settings.redact_text(str(item.get("command", ""))),
                        "result": str(item.get("result", "")),
                        "output_summary": settings.redact_text(str(item.get("output_summary", ""))),
                    }
                )
    changed_files = report.get("changed_files", [])
    if not isinstance(changed_files, list):
        changed_files = []
    return {
        "status": str(report.get("status", "")),
        "summary": settings.redact_text(str(report.get("summary", "")))[:4000],
        "changed_files": [settings.redact_text(str(item))[:500] for item in changed_files[:30]],
        "tests": safe_tests,
        "git_status": settings.redact_text(str(report.get("git_status", "")))[:2000],
        "needs_attention": bool(report.get("needs_attention")),
    }


def _job_payload(settings: Settings, job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "provider": job.provider,
        "runner": job.runner,
        "sandbox_mode": job.sandbox_mode,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": settings.redact_text(job.error or "") or None,
        "exit_code": job.exit_code,
        "workflow_id": job.workflow_id,
        "workflow_stage": job.workflow_stage,
        "workflow_order": job.workflow_order,
        "report": _report_values(settings, job.report_path),
    }


def _workflow_payload(settings: Settings, queue: JobQueue, workflow: Any) -> dict[str, Any]:
    return {
        "id": workflow.id,
        "status": workflow.status,
        "current_stage": workflow.current_stage,
        "created_at": workflow.created_at,
        "finished_at": workflow.finished_at,
        "github_url": settings.redact_text(workflow.github_url or "") or None,
        "github_status": workflow.github_status,
        "error": settings.redact_text(workflow.error or "") or None,
        "external_publication_enabled": workflow.external_publication_enabled,
        "no_external_write": not workflow.external_publication_enabled,
        "jobs": [_job_payload(settings, job) for job in queue.workflow_jobs(workflow.id)],
    }


class BridgeAPIHandler(BaseHTTPRequestHandler):
    server_version = "CodexBridgeAPI/1"

    @property
    def api_settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def queue(self) -> JobQueue:
        return self.server.queue  # type: ignore[attr-defined]

    @property
    def api_token(self) -> str:
        return self.server.api_token  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        """Suppress BaseHTTPRequestHandler's default line.

        The default handler logs the raw request line, which can contain
        caller-controlled path/query content. Structured, allowlisted events are
        emitted by ``_send`` and the dispatch handlers instead.
        """
        return

    @property
    def logger(self) -> logging.Logger:
        return get_logger()

    def _log(self, event: str, **fields: Any) -> None:
        log_event(self.logger, event, **fields)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        # Log the response code against the sanitized route, never the payload:
        # job reports and error messages may embed workspace or task text.
        code = payload.get("code")
        self._log(
            "http_response",
            method=self.command,
            path=safe_path(urlsplit(self.path).path),
            status=int(status),
            code=code if isinstance(code, str) else None,
            level=logging.WARNING if int(status) >= 500 else logging.INFO,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.api_token}"
        return bool(self.api_token) and hmac.compare_digest(supplied, expected)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if not self._authorized():
                self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": "BRIDGE_UNAUTHORIZED"})
                return
            if path == "/health":
                settings = self.api_settings
                self._send(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "codex-bridge",
                        "api": "v1",
                        "telegramConfigured": bool(settings.telegram_bot_token),
                        "workspaceConfigured": bool(settings.default_workspace),
                        "queue": self.queue.counts(),
                    },
                )
                return
            if path == "/status":
                chat_id = self.api_settings.telegram_allowed_chat_id
                recent = self.queue.recent_for_chat(chat_id, limit=20) if chat_id else []
                workflows = self.queue.recent_workflows_for_chat(chat_id, limit=10) if chat_id else []
                self._send(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "counts": self.queue.counts(),
                        # Unlike ``recent``, this is not capped. The UI uses it
                        # to retire a point-in-time CODEX_EXEC_RUNNING notice.
                        "active_by_provider": self.queue.active_counts_by_provider(),
                        "recent": [_job_payload(self.api_settings, job) for job in recent],
                        "workflows": [
                            _workflow_payload(self.api_settings, self.queue, workflow)
                            for workflow in workflows
                        ],
                    },
                )
                return
            if path.startswith("/workflow/"):
                workflow_id = unquote(path.removeprefix("/workflow/"))
                chat_id = self.api_settings.telegram_allowed_chat_id
                workflow = (
                    self.queue.get_workflow_for_chat(workflow_id, chat_id)
                    if chat_id
                    else None
                )
                if workflow is None:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "code": "WORKFLOW_NOT_FOUND"})
                    return
                self._send(
                    HTTPStatus.OK,
                    {"ok": True, "workflow": _workflow_payload(self.api_settings, self.queue, workflow)},
                )
                return
            if path.startswith("/result/"):
                job_id = unquote(path.removeprefix("/result/"))
                chat_id = self.api_settings.telegram_allowed_chat_id
                job = self.queue.get_for_chat(job_id, chat_id) if chat_id else None
                if job is None:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "code": "JOB_NOT_FOUND"})
                    return
                self._send(HTTPStatus.OK, {"ok": True, "job": _job_payload(self.api_settings, job)})
                return
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "code": "NOT_FOUND"})
        except Exception as exc:
            self._log(
                "bridge_error",
                path=safe_path(path),
                level=logging.ERROR,
                include_traceback=True,
                **safe_error_metadata(exc),
            )
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "code": "BRIDGE_ERROR"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": "BRIDGE_UNAUTHORIZED"})
            return
        path = urlsplit(self.path).path
        is_workflow_cancel = path.startswith("/workflow/") and path.endswith("/cancel")
        if path not in {"/run", "/workflow"} and not is_workflow_cancel:
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "code": "NOT_FOUND"})
            return
        try:
            body = self._read_json()
            settings = self.api_settings
            allowed_chat_id = settings.telegram_allowed_chat_id
            if not allowed_chat_id:
                raise ValueError("TELEGRAM_ALLOWED_CHAT_ID is not configured")
            requested_chat_id = str(body.get("chatId") or body.get("chat_id") or allowed_chat_id)
            requested_user_id = str(body.get("userId") or body.get("user_id") or allowed_chat_id)
            if requested_chat_id != allowed_chat_id or requested_user_id != allowed_chat_id:
                self._send(HTTPStatus.FORBIDDEN, {"ok": False, "code": "USER_NOT_ALLOWED"})
                return
            if not is_workflow_cancel:
                task = body.get("task")
                if not isinstance(task, str) or not task.strip():
                    raise ValueError("task is required")
                if len(task) > settings.max_prompt_length:
                    raise ValueError(f"task exceeds {settings.max_prompt_length} characters")
            if is_workflow_cancel:
                workflow_id = unquote(path.removeprefix("/workflow/").removesuffix("/cancel"))
                if not workflow_id:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "code": "WORKFLOW_NOT_FOUND"})
                    return
                workflow = self.queue.get_workflow_for_chat(workflow_id, allowed_chat_id)
                if workflow is None:
                    self._send(HTTPStatus.NOT_FOUND, {"ok": False, "code": "WORKFLOW_NOT_FOUND"})
                    return
                reason = body.get("reason") or "cancelled from Control Plane"
                if not isinstance(reason, str) or len(reason) > 200:
                    raise ValueError("cancellation reason must be a string of at most 200 characters")
                cancelled_workflow, cancelled_job = self.queue.cancel_queued_workflow(workflow_id, reason)
                self._log(
                    "workflow_cancelled",
                    workflow_id=safe_id(cancelled_workflow.id),
                    job_id=safe_id(cancelled_job.id),
                )
                self._send(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "workflow": _workflow_payload(settings, self.queue, cancelled_workflow),
                        "job": _job_payload(settings, cancelled_job),
                    },
                )
                return
            mode = str(body.get("mode") or "write")
            sandbox_mode = MODE_TO_SANDBOX.get(mode)
            if sandbox_mode is None:
                validate_sandbox_mode(mode)
            workspace = settings.validate_workspace(settings.default_workspace)
            if path == "/workflow":
                no_external_write = body.get("noExternalWrite", False)
                if type(no_external_write) is not bool:
                    raise ValueError("noExternalWrite must be a boolean")
                workflow, job = self.queue.submit_workflow(
                    chat_id=allowed_chat_id,
                    prompt=task.strip(),
                    workspace=workspace,
                    sandbox_mode=sandbox_mode or "workspace-write",
                    external_publication_enabled=not no_external_write,
                )
                self._log(
                    "workflow_dispatched",
                    workflow_id=safe_id(workflow.id),
                    job_id=safe_id(job.id),
                    stage=workflow.current_stage,
                    sandbox_mode=job.sandbox_mode,
                )
                self._send(
                    HTTPStatus.ACCEPTED,
                    {
                        "ok": True,
                        "workflow": _workflow_payload(settings, self.queue, workflow),
                        "job": _job_payload(settings, job),
                    },
                )
                return
            provider = str(body.get("provider") or "codex").lower()
            if provider == "gpt":
                provider = "codex"
            if provider not in SUPPORTED_PROVIDERS:
                raise ValueError(f"unsupported provider: {provider}")
            job = self.queue.submit(
                chat_id=allowed_chat_id,
                prompt=task.strip(),
                workspace=workspace,
                sandbox_mode=sandbox_mode or "workspace-write",
                provider=provider,
            )
            self._log(
                "run_dispatched",
                job_id=safe_id(job.id),
                provider=job.provider,
                sandbox_mode=job.sandbox_mode,
            )
            self._send(HTTPStatus.ACCEPTED, {"ok": True, "job": _job_payload(settings, job)})
        except CodexExecAlreadyRunning as exc:
            # Record which jobs the guard considered running: this is exactly the
            # evidence needed to tell a genuine conflict from a stale one.
            self._log(
                "codex_exec_running_rejected",
                path=safe_path(path),
                running_job_ids=[
                    job_id for job_id in (safe_id(job.id) for job in exc.jobs) if job_id
                ],
                running_count=len(exc.jobs),
                level=logging.WARNING,
            )
            self._send(
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "code": "CODEX_EXEC_RUNNING",
                    "message": "已有 Codex exec 正在執行，/gpt 尚未派送。",
                    "running_jobs": [_job_payload(self.api_settings, job) for job in exc.jobs],
                },
            )
        except QueueTransitionConflict:
            self._send(
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "code": "WORKFLOW_STATE_CONFLICT",
                    "message": "Workflow is no longer queued and was not cancelled.",
                },
            )
        except (ValueError, TypeError) as exc:
            self._log(
                "request_invalid",
                path=safe_path(path),
                level=logging.WARNING,
                **safe_error_metadata(exc),
            )
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "code": "REQUEST_INVALID", "message": str(exc)})
        except Exception as exc:
            # The client only ever sees an opaque BRIDGE_ERROR, but the server
            # keeps a full traceback so the failure is actually diagnosable.
            self._log(
                "bridge_error",
                path=safe_path(path),
                level=logging.ERROR,
                include_traceback=True,
                **safe_error_metadata(exc),
            )
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "code": "BRIDGE_ERROR"})


class BridgeAPIServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: Settings, api_token: str):
        settings.ensure_runtime_dirs()
        self.settings = settings
        self.queue = JobQueue(settings.queue_db)
        self.api_token = api_token
        super().__init__(address, BridgeAPIHandler)


def main() -> int:
    settings = Settings.from_env()
    api_token = os.environ.get("CODEX_BRIDGE_API_TOKEN", "")
    if len(api_token) < 32:
        raise SystemExit("CODEX_BRIDGE_API_TOKEN must be at least 32 characters")
    host = os.environ.get("CODEX_API_HOST", "127.0.0.1")
    port = int(os.environ.get("CODEX_API_PORT", "4300"))
    configure_logging()
    server = BridgeAPIServer((host, port), settings, api_token)
    print(f"Codex Bridge API listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
