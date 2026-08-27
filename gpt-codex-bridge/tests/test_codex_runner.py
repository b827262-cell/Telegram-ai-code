from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bridge.codex_runner import CodexRunner, ReportValidationError, validate_report
from bridge.config import Settings
from bridge.models import Job
from bridge.sandbox import DEFAULT_SANDBOX_MODE


def report(
    summary: str = "ok", *, status: str = "success", sandbox_mode: str | None = None
) -> dict:
    payload = {
        "status": status,
        "summary": summary,
        "changed_files": ["worker.py"],
        "tests": [{"command": "python -m unittest", "result": "pass", "output_summary": "ok"}],
        "git_status": " M worker.py",
        "needs_attention": False,
    }
    if sandbox_mode is not None:
        payload["sandbox_mode"] = sandbox_mode
    return payload


def settings_for(directory: str, *, token: str = "telegram-secret") -> Settings:
    workspace = Path(directory) / "repo"
    workspace.mkdir()
    env = {
        "CODEX_ALLOWED_WORKSPACES": str(workspace),
        "CODEX_DEFAULT_WORKSPACE": str(workspace),
        "CODEX_BRIDGE_DATA_DIR": str(Path(directory) / "state"),
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_ALLOWED_CHAT_ID": "12345",
    }
    return Settings.from_env(env, root_dir=Path(__file__).resolve().parents[1])


def job_for(settings: Settings, *, sandbox_mode: str = DEFAULT_SANDBOX_MODE) -> Job:
    return Job(
        id="job-0123456789abcdef",
        chat_id="12345",
        prompt="repair tests",
        workspace=settings.default_workspace,
        status="running",
        created_at="now",
        sandbox_mode=sandbox_mode,
    )


class FakeProcess:
    def __init__(
        self,
        argv: list[str],
        report_payload: dict | None = None,
        *,
        stdout: bytes = b"",
        timeout: bool = False,
    ) -> None:
        self.argv = argv
        self.report_payload = report_payload
        self.stdout = stdout
        self.timeout = timeout
        self.returncode: int | None = None
        self.pid = os.getpid()
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        if self.report_payload is not None:
            report_path = Path(self.argv[self.argv.index("-o") + 1])
            report_path.write_text(json.dumps(self.report_payload), encoding="utf-8")
        self.returncode = 0 if not self.killed else -15
        return self.stdout, b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class CodexRunnerTests(unittest.TestCase):
    def test_command_uses_default_mode_and_exact_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TELEGRAM_BOT_TOKEN": "telegram-secret"}, clear=False
        ):
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            captured: dict[str, object] = {}

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                captured["argv"] = argv
                captured["kwargs"] = kwargs
                return FakeProcess(argv, report("telegram-secret was not leaked"))

            job = job_for(settings)
            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            argv = captured["argv"]
            kwargs = captured["kwargs"]
            expected_report_path = settings.report_dir / f"{job.id}.json"
            self.assertEqual(
                argv,
                [
                    "codex",
                    "exec",
                    "--sandbox",
                    "workspace-write",
                    "-C",
                    str(settings.default_workspace),
                    "--output-schema",
                    str(settings.schema_path),
                    "-o",
                    str(expected_report_path),
                    job.prompt,
                ],
            )
            for forbidden in (
                "--full-auto",
                "--yolo",
                "--dangerously-bypass-approvals-and-sandbox",
            ):
                self.assertNotIn(forbidden, argv)
            self.assertIs(kwargs["shell"], False)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", kwargs["env"])
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")
            content = outcome.report_path.read_text(encoding="utf-8")
            self.assertNotIn("telegram-secret", content)
            self.assertEqual(json.loads(content)["sandbox_mode"], "workspace-write")

    def test_current_codex_report_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, report(sandbox_mode="workspace-write"))

            outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["status"], "success")
            self.assertEqual(
                json.loads(outcome.report_path.read_text(encoding="utf-8")),
                outcome.report,
            )

    def test_missing_output_file_uses_only_strict_json_stdout_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            payload = report(sandbox_mode="workspace-write")

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, stdout=json.dumps(payload).encode("utf-8"))

            outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report, payload)
            self.assertEqual(
                json.loads(outcome.report_path.read_text(encoding="utf-8")), payload
            )

    def test_exit_zero_with_missing_or_natural_language_report_fails_closed(self) -> None:
        for stdout in (b"", b"Codex completed the task successfully."):
            with self.subTest(stdout=stdout), tempfile.TemporaryDirectory() as directory:
                settings = settings_for(directory)
                settings.ensure_runtime_dirs()

                def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                    return FakeProcess(argv, stdout=stdout)

                outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
                self.assertEqual(outcome.exit_code, 0)
                self.assertFalse(outcome.succeeded)
                self.assertEqual(
                    outcome.report["summary"],
                    "Codex did not produce a valid structured report",
                )

    def test_invalid_report_file_and_invalid_stdout_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                report_path = Path(argv[argv.index("-o") + 1])
                report_path.write_text('{"status":"success"}', encoding="utf-8")
                return FakeProcess(argv, stdout=b"{\"status\":\"success\"}")

            outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertFalse(outcome.succeeded)
            self.assertEqual(
                outcome.report["summary"],
                "Codex did not produce a valid structured report",
            )

    def test_schema_contract_validation_rejects_invalid_shapes(self) -> None:
        valid = report(sandbox_mode="workspace-write")
        self.assertEqual(validate_report(valid), valid)
        for invalid in (
            {**valid, "extra": True},
            {key: value for key, value in valid.items() if key != "tests"},
            {**valid, "tests": [{"command": "pytest", "result": "pass"}]},
            {**valid, "sandbox_mode": "unrestricted"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReportValidationError):
                    validate_report(invalid)

    def test_report_schema_file_matches_strict_validator_contract(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "codex_report.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(report(sandbox_mode="read-only")))
        self.assertEqual(set(schema["properties"]), set(report(sandbox_mode="read-only")))
        self.assertFalse(schema["properties"]["tests"]["items"]["additionalProperties"])

    def test_report_mode_is_normalized_only_when_absent_and_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, report())

            outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")

        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(
                    argv,
                    report(sandbox_mode="danger-full-access"),
                )

            outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")

    def test_supported_modes_are_selected_per_job(self) -> None:
        for mode in ("read-only", "workspace-write", "danger-full-access"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                settings = settings_for(directory)
                settings.ensure_runtime_dirs()
                captured: dict[str, list[str]] = {}

                def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                    captured["argv"] = argv
                    return FakeProcess(argv, report())

                CodexRunner(settings, popen_factory=factory).run(
                    job_for(settings, sandbox_mode=mode)
                )
                self.assertEqual(captured["argv"][3], mode)

    def test_report_mode_cannot_override_job_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                payload = report()
                payload["sandbox_mode"] = "danger-full-access"
                return FakeProcess(argv, payload)

            outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")

    def test_needs_attention_overrides_status_success_or_partial(self) -> None:
        for status in ("success", "partial"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                settings = settings_for(directory)
                settings.ensure_runtime_dirs()

                def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                    payload = report(status=status)
                    payload["needs_attention"] = True
                    return FakeProcess(argv, payload)

                outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
                self.assertEqual(outcome.exit_code, 0)
                self.assertFalse(
                    outcome.succeeded,
                    "needs_attention=True must never be reported as succeeded",
                )

    def test_timeout_terminates_process_and_writes_failed_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            processes: list[FakeProcess] = []

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                process = FakeProcess(argv, report(), timeout=True)
                processes.append(process)
                return process

            killed: list[object] = []

            def kill(process: FakeProcess) -> None:
                killed.append(process)
                process.killed = True

            outcome = CodexRunner(
                settings,
                popen_factory=factory,
                kill_process_group=kill,
            ).run(job_for(settings))
            self.assertTrue(outcome.timed_out)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(killed, processes)
            self.assertEqual(outcome.report["status"], "failed")


if __name__ == "__main__":
    unittest.main()
