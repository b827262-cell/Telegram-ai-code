"""Safe subprocess wrapper for non-interactive Claude Code jobs."""

from __future__ import annotations

import json
from pathlib import Path
import os
import signal
import subprocess
from typing import Any, Callable

from .codex_runner import RunOutcome, validate_report
from .config import Settings
from .models import Job
from .sandbox import validate_sandbox_mode


CLAUDE_MODEL = "claude-opus-5"
CLAUDE_EFFORT = "medium"


class ClaudeRunner:
    """Run the local Claude Code CLI without interactive permission prompts."""

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
        if job.provider != "claude":
            raise ValueError("ClaudeRunner can only run Claude jobs")
        self.settings.validate_workspace(job.workspace)
        model = job.model or CLAUDE_MODEL
        effort = job.effort or CLAUDE_EFFORT
        return [
            self.settings.claude_bin,
            "-p",
            job.prompt,
            "--model",
            model,
            "--effort",
            effort,
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

    @staticmethod
    def _text(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

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
        report = self._report(
            job,
            status="failed",
            summary=summary,
            needs_attention=needs_attention,
        )
        self._write_report(path, report)
        return path

    def run(self, job: Job) -> RunOutcome:
        report_path = self._report_path(job)
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = self.settings.validate_workspace(job.workspace)
        command = self.command_for(job)
        process = self._popen(
            command,
            cwd=str(workspace),
            env=self.settings.claude_environment(),
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
            stdout, stderr = process.communicate(timeout=self.settings.claude_timeout_seconds)
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
        if timed_out:
            report = self._report(
                job,
                status="failed",
                summary="Claude job timed out",
                needs_attention=True,
            )
        elif exit_code == 0:
            output = self.settings.redact_text(self._text(stdout).strip())
            report = self._report(
                job,
                status="success",
                summary=output or "Claude completed without output",
                needs_attention=False,
            )
        else:
            detail = self.settings.redact_text(self._text(stderr).strip())
            if not detail:
                detail = self.settings.redact_text(self._text(stdout).strip())
            summary = f"Claude CLI failed (exit code {exit_code})"
            if detail:
                summary += f": {detail[:1000]}"
            report = self._report(
                job,
                status="failed",
                summary=summary,
                needs_attention=True,
            )
        self._write_report(report_path, report)
        return RunOutcome(report_path, report, exit_code, timed_out=timed_out)
