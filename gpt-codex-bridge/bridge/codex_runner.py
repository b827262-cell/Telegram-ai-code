"""Safe subprocess wrapper for non-interactive Codex jobs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Callable

from .config import Settings
from .models import Job
from .sandbox import validate_sandbox_mode


logger = logging.getLogger(__name__)


class ReportValidationError(ValueError):
    """Raised when Codex output does not match the runner report contract."""


MAX_STDOUT_REPORT_BYTES = 1_000_000
MAX_RAW_DIAGNOSTIC_BYTES = 16_384  # <= 16 KiB safe bound for raw failure sidecar

JOB_SANDBOX_MODE_TOKEN = "__JOB_SANDBOX_MODE__"

REPORT_CONTRACT = """
REPORTING CONTRACT:
Your final message MUST be exactly one JSON object with NO additional text, markdown formatting/fences, or conversational commentary before or after it.
The JSON object must contain exactly these fields:
{
  "status": "success",
  "summary": "Concise summary of task results",
  "changed_files": ["path/to/modified/file"],
  "tests": [
    {
      "command": "test command executed",
      "result": "pass",
      "output_summary": "summary of test result"
    }
  ],
  "git_status": "relevant git status or empty string",
  "needs_attention": false,
  "sandbox_mode": "__JOB_SANDBOX_MODE__"
}

Schema requirements:
- "status": string enum, must be one of: "success", "partial", "failed".
- "summary": string describing observed task results.
- "changed_files": array of strings listing paths of files modified for this task. Report observed task results, not ambient unrelated dirty files unless relevant.
- "tests": array of test objects, each with "command" (string), "result" (string enum: "pass" | "fail"), and "output_summary" (string).
- "git_status": string describing git status for this task, or empty string.
- "needs_attention": boolean. Set true only when a human must intervene before this task can be considered done. IMPORTANT: needs_attention=true makes this stage non-successful and stops the automated workflow from advancing, even when "status" is "success" or "partial". Do not set it merely because unrelated dirty files, pre-existing unrelated failures, or other ambient environment conditions exist; describe those in "summary" or "git_status" instead.
- "sandbox_mode": string, exactly the mode this job was launched with, as shown in the example above. The supported set is "read-only", "workspace-write", "danger-full-access"; never report a mode different from the one you were launched with.
""".strip()


def report_contract(sandbox_mode: str) -> str:
    """Render the fixed report contract for a job's real sandbox mode.

    The example echoes the launch mode on purpose: telling a read-only job to
    write "workspace-write" into its own report would manufacture the very
    discrepancy the runner then has to warn about.
    """

    return REPORT_CONTRACT.replace(
        JOB_SANDBOX_MODE_TOKEN, validate_sandbox_mode(sandbox_mode)
    )


FAILURE_NONE = "none"
FAILURE_TIMEOUT = "timeout"
FAILURE_EXIT_NONZERO = "exit_nonzero"
FAILURE_INVALID_REPORT = "invalid_report"
FAILURE_NEEDS_ATTENTION = "needs_attention"
FAILURE_AGENT_REPORTED = "agent_reported_failure"

FAILURE_CLASSES = frozenset(
    {
        FAILURE_NONE,
        FAILURE_TIMEOUT,
        FAILURE_EXIT_NONZERO,
        FAILURE_INVALID_REPORT,
        FAILURE_NEEDS_ATTENTION,
        FAILURE_AGENT_REPORTED,
    }
)

MAX_FAILURE_DETAIL_CHARS = 300


@dataclass(frozen=True)
class RunOutcome:
    report_path: Path
    report: dict[str, Any]
    exit_code: int
    timed_out: bool = False
    explicit_failure: str | None = None

    def __post_init__(self) -> None:
        # Keep the failure vocabulary closed: an unrecognised code would silently
        # fall through describe_failure and be reported as a different cause.
        if self.explicit_failure == FAILURE_NONE:
            raise ValueError("explicit_failure must name a failing class, not none")
        if self.explicit_failure is not None and self.explicit_failure not in FAILURE_CLASSES:
            raise ValueError(f"unknown failure class: {self.explicit_failure!r}")

    @property
    def succeeded(self) -> bool:
        if self.exit_code != 0 or self.report.get("status") not in {"success", "partial"}:
            return False
        # An agent can exit 0 and self-report "partial" while flagging that a
        # human must look at it; that must not be reported as succeeded.
        return not bool(self.report.get("needs_attention"))

    @property
    def failure_reason(self) -> str:
        """Name why this run is not successful, from a closed enum.

        Precedence matters: a crash outranks a self-report, so a nonzero exit
        can never be relabelled as a benign needs_attention flag.
        """

        if self.succeeded:
            return FAILURE_NONE
        if self.timed_out:
            return FAILURE_TIMEOUT
        if self.exit_code != 0:
            return FAILURE_EXIT_NONZERO
        if self.explicit_failure:
            return self.explicit_failure
        if self.report.get("status") in {"success", "partial"} and self.report.get(
            "needs_attention"
        ):
            return FAILURE_NEEDS_ATTENTION
        return FAILURE_AGENT_REPORTED


def _failure_detail(report: dict[str, Any]) -> str:
    """Return a bounded single-line excerpt of an already-redacted summary."""

    summary = report.get("summary")
    if not isinstance(summary, str):
        return ""
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:MAX_FAILURE_DETAIL_CHARS]
    return ""


def describe_failure(provider: str, outcome: RunOutcome) -> str:
    """Build the operator-facing job error for a non-successful run."""

    label = provider.title()
    reason = outcome.failure_reason
    if reason == FAILURE_TIMEOUT:
        return f"{label} job timed out"
    if reason == FAILURE_EXIT_NONZERO:
        return f"{label} CLI failed (exit code {outcome.exit_code})"
    if reason == FAILURE_INVALID_REPORT:
        return f"{label} did not produce a valid structured report"
    if reason == FAILURE_NEEDS_ATTENTION:
        message = f"{label} completed but flagged needs_attention"
        detail = _failure_detail(outcome.report)
        return f"{message}: {detail}" if detail else message
    message = f"{label} reported a failed status"
    detail = _failure_detail(outcome.report)
    return f"{message}: {detail}" if detail else message


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
        prompt = f"{job.prompt.rstrip()}\n\n{report_contract(sandbox_mode)}"
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
            prompt,
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

    def _collect_raw_evidence(self, report_path: Path, stdout: bytes | str) -> str:
        file_text = ""
        if report_path.is_file():
            try:
                file_text = report_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                file_text = ""
        stdout_text = (
            stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout)
        ).strip()
        if file_text and stdout_text and file_text != stdout_text:
            return f"--- report artifact ---\n{file_text}\n\n--- stdout ---\n{stdout_text}"
        return file_text or stdout_text

    def _raw_diagnostic_path(self, job: Job) -> Path:
        return self.settings.report_dir / f"{job.id}.raw.txt"

    def _clear_raw_diagnostic(self, job: Job) -> None:
        # A sidecar must only ever describe the run that produced it; a stale
        # one from an earlier attempt would make a valid report look failed.
        try:
            self._raw_diagnostic_path(job).unlink()
        except OSError:
            pass

    def _write_raw_diagnostic(self, job: Job, raw_text: str) -> Path:
        raw_path = self._raw_diagnostic_path(job)
        redacted = _redact(raw_text, self.settings.secret_values, self.settings.redact_text)
        if not isinstance(redacted, str):
            redacted = str(redacted)
        encoded = redacted.encode("utf-8")
        if len(encoded) > MAX_RAW_DIAGNOSTIC_BYTES:
            redacted = encoded[:MAX_RAW_DIAGNOSTIC_BYTES].decode("utf-8", errors="ignore")
        temporary = raw_path.with_suffix(raw_path.suffix + ".tmp")
        temporary.write_text(redacted, encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, raw_path)
        return raw_path

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
        if "sandbox_mode" in report:
            try:
                reported_mode = validate_sandbox_mode(report["sandbox_mode"])
            except (TypeError, ValueError) as exc:
                raise ReportValidationError("sandbox_mode is invalid") from exc
            if reported_mode != sandbox_mode:
                logger.warning(
                    "Codex job %s reported sandbox_mode '%s' differing from job sandbox_mode '%s'",
                    job.id,
                    reported_mode,
                    sandbox_mode,
                )
        normalized = dict(report)
        normalized["sandbox_mode"] = sandbox_mode
        return validate_report(normalized)

    def run(self, job: Job) -> RunOutcome:
        report_path = self._report_path(job)
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw_path = self.settings.report_dir / f"{job.id}.raw.txt"
        try:
            raw_path.unlink(missing_ok=True)
        except OSError:
            pass
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
            self._clear_raw_diagnostic(job)
            self._write_report(report_path, report)
            return RunOutcome(report_path, report, exit_code, timed_out=True)

        report_failure_exc: Exception | None = None
        invalid_report_failure = False
        try:
            report = self._read_report(report_path, job)
            self._clear_raw_diagnostic(job)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReportValidationError) as exc:
            report_failure_exc = exc
            try:
                report = self._parse_stdout_report(stdout, job)
                report_failure_exc = None
                self._clear_raw_diagnostic(job)
            except ReportValidationError as stdout_exc:
                report_failure_exc = stdout_exc
                invalid_report_failure = True
                raw_evidence = self._collect_raw_evidence(report_path, stdout)
                self._write_raw_diagnostic(job, raw_evidence)
                safe_reason = _redact(
                    str(report_failure_exc),
                    self.settings.secret_values,
                    self.settings.redact_text,
                )
                logger.warning(
                    "Codex job %s failed structured report validation: %s (%s)",
                    job.id,
                    report_failure_exc.__class__.__name__,
                    safe_reason,
                )
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
        return RunOutcome(
            report_path,
            report,
            exit_code,
            timed_out=False,
            explicit_failure=FAILURE_INVALID_REPORT if invalid_report_failure else None,
        )
