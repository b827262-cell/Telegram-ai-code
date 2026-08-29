"""Publish one redacted workflow report to a GitHub repository via gh."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from .config import Settings
from .models import Job, Workflow


class GitHubReportError(RuntimeError):
    """Raised when the configured GitHub report upload cannot complete."""


class GitHubReportPublisher:
    """Use the existing gh login to upload only a generated Markdown report."""

    def __init__(
        self,
        settings: Settings,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.settings = settings
        self._run = run

    def _command(self, args: list[str], workspace: Path) -> str:
        try:
            result = self._run(
                args,
                cwd=str(workspace),
                env=self.settings.codex_environment(),
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.github_report_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubReportError(f"GitHub CLI unavailable: {type(exc).__name__}") from exc
        if result.returncode != 0:
            detail = self.settings.redact_text((result.stderr or result.stdout or "").strip())
            raise GitHubReportError(detail[:500] or "GitHub CLI request failed")
        return self.settings.redact_text(result.stdout.strip())

    def _repository(self, workspace: Path) -> str:
        value = self._command(
            [self.settings.github_cli_bin, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            workspace,
        )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise GitHubReportError("GitHub repository could not be verified")
        return value

    def _remote_path(self, workflow_id: str) -> str:
        directory = self.settings.github_report_directory.strip("/")
        if not directory or ".." in directory or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", directory):
            raise GitHubReportError("GITHUB_REPORT_DIRECTORY is invalid")
        if not re.fullmatch(r"flow-[a-f0-9]{16}", workflow_id):
            raise GitHubReportError("workflow id is invalid")
        return f"{directory}/{workflow_id}.md"

    def _markdown(self, workflow: Workflow, jobs: list[Job]) -> str:
        lines = [
            f"# GPT → AGY → Claude workflow {workflow.id}",
            "",
            "## Task",
            self.settings.redact_text(workflow.prompt)[:12000],
            "",
            "## Stages",
            "",
            "| Stage | Job | Status | Summary |",
            "| --- | --- | --- | --- |",
        ]
        for job in jobs:
            summary = "report unavailable"
            if job.report_path and job.report_path.is_file():
                try:
                    import json

                    report: Any = json.loads(job.report_path.read_text(encoding="utf-8"))
                    if isinstance(report, dict):
                        summary = str(report.get("summary", summary))
                except (OSError, ValueError):
                    pass
            summary = " ".join(self.settings.redact_text(summary).split()).replace("|", "\\|")[:500]
            lines.append(
                f"| {job.workflow_stage or job.provider} | `{job.id}` | {job.status} | {summary} |"
            )
        lines.extend(
            [
                "",
                "## Execution contract",
                "",
                "- Sequence: GPT/Codex implementation → AGY review → Claude finalization",
                "- GitHub report upload: bridge-managed, report-only, no automatic code push",
                "- Secrets: redacted before upload",
                "",
            ]
        )
        return "\n".join(lines)

    def publish(self, workflow: Workflow, jobs: list[Job]) -> str:
        if not workflow.external_publication_enabled:
            raise GitHubReportError("GitHub report upload is disabled for this workflow")
        if not self.settings.github_report_enabled:
            raise GitHubReportError("GitHub report upload is disabled")
        repository = self._repository(workflow.workspace)
        remote_path = self._remote_path(workflow.id)
        content = base64.b64encode(self._markdown(workflow, jobs).encode("utf-8")).decode("ascii")
        return self._command(
            [
                self.settings.github_cli_bin,
                "api",
                "--method",
                "PUT",
                f"repos/{repository}/contents/{remote_path}",
                "-f",
                f"message=docs: upload automated workflow report {workflow.id}",
                "-f",
                f"content={content}",
                "-f",
                f"branch={self.settings.github_report_branch}",
                "--jq",
                ".content.html_url",
            ],
            workflow.workspace,
        )
