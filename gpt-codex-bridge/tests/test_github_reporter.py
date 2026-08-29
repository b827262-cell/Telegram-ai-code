from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest

from bridge.config import Settings
from bridge.github_reporter import GitHubReportPublisher
from bridge.models import Job, Workflow


class GitHubReportPublisherTests(unittest.TestCase):
    def test_uploads_only_redacted_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            settings = Settings.from_env(
                {
                    "CODEX_ALLOWED_WORKSPACES": str(workspace),
                    "CODEX_DEFAULT_WORKSPACE": str(workspace),
                    "GITHUB_REPORT_ENABLED": "true",
                    "TELEGRAM_BOT_TOKEN": "bot-secret",
                },
                root_dir=Path(__file__).resolve().parents[1],
            )
            calls: list[list[str]] = []

            def fake_run(args: list[str], **_: object) -> CompletedProcess[str]:
                calls.append(args)
                if args[1:4] == ["repo", "view", "--json"]:
                    return CompletedProcess(args, 0, stdout="owner/repo\n", stderr="")
                return CompletedProcess(args, 0, stdout="https://github/report\n", stderr="")

            workflow = Workflow(
                "flow-0123456789abcdef",
                "42",
                "task contains bot-secret",
                workspace,
                "running",
                "github",
                "now",
            )
            report_path = workspace / "job.json"
            report_path.write_text('{"summary":"safe bot-secret","status":"success"}', encoding="utf-8")
            job = Job(
                "job-0123456789abcdef",
                "42",
                "task",
                workspace,
                "succeeded",
                "now",
                report_path=report_path,
                workflow_id=workflow.id,
                workflow_stage="gpt",
                workflow_order=1,
            )

            url = GitHubReportPublisher(settings, run=fake_run).publish(workflow, [job])

            self.assertEqual(url, "https://github/report")
            self.assertTrue(any(
                "contents/reports/auto-loop/flow-0123456789abcdef.md" in value
                for value in calls[-1]
            ))
            content_arg = next(value for value in calls[-1] if value.startswith("content="))
            self.assertNotIn("bot-secret", content_arg)

    def test_no_external_write_policy_blocks_writer_when_global_reporting_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            settings = Settings.from_env(
                {
                    "CODEX_ALLOWED_WORKSPACES": str(workspace),
                    "CODEX_DEFAULT_WORKSPACE": str(workspace),
                    "GITHUB_REPORT_ENABLED": "true",
                },
                root_dir=Path(__file__).resolve().parents[1],
            )
            calls: list[list[str]] = []

            def fake_run(args: list[str], **_: object) -> CompletedProcess[str]:
                calls.append(args)
                return CompletedProcess(args, 0, stdout="unexpected", stderr="")

            workflow = Workflow(
                "flow-0123456789abcdef", "42", "smoke", workspace, "running", "github", "now",
                external_publication_enabled=False,
            )
            with self.assertRaisesRegex(Exception, "disabled for this workflow"):
                GitHubReportPublisher(settings, run=fake_run).publish(workflow, [])
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
