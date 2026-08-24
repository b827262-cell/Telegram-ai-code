"""Telegram Bot API long-polling adapter and notification outbox drainer.

This module authenticates, enqueues jobs, and sends notifications. It never starts a provider CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import re
import signal
import time
from typing import Any, Callable
from urllib import request

from bridge.config import Settings
from bridge.meeting import (
    MeetingRoomClient,
    MeetingRoomError,
    agent_replies,
    meeting_payload,
    response_text,
    summary_text,
)
from bridge.models import Job, Notification
from bridge.queue import JobQueue
from bridge.sandbox import (
    DEFAULT_SANDBOX_MODE,
    SandboxModeError,
    validate_sandbox_mode,
)


class TelegramAPIError(RuntimeError):
    """Raised when the Telegram API returns an unsuccessful response."""


@dataclass
class TelegramClient:
    token: str
    api_base: str = "https://api.telegram.org"
    request_timeout: int = 45
    transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None

    def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None:
            response = self.transport(method, payload)
        else:
            body = json.dumps(payload).encode("utf-8")
            url = f"{self.api_base.rstrip('/')}/bot{self.token}/{method}"
            http_request = request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(http_request, timeout=self.request_timeout) as result:
                response = json.loads(result.read().decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise TelegramAPIError("Telegram API request failed")
        return response

    def delete_webhook(self) -> None:
        self._request("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload)
        updates = result.get("result", [])
        return updates if isinstance(updates, list) else []

    def send_message(self, chat_id: str, text: str) -> None:
        self._request("sendMessage", {"chat_id": chat_id, "text": text})


_COMMAND_RE = re.compile(r"^/(?P<command>[A-Za-z0-9_-]+)(?:@[A-Za-z0-9_]+)?(?:\s+(?P<args>.*))?$", re.S)

_RUN_COMMAND_MODES = {
    "run": DEFAULT_SANDBOX_MODE,
    "run-read": "read-only",
    "run-full": "danger-full-access",
}

_MEETING_COMMANDS = {
    "hermes",
    "gemini",
    "all",
    "roundtable",
    "agents",
    "meeting-status",
    "meeting-stop",
    "meeting-reset",
}

_TELEGRAM_MESSAGE_LIMIT = 4096


def split_telegram_message(text: str, *, limit: int = _TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split long replies at line boundaries, then hard-wrap if necessary."""

    if limit < 1:
        raise ValueError("Telegram message limit must be positive")
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
            continue
        if current:
            chunks.append(current.rstrip("\n"))
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks or [""]


class TelegramAdapter:
    """Validate Telegram messages, enqueue jobs, and return sanitized results."""

    def __init__(
        self,
        settings: Settings,
        queue: JobQueue,
        client: TelegramClient,
        meeting_client: MeetingRoomClient | None = None,
    ):
        if not settings.telegram_allowed_chat_id:
            raise ValueError("Telegram allowed chat id is not configured")
        self.settings = settings
        self.queue = queue
        self.client = client
        self.meeting_client = meeting_client or MeetingRoomClient(
            base_url=settings.meeting_room_url,
            api_token=settings.meeting_api_token,
            connect_timeout_seconds=settings.meeting_connect_timeout_seconds,
            read_timeout_seconds=settings.meeting_read_timeout_seconds,
        )

    def _authorized(self, chat_id: Any) -> bool:
        return str(chat_id) == self.settings.telegram_allowed_chat_id

    @staticmethod
    def _message(update: dict[str, Any]) -> dict[str, Any] | None:
        message = update.get("message")
        return message if isinstance(message, dict) else None

    async def handle_update_async(self, update: dict[str, Any]) -> str | None:
        """Handle one update; unauthorized updates produce no response."""

        message = self._message(update)
        if message is None:
            return None
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            return None
        chat_id = str(chat["id"])

        # This check deliberately happens before text parsing or any queue operation.
        if not self._authorized(chat_id):
            return None

        text = message.get("text")
        if not isinstance(text, str):
            return None
        match = _COMMAND_RE.fullmatch(text.strip())
        if not match:
            reply = "請使用 /ping、/run <task>、/gpt <task>、/agy <task>、/claude <task>、/run-read <task>、/run-full <task>、/status、/result <job_id> 或 /workflow <flow_id>。"
            self._send_reply(chat_id, reply)
            return reply
        command = match.group("command").lower()
        args = (match.group("args") or "").strip()

        if command in {"start", "help"}:
            reply = (
                "Codex job runner 已啟動。使用 /ping、/run <task>、/run-read <task>、"
                "/run-full <task>、/gpt <task>、/agy <task>、/claude <task>、/status、"
                "/result <job_id>、/workflow <flow_id>。/gpt 會依序接 AGY、Claude，最後上傳 GitHub 報告。"
            )
        elif command == "ping":
            reply = "PONG"
        elif command == "status":
            reply = self._status(chat_id)
        elif command in _RUN_COMMAND_MODES:
            reply = self._submit(chat_id, args, _RUN_COMMAND_MODES[command], command)
        elif command == "gpt":
            reply = self._submit_workflow(chat_id, args)
        elif command == "claude":
            reply = self._submit(
                chat_id,
                args,
                DEFAULT_SANDBOX_MODE,
                command,
                provider="claude",
            )
        elif command == "agy":
            reply = self._submit(
                chat_id,
                args,
                DEFAULT_SANDBOX_MODE,
                command,
                provider="agy",
            )
        elif command == "result":
            reply = self._result(chat_id, args)
        elif command == "workflow":
            reply = self._workflow_result(chat_id, args)
        elif command in _MEETING_COMMANDS:
            reply = await self._meeting_reply(message, chat_id, command, args)
        else:
            reply = (
                "未知命令。可用：/ping、/run、/gpt、/agy、/claude、/run-read、/run-full、/status、/result、/workflow、"
                "/hermes、/gemini、/all、/roundtable、/agents、/meeting-status、"
                "/meeting-stop、/meeting-reset。"
            )

        self._send_reply(chat_id, reply)
        return reply

    def handle_update(self, update: dict[str, Any]) -> str | None:
        """Synchronous compatibility wrapper used by the existing poller/tests."""

        return asyncio.run(self.handle_update_async(update))

    def _send_reply(self, chat_id: str, text: str) -> None:
        text = self.settings.redact_text(text)
        for chunk in split_telegram_message(text):
            self.client.send_message(chat_id, chunk)

    @staticmethod
    def _user_id(message: dict[str, Any], chat_id: str) -> Any:
        sender = message.get("from")
        if isinstance(sender, dict) and sender.get("id") is not None:
            return sender["id"]
        return chat_id

    @staticmethod
    def _agent_label(agent: str) -> str:
        return {"hermes": "☤ Hermes", "gpt": "🟢 GPT", "gemini": "🔵 Gemini"}[agent]

    def _meeting_error_reply(self, error: MeetingRoomError) -> str:
        if error.kind == "unavailable":
            return "⚠️ AI Meeting Room unavailable\nTUF A16 10.0.3.67 is not reachable."
        if error.kind == "auth":
            return "⚠️ Meeting Room authentication failed."
        if error.kind == "internal":
            return "⚠️ Meeting Room internal error."
        if error.kind == "timeout":
            return "⚠️ Meeting Room request timed out."
        return "⚠️ Meeting Room request failed."

    def _render_agent_reply(self, agent: str, data: Any) -> str:
        text = response_text(data) or agent_replies(data).get(agent, "")
        return f"{self._agent_label(agent)}\n{text or '（無回覆）'}"

    def _render_group_reply(self, data: Any, *, roundtable: bool = False) -> str:
        replies = agent_replies(data)
        parts = [
            self._agent_label(agent) + "\n" + (replies[agent] or "（無回覆）")
            for agent in ("hermes", "gpt", "gemini")
        ]
        if roundtable:
            parts.append("🎯 Summary\n" + (summary_text(data) or "（無摘要）"))
        return "\n\n".join(parts)

    async def _meeting_reply(
        self,
        message: dict[str, Any],
        chat_id: str,
        command: str,
        args: str,
    ) -> str:
        user_id = self._user_id(message, chat_id)
        chat_value = message.get("chat", {}).get("id", chat_id)
        payload = meeting_payload(
            chat_id=str(chat_value),
            user_id=str(user_id),
            message=args,
        )
        try:
            if command == "meeting-status":
                try:
                    await self.meeting_client.health()
                except MeetingRoomError as exc:
                    if exc.kind == "unavailable":
                        return "Meeting Room: OFFLINE\n" + self._meeting_error_reply(exc)
                    if exc.kind == "auth":
                        return "Meeting Room: AUTH FAILED\n" + self._meeting_error_reply(exc)
                    raise
                return "Meeting Room: READY"
            if command == "agents":
                data = await self.meeting_client.agents()
                return "🤖 Meeting Room Agents\n" + (response_text(data) or str(data))
            if command in {"hermes", "gemini"}:
                if not args:
                    return f"用法：/{command} <message>"
                data = await self.meeting_client.ask(command, payload)
                return self._render_agent_reply(command, data)
            if command == "all":
                if not args:
                    return "用法：/all <message>"
                data = await self.meeting_client.all(payload)
                return self._render_group_reply(data)
            if command == "roundtable":
                if not args:
                    return "用法：/roundtable <message>"
                data = await self.meeting_client.roundtable(payload)
                return "📋 AI Meeting\n\n" + self._render_group_reply(data, roundtable=True)
            if command == "meeting-stop":
                data = await self.meeting_client.stop(payload)
                return "🛑 Meeting stopped\n" + (response_text(data) or "OK")
            if command == "meeting-reset":
                data = await self.meeting_client.reset(payload)
                return "♻️ Meeting reset\n" + (response_text(data) or "OK")
        except MeetingRoomError as exc:
            return self._meeting_error_reply(exc)
        return "⚠️ Unknown Meeting Room command."

    def _status(self, chat_id: str) -> str:
        counts = self.queue.counts()
        lines = [
            f"queued={counts['queued']} running={counts['running']} "
            f"succeeded={counts['succeeded']} failed={counts['failed']}"
        ]
        jobs = self.queue.recent_for_chat(chat_id)
        if jobs:
            lines.append("recent jobs:")
            lines.extend(
                f"{job.id}: {job.status} provider={job.provider} runner={job.runner} "
                f"sandbox_mode={job.sandbox_mode}"
                for job in jobs
            )
        else:
            lines.append("sandbox_mode=none")
        workflows = self.queue.recent_workflows_for_chat(chat_id)
        if workflows:
            lines.append("recent workflows:")
            lines.extend(
                f"{workflow.id}: {workflow.status} stage={workflow.current_stage} "
                f"github={workflow.github_status or 'pending'}"
                for workflow in workflows
            )
        return "\n".join(lines)

    def _submit_workflow(self, chat_id: str, prompt: str) -> str:
        if not prompt:
            return "用法：/gpt <task>（完成後自動接 /agy、/claude 並上傳 GitHub 報告）"
        if len(prompt) > self.settings.max_prompt_length:
            return f"task 太長；上限為 {self.settings.max_prompt_length} 字元。"
        workflow, job = self.queue.submit_workflow(
            chat_id=chat_id,
            prompt=prompt,
            workspace=self.settings.default_workspace,
            sandbox_mode=DEFAULT_SANDBOX_MODE,
        )
        return (
            f"workflow queued {workflow.id}\n"
            f"stage=gpt job={job.id}\n"
            "next=agy → claude → github report"
        )

    def _submit(
        self,
        chat_id: str,
        prompt: str,
        sandbox_mode: str,
        command: str,
        *,
        provider: str = "codex",
    ) -> str:
        if not prompt:
            return f"用法：/{command} <task>"
        if len(prompt) > self.settings.max_prompt_length:
            return f"task 太長；上限為 {self.settings.max_prompt_length} 字元。"
        try:
            sandbox_mode = validate_sandbox_mode(sandbox_mode)
        except SandboxModeError:
            return "sandbox mode 無效；job 已拒絕。"
        if sandbox_mode == "danger-full-access" and not self._authorized(chat_id):
            return "未授權使用 danger-full-access。"
        # Workspace is selected only from validated configuration. Telegram text
        # is never interpreted as a filesystem path or command line.
        job = self.queue.submit(
            chat_id=chat_id,
            prompt=prompt,
            workspace=self.settings.default_workspace,
            sandbox_mode=sandbox_mode,
            provider=provider,
        )
        return f"queued {job.id} provider={job.provider} runner={job.runner}"

    def _result(self, chat_id: str, job_id: str) -> str:
        if not job_id or not re.fullmatch(r"job-[a-f0-9]{16}", job_id):
            return "用法：/result <job_id>"
        job = self.queue.get_for_chat(job_id, chat_id)
        if job is None:
            return "找不到這個 job。"
        if job.report_path is None or not job.report_path.is_file():
            return (
                f"{job.id}: {job.status} provider={job.provider} runner={job.runner} "
                f"sandbox_mode={job.sandbox_mode}"
            )
        report = self._load_report(job)
        if report is None:
            return (
                f"{job.id}: {job.status} provider={job.provider} runner={job.runner} "
                f"sandbox_mode={job.sandbox_mode}（report 尚未可讀取）"
            )
        summary, changed_text, attention = self._report_values(report)
        return (
            f"{job.id}: {report.get('status', job.status)}\n"
            f"provider: {job.provider}\n"
            f"runner: {job.runner}\n"
            f"sandbox_mode: {job.sandbox_mode}\n"
            f"summary: {summary[:1000]}\n"
            f"changed_files: {changed_text}\n"
            f"needs_attention: {attention}"
        )

    def _workflow_result(self, chat_id: str, workflow_id: str) -> str:
        if not workflow_id or not re.fullmatch(r"flow-[a-f0-9]{16}", workflow_id):
            return "用法：/workflow <flow_id>"
        workflow = self.queue.get_workflow_for_chat(workflow_id, chat_id)
        if workflow is None:
            return "找不到這個 workflow。"
        lines = [
            f"{workflow.id}: {workflow.status}",
            f"current_stage: {workflow.current_stage}",
        ]
        if workflow.github_status:
            lines.append(f"github_report: {workflow.github_status}")
        if workflow.github_url:
            lines.append(f"github_url: {workflow.github_url}")
        if workflow.error:
            lines.append(f"error: {self._safe_short(workflow.error)}")
        for job in self.queue.workflow_jobs(workflow.id):
            lines.append(
                f"{job.workflow_stage or job.provider}: {job.id} {job.status}"
            )
        return "\n".join(lines)

    @staticmethod
    def _load_report(job: Job) -> dict[str, Any] | None:
        if job.report_path is None or not job.report_path.is_file():
            return None
        try:
            report = json.loads(job.report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return report if isinstance(report, dict) else None

    def _report_values(self, report: dict[str, Any]) -> tuple[str, str, str]:
        summary = self._safe_short(report.get("summary", "無摘要"), limit=1000)
        changed = report.get("changed_files", [])
        if isinstance(changed, list):
            changed_items = [self._safe_short(item, limit=300) for item in changed[:10]]
        else:
            changed_items = []
        changed_text = ", ".join(item for item in changed_items if item) or "none"
        attention = "yes" if report.get("needs_attention") else "no"
        return summary, changed_text, attention

    def _safe_short(self, value: Any, *, limit: int = 300) -> str:
        text = str(value)
        text = self.settings.redact_text(text)
        text = " ".join(text.split())
        return text[:limit] or "unknown error"

    def _notification_text(self, notification: Notification) -> str:
        job = self.queue.get(notification.job_id)
        if job is None:
            raise ValueError("notification job does not exist")
        exit_code = job.exit_code if job.exit_code is not None else "unknown"
        report = self._load_report(job)
        if report is None:
            summary = self._safe_short(
                job.error or "Structured report is unavailable", limit=1000
            )
            changed_text = "none"
            attention = "yes"
        else:
            summary, changed_text, attention = self._report_values(report)
        status = "succeeded" if notification.event_type == "succeeded" else "failed"
        provider_label = {"agy": "AGY", "codex": "Codex", "claude": "Claude"}.get(
            job.provider, "AI"
        )
        details = (
            f"Job: {job.id}\n"
            f"Provider: {job.provider}\n"
            f"Runner: {job.runner}\n"
            f"Status: {status}\n"
            f"Sandbox: {job.sandbox_mode}\n\n"
            f"Result:\n{summary}\n\n"
            f"Changed files:\n{changed_text}\n\n"
            f"Needs attention: {attention}\n"
            f"Workspace: {job.workspace}\n"
            f"Exit code: {exit_code}"
        )
        workflow = self.queue.get_workflow(job.workflow_id) if job.workflow_id else None
        if workflow is not None:
            details += (
                f"\n\nWorkflow: {workflow.id}\n"
                f"Stage: {job.workflow_stage or 'unknown'}\n"
                f"Workflow status: {workflow.status}"
            )
            if workflow.github_status:
                details += f"\nGitHub report: {workflow.github_status}"
            if workflow.github_url:
                details += f"\nGitHub URL: {workflow.github_url}"
        if notification.event_type == "succeeded":
            return (
                f"✅ {provider_label} job completed\n\n"
                f"{details}\n\n"
                f"/result {job.id}"
            )
        return (
            f"❌ {provider_label} job failed\n\n"
            f"{details}\n"
            f"Error: {self._safe_short(job.error or 'Codex job failed')}\n\n"
            f"/result {job.id}"
        )

    def drain_notifications(self, *, limit: int = 20) -> int:
        """Deliver pending outbox rows and leave failed sends retryable."""

        delivered = 0
        for notification in self.queue.pending_notifications(limit=limit):
            try:
                text = self._notification_text(notification)
                self._send_reply(notification.chat_id, text)
            except Exception as exc:
                self.queue.mark_notification_failed(
                    notification.id,
                    self._safe_short(f"{type(exc).__name__}: {exc}", limit=500),
                )
                continue
            if self.queue.mark_notification_sent(notification.id):
                delivered += 1
        return delivered

    def run_forever(self, stop_event=None) -> None:
        """Delete any old webhook, then use official getUpdates long polling."""

        self.client.delete_webhook()
        offset: int | None = None
        while stop_event is None or not stop_event.is_set():
            self.drain_notifications()
            updates = self.client.get_updates(
                offset=offset,
                timeout=self.settings.telegram_poll_timeout_seconds,
            )
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                self.handle_update(update)
            self.drain_notifications()


def main() -> int:
    settings = Settings.from_env(require_telegram=True)
    settings.ensure_runtime_dirs()
    queue = JobQueue(settings.queue_db)
    client = TelegramClient(
        token=settings.telegram_bot_token or "",
        api_base=settings.telegram_api_base,
        request_timeout=settings.telegram_request_timeout_seconds,
    )
    adapter = TelegramAdapter(settings, queue, client)
    stop = __import__("threading").Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        try:
            adapter.run_forever(stop)
        except (TelegramAPIError, OSError, TimeoutError):
            if not stop.is_set():
                time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
