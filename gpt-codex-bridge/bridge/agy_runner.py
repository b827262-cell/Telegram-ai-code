"""Safe subprocess wrapper for the local agy OAuth CLI."""

from __future__ import annotations

import json
from pathlib import Path
import os
import re
import signal
import subprocess
from typing import Any, Callable

from .codex_runner import FAILURE_INVALID_REPORT, RunOutcome, validate_report
from .config import Settings
from .models import Job
from .sandbox import validate_sandbox_mode


AGY_MODEL = "gemini-3.7-flash-high"
_AUTH_REQUIRED_RE = re.compile(
    r"(?i)(?:\bagy\s+login\b|re[- ]?login|reauth(?:enticate)?|"
    r"log[- ]?in\s+required|sign[- ]?in\s+required|authentication\s+required|"
    r"please\s+(?:run\s+)?(?:agy\s+)?login|please\s+(?:re[- ]?)?(?:authenticate|"
    r"log[- ]?in|sign[- ]?in))"
)
_TEXT_KEYS = ("response", "result", "output", "text", "content", "answer", "message")


class AgyOutputError(ValueError):
    """Raised when agy does not return a usable JSON response."""


def _response_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            if key in value:
                found = _response_text(value[key])
                if found:
                    return found
    if isinstance(value, list):
        parts = [_response_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return ""


def parse_agy_json(output: bytes | str) -> str:
    """Parse agy's JSON output and return only the assistant response text."""

    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgyOutputError("agy returned malformed JSON") from exc

    if isinstance(payload, dict) and payload.get("is_error") is True:
        raise AgyOutputError("agy returned an error response")
    response = _response_text(payload)
    if not response:
        raise AgyOutputError("agy JSON did not contain a response")
    return response


class AgyRunner:
    """Run agy in print mode using its existing Google OAuth credential."""

    def __init__(
        self,
        settings: Settings,
        *,
        popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        kill_process_group: Callable[[Any], None] | None = None,
    ):
        self.settings = settings
        self._popen = popen_factory
        self._kill_process_group = kill_process_group or self._kill_group

    def command_for(self, job: Job) -> list[str]:
        if job.provider != "agy":
            raise ValueError("AgyRunner can only run agy jobs")
        self.settings.validate_workspace(job.workspace)
        return [
            self.settings.agy_bin,
            "-p",
            job.prompt,
            "--model",
            AGY_MODEL,
            "--output-format",
            "json",
        ]

    @staticmethod
    def _kill_group(process: Any) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, AttributeError):
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _text(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _report_path(self, job: Job) -> Path:
        return self.settings.report_dir / f"{job.id}.json"

    def _write_report(self, path: Path, report: dict[str, Any]) -> None:
        safe_report = {
            key: self.settings.redact_text(value) if isinstance(value, str) else value
            for key, value in report.items()
        }
        validate_report(safe_report)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)

    def _report(
        self,
        job: Job,
        *,
        status: str,
        summary: str,
        needs_attention: bool,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "summary": self.settings.redact_text(summary)[:12000],
            "changed_files": [],
            "tests": [],
            "git_status": "",
            "needs_attention": needs_attention,
            "sandbox_mode": validate_sandbox_mode(job.sandbox_mode),
        }

    def internal_report(self, job: Job, *, summary: str, needs_attention: bool = True) -> Path:
        path = self._report_path(job)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write_report(
            path,
            self._report(
                job,
                status="failed",
                summary=summary,
                needs_attention=needs_attention,
            ),
        )
        return path

    def run(self, job: Job) -> RunOutcome:
        report_path = self._report_path(job)
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = self.settings.validate_workspace(job.workspace)
        command = self.command_for(job)
        process = self._popen(
            command,
            cwd=str(workspace),
            env=self.settings.agy_environment(),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        timed_out = False
        stdout = b""
        stderr = b""
        try:
            stdout, stderr = process.communicate(timeout=self.settings.agy_timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_process_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                stdout, stderr = process.communicate()

        exit_code = int(process.returncode if process.returncode is not None else -1)
        invalid_report_failure = False
        if timed_out:
            report = self._report(
                job,
                status="failed",
                summary="AGY job timed out",
                needs_attention=True,
            )
        elif _AUTH_REQUIRED_RE.search(self._text(stdout) + "\n" + self._text(stderr)):
            report = self._report(
                job,
                status="failed",
                summary="BLOCKED_AUTH",
                needs_attention=True,
            )
        elif exit_code != 0:
            report = self._report(
                job,
                status="failed",
                summary=f"AGY CLI failed (exit code {exit_code})",
                needs_attention=True,
            )
        else:
            try:
                summary = parse_agy_json(stdout)
            except AgyOutputError:
                invalid_report_failure = True
                report = self._report(
                    job,
                    status="failed",
                    summary="AGY CLI did not produce valid JSON",
                    needs_attention=True,
                )
            else:
                report = self._report(
                    job,
                    status="success",
                    summary=summary,
                    needs_attention=False,
                )
        self._write_report(report_path, report)
        return RunOutcome(
            report_path,
            report,
            exit_code,
            timed_out=timed_out,
            explicit_failure=FAILURE_INVALID_REPORT if invalid_report_failure else None,
        )
