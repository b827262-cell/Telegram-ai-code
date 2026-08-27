"""Safe subprocess wrapper for non-interactive Codex jobs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Callable

from .config import Settings
from .models import Job
from .sandbox import validate_sandbox_mode


class ReportValidationError(ValueError):
    """Raised when Codex output does not match the runner report contract."""


MAX_STDOUT_REPORT_BYTES = 1_000_000


@dataclass(frozen=True)
class RunOutcome:
    report_path: Path
    report: dict[str, Any]
    exit_code: int
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        if self.exit_code != 0 or self.report.get("status") not in {"success", "partial"}:
            return False
        # An agent can exit 0 and self-report "partial" while flagging that a
        # human must look at it; that must not be reported as succeeded.
        return not bool(self.report.get("needs_attention"))


def validate_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ReportValidationError("report must be an object")
    required = {
        "status",
        "summary",
        "changed_files",
        "tests",
        "git_status",
        "needs_attention",
        "sandbox_mode",
    }
    if set(report) != required:
        raise ReportValidationError("report fields do not match the required schema")
    if report["status"] not in {"success", "partial", "failed"}:
        raise ReportValidationError("report status is invalid")
    if not isinstance(report["summary"], str):
        raise ReportValidationError("report summary must be a string")
    if not isinstance(report["changed_files"], list) or not all(
        isinstance(item, str) for item in report["changed_files"]
    ):
        raise ReportValidationError("changed_files must be an array of strings")
    if not isinstance(report["tests"], list):
        raise ReportValidationError("tests must be an array")
    for test in report["tests"]:
        if not isinstance(test, dict) or set(test) != {"command", "result", "output_summary"}:
            raise ReportValidationError("test entries do not match the required schema")
        if not isinstance(test["command"], str) or not isinstance(test["output_summary"], str):
            raise ReportValidationError("test command and output_summary must be strings")
        if test["result"] not in {"pass", "fail"}:
            raise ReportValidationError("test result is invalid")
    if not isinstance(report["git_status"], str):
        raise ReportValidationError("git_status must be a string")
    if not isinstance(report["needs_attention"], bool):
        raise ReportValidationError("needs_attention must be a boolean")
    try:
        validate_sandbox_mode(report["sandbox_mode"])
    except (TypeError, ValueError) as exc:
        raise ReportValidationError("sandbox_mode is invalid") from exc
    return report


def _redact(
    value: Any,
    secrets: tuple[str, ...],
    redactor: Callable[[str], str] | None = None,
) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redactor(redacted) if redactor else redacted
    if isinstance(value, list):
        return [_redact(item, secrets, redactor) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secrets, redactor) for key, item in value.items()}
    return value


class CodexRunner:
    """Runs only the fixed, sandboxed Codex command for a queued job."""

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

    def command_for(self, job: Job, report_path: Path) -> list[str]:
        if job.provider != "codex":
            raise ValueError("CodexRunner can only run Codex jobs")
        sandbox_mode = validate_sandbox_mode(job.sandbox_mode)
        workspace = self.settings.validate_workspace(job.workspace)
        # Keep this as argv, never shell text. Do not add approval or
        # sandbox-bypass flags.
        return [
            self.settings.codex_bin,
            "exec",
            "--sandbox",
            sandbox_mode,
            "-C",
            str(workspace),
            "--output-schema",
            str(self.settings.schema_path),
            "-o",
            str(report_path),
            job.prompt,
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

    def _report_path(self, job: Job) -> Path:
        return self.settings.report_dir / f"{job.id}.json"

    def _parse_report_payload(self, payload: Any, job: Job) -> dict[str, Any]:
        return self._report_for_job(
            _redact(payload, self.settings.secret_values, self.settings.redact_text),
            job,
        )

    def _read_report(self, path: Path, job: Job) -> dict[str, Any]:
        return self._parse_report_payload(
            json.loads(path.read_text(encoding="utf-8")),
            job,
        )

    def _parse_stdout_report(self, stdout: bytes, job: Job) -> dict[str, Any]:
        """Parse only a complete JSON stdout message as a compatibility path.

        ``codex exec`` documents stdout as the final agent message.  Some CLI
        versions/configurations have emitted that message without creating
        the ``--output-last-message`` artifact.  Do not extract JSON from
        prose, JSONL, or markdown: those are not structured-report success.
        """

        if len(stdout) > MAX_STDOUT_REPORT_BYTES:
            raise ReportValidationError("Codex stdout report is too large")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReportValidationError("Codex stdout is not one JSON report") from exc
        return self._parse_report_payload(payload, job)

    def _write_report(self, path: Path, report: dict[str, Any]) -> None:
        safe_report = _redact(
            report,
            self.settings.secret_values,
            self.settings.redact_text,
        )
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

    def internal_report(self, job: Job, *, summary: str, needs_attention: bool = True) -> Path:
        path = self._report_path(job)
        report = {
            "status": "failed",
            "summary": summary,
            "changed_files": [],
            "tests": [],
            "git_status": "",
            "needs_attention": needs_attention,
            "sandbox_mode": validate_sandbox_mode(job.sandbox_mode),
        }
        self._write_report(path, report)
        return path

    @staticmethod
    def _report_for_job(report: Any, job: Job) -> dict[str, Any]:
        sandbox_mode = validate_sandbox_mode(job.sandbox_mode)
        if not isinstance(report, dict):
            raise ReportValidationError("report must be an object")
        if "sandbox_mode" in report and report["sandbox_mode"] != sandbox_mode:
            raise ReportValidationError("report sandbox_mode does not match job")
        normalized = dict(report)
        normalized["sandbox_mode"] = sandbox_mode
        return validate_report(normalized)

    def run(self, job: Job) -> RunOutcome:
        report_path = self._report_path(job)
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = self.settings.validate_workspace(job.workspace)
        command = self.command_for(job, report_path)
        child_env = self.settings.codex_environment()
        process = self._popen(
            command,
            cwd=str(workspace),
            env=child_env,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        timed_out = False
        stdout = b""
        try:
            stdout, _stderr = process.communicate(timeout=self.settings.codex_timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_process_group(process)
            try:
                stdout, _stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                stdout, _stderr = process.communicate()

        exit_code = int(process.returncode if process.returncode is not None else -1)
        if timed_out:
            report = {
                "status": "failed",
                "summary": "Codex job timed out",
                "changed_files": [],
                "tests": [],
                "git_status": "",
                "needs_attention": True,
                "sandbox_mode": validate_sandbox_mode(job.sandbox_mode),
            }
            self._write_report(report_path, report)
            return RunOutcome(report_path, report, exit_code, timed_out=True)

        try:
            report = self._read_report(report_path, job)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReportValidationError):
            try:
                report = self._parse_stdout_report(stdout, job)
            except ReportValidationError:
                report = {
                    "status": "failed",
                    "summary": "Codex did not produce a valid structured report",
                    "changed_files": [],
                    "tests": [],
                    "git_status": "",
                    "needs_attention": True,
                    "sandbox_mode": validate_sandbox_mode(job.sandbox_mode),
                }
        if exit_code != 0:
            report = {
                "status": "failed",
                "summary": "Codex exited before completing the job",
                "changed_files": report.get("changed_files", []),
                "tests": report.get("tests", []),
                "git_status": report.get("git_status", ""),
                "needs_attention": True,
                "sandbox_mode": validate_sandbox_mode(job.sandbox_mode),
            }
        self._write_report(report_path, report)
        return RunOutcome(report_path, report, exit_code, timed_out=False)
