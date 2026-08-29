from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bridge.codex_runner import (
    CodexRunner,
    FAILURE_AGENT_REPORTED,
    FAILURE_CLASSES,
    FAILURE_INVALID_REPORT,
    FAILURE_NEEDS_ATTENTION,
    FAILURE_NONE,
    FAILURE_TIMEOUT,
    JOB_SANDBOX_MODE_TOKEN,
    MAX_RAW_DIAGNOSTIC_BYTES,
    REPORT_CONTRACT,
    RunOutcome,
    ReportValidationError,
    describe_failure,
    report_contract,
    validate_report,
)
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
            expected_prompt = f"{job.prompt}\n\n{report_contract('workspace-write')}"
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
                    expected_prompt,
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

    def test_command_appends_report_contract_to_bare_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            runner = CodexRunner(settings)
            job = Job(
                id="job-bare1234567890",
                chat_id="12345",
                prompt="just list directory contents",
                workspace=settings.default_workspace,
                status="running",
                created_at="now",
                sandbox_mode="read-only",
            )
            report_path = settings.report_dir / f"{job.id}.json"
            argv = runner.command_for(job, report_path)
            self.assertEqual(
                argv[-1],
                f"just list directory contents\n\n{report_contract('read-only')}",
            )
            rendered = argv[-1]
            self.assertIn('"sandbox_mode": "read-only"', rendered)
            self.assertNotIn(JOB_SANDBOX_MODE_TOKEN, rendered)

    def test_report_contract_content_and_requirements(self) -> None:
        self.assertIn("status", REPORT_CONTRACT)
        self.assertIn("summary", REPORT_CONTRACT)
        self.assertIn("changed_files", REPORT_CONTRACT)
        self.assertIn("tests", REPORT_CONTRACT)
        self.assertIn("git_status", REPORT_CONTRACT)
        self.assertIn("needs_attention", REPORT_CONTRACT)
        self.assertIn("sandbox_mode", REPORT_CONTRACT)
        self.assertIn('"success"', REPORT_CONTRACT)
        self.assertIn('"partial"', REPORT_CONTRACT)
        self.assertIn('"failed"', REPORT_CONTRACT)
        self.assertIn('"command"', REPORT_CONTRACT)
        self.assertIn('"result"', REPORT_CONTRACT)
        self.assertIn('"output_summary"', REPORT_CONTRACT)
        self.assertIn('"pass"', REPORT_CONTRACT)
        self.assertIn('"fail"', REPORT_CONTRACT)
        self.assertIn('"read-only"', REPORT_CONTRACT)
        self.assertIn('"workspace-write"', REPORT_CONTRACT)
        self.assertIn('"danger-full-access"', REPORT_CONTRACT)
        self.assertIn("exactly one JSON object", REPORT_CONTRACT)
        self.assertIn("unrelated dirty files", REPORT_CONTRACT)
        self.assertIn(JOB_SANDBOX_MODE_TOKEN, REPORT_CONTRACT)
        self.assertIn(
            "needs_attention=true makes this stage non-successful", REPORT_CONTRACT
        )
        self.assertIn("stops the automated workflow", REPORT_CONTRACT)
        self.assertIn('"success" or "partial"', REPORT_CONTRACT)

    def test_report_contract_renders_and_validates_job_mode(self) -> None:
        rendered = report_contract("read-only")
        self.assertNotIn(JOB_SANDBOX_MODE_TOKEN, rendered)
        self.assertIn('"sandbox_mode": "read-only"', rendered)
        # The closed enum set must survive rendering.
        for mode in ("read-only", "workspace-write", "danger-full-access"):
            self.assertIn(f'"{mode}"', rendered)
        with self.assertRaises(ValueError):
            report_contract("danger-full-access-but-sneaky")

    def test_failure_reason_is_none_when_successful(self) -> None:
        outcome = RunOutcome(
            Path("/tmp/job.json"), report(sandbox_mode="workspace-write"), 0
        )
        self.assertEqual(outcome.failure_reason, FAILURE_NONE)
        self.assertTrue(outcome.succeeded)

    def test_failure_reason_classifies_needs_attention_on_clean_exit(self) -> None:
        outcome = RunOutcome(
            Path("/tmp/job.json"),
            report(
                "blocked by sandbox",
                status="partial",
                sandbox_mode="workspace-write",
            )
            | {"needs_attention": True},
            0,
        )
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_reason, FAILURE_NEEDS_ATTENTION)

    def test_failure_reason_never_masks_nonzero_exit(self) -> None:
        # A crash must not be relabelled as a benign needs_attention flag.
        outcome = RunOutcome(
            Path("/tmp/job.json"),
            report(
                "please look",
                status="success",
                sandbox_mode="workspace-write",
            )
            | {"needs_attention": True},
            3,
        )
        self.assertEqual(outcome.failure_reason, "exit_nonzero")

    def test_failure_reason_precedence_timeout_then_explicit(self) -> None:
        timed_out = RunOutcome(
            Path("/tmp/job.json"),
            report("t", status="failed", sandbox_mode="workspace-write"),
            -1,
            timed_out=True,
            explicit_failure=FAILURE_INVALID_REPORT,
        )
        self.assertEqual(timed_out.failure_reason, FAILURE_TIMEOUT)
        invalid = RunOutcome(
            Path("/tmp/job.json"),
            report("i", status="failed", sandbox_mode="workspace-write"),
            0,
            explicit_failure=FAILURE_INVALID_REPORT,
        )
        self.assertEqual(invalid.failure_reason, FAILURE_INVALID_REPORT)

    def test_failure_reasons_are_all_declared_classes(self) -> None:
        for outcome in (
            RunOutcome(Path("/p"), report(sandbox_mode="workspace-write"), 0),
            RunOutcome(Path("/p"), report("x", status="failed", sandbox_mode="workspace-write"), 0),
        ):
            self.assertIn(outcome.failure_reason, FAILURE_CLASSES)

    def test_describe_failure_names_a_distinct_cause_per_class(self) -> None:
        messages = {}
        cases = {
            FAILURE_TIMEOUT: RunOutcome(
                Path("/p"),
                report("t", status="failed", sandbox_mode="workspace-write"),
                -1,
                timed_out=True,
            ),
            "exit_nonzero": RunOutcome(
                Path("/p"),
                report("e", status="failed", sandbox_mode="workspace-write"),
                7,
            ),
            FAILURE_INVALID_REPORT: RunOutcome(
                Path("/p"),
                report("i", status="failed", sandbox_mode="workspace-write"),
                0,
                explicit_failure=FAILURE_INVALID_REPORT,
            ),
            FAILURE_NEEDS_ATTENTION: RunOutcome(
                Path("/p"),
                report(
                    "workspace is outside the writable sandbox",
                    status="partial",
                    sandbox_mode="workspace-write",
                )
                | {"needs_attention": True},
                0,
            ),
            FAILURE_AGENT_REPORTED: RunOutcome(
                Path("/p"),
                report("BLOCKED_AUTH", status="failed", sandbox_mode="workspace-write"),
                0,
            ),
        }
        for expected_reason, outcome in cases.items():
            self.assertEqual(outcome.failure_reason, expected_reason)
            messages[expected_reason] = describe_failure("codex", outcome)
        self.assertEqual(len(set(messages.values())), len(messages), messages)
        for message in messages.values():
            self.assertNotIn("job failed", message)
        self.assertIn(
            "completed but flagged needs_attention: workspace is outside the writable sandbox",
            messages[FAILURE_NEEDS_ATTENTION],
        )
        self.assertIn("exit code 7", messages["exit_nonzero"])
        self.assertIn("BLOCKED_AUTH", messages[FAILURE_AGENT_REPORTED])

    def test_describe_failure_detail_is_bounded(self) -> None:
        from bridge.codex_runner import MAX_FAILURE_DETAIL_CHARS

        long_summary = "z" * (MAX_FAILURE_DETAIL_CHARS * 5)
        outcome = RunOutcome(
            Path("/p"),
            report(long_summary, status="success", sandbox_mode="workspace-write")
            | {"needs_attention": True},
            0,
        )
        message = describe_failure("codex", outcome)
        detail = message.split(": ", 1)[1]
        self.assertEqual(len(detail), MAX_FAILURE_DETAIL_CHARS)

    def test_explicit_failure_must_be_a_declared_class(self) -> None:
        base = report("x", status="failed", sandbox_mode="workspace-write")
        for legal in (FAILURE_INVALID_REPORT, FAILURE_TIMEOUT, FAILURE_NEEDS_ATTENTION):
            self.assertEqual(
                RunOutcome(Path("/p"), base, 0, explicit_failure=legal).failure_reason,
                legal,
            )
        with self.assertRaises(ValueError):
            RunOutcome(Path("/p"), base, 0, explicit_failure="arbitrary; DROP")
        with self.assertRaises(ValueError):
            RunOutcome(Path("/p"), base, 0, explicit_failure=FAILURE_NONE)

    def test_nonzero_exit_with_needs_attention_report_is_not_benign(self) -> None:
        # End-to-end: a killed run whose persisted report still carries
        # needs_attention=true must be reported as a crash, never as a flag.
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            job = job_for(settings)
            payload = report(
                "please review",
                status="success",
                sandbox_mode="workspace-write",
            ) | {"needs_attention": True}

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                process = FakeProcess(argv, payload)
                process.killed = True
                return process

            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            self.assertEqual(outcome.exit_code, -15)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.failure_reason, "exit_nonzero")
            self.assertIn("exit code -15", describe_failure("codex", outcome))

    def test_succeeded_semantics_are_unchanged(self) -> None:
        # Fail-closed boundary regression: exit 0 AND status in {success, partial}
        # AND not needs_attention. Nothing in 013 may alter this table.
        table = {
            ("success", False, 0): True,
            ("partial", False, 0): True,
            ("success", True, 0): False,
            ("partial", True, 0): False,
            ("failed", False, 0): False,
            ("failed", True, 0): False,
            ("success", False, 1): False,
            ("success", True, 1): False,
            ("partial", False, -15): False,
        }
        for (status, attention, exit_code), expected in table.items():
            with self.subTest(status=status, attention=attention, exit=exit_code):
                outcome = RunOutcome(
                    Path("/p"),
                    report(
                        "s",
                        status=status,
                        sandbox_mode="workspace-write",
                    )
                    | {"needs_attention": attention},
                    exit_code,
                )
                self.assertEqual(outcome.succeeded, expected)

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
            raw_path = settings.report_dir / f"{job_for(settings).id}.raw.txt"
            self.assertFalse(raw_path.exists())

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
            raw_path = settings.report_dir / f"{job_for(settings).id}.raw.txt"
            self.assertFalse(raw_path.exists())

    def test_exit_zero_with_missing_or_natural_language_report_fails_closed(self) -> None:
        for stdout in (b"", b"Codex completed the task successfully."):
            with self.subTest(stdout=stdout), tempfile.TemporaryDirectory() as directory:
                settings = settings_for(directory)
                settings.ensure_runtime_dirs()

                def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                    return FakeProcess(argv, stdout=stdout)

                job = job_for(settings)
                outcome = CodexRunner(settings, popen_factory=factory).run(job)
                self.assertEqual(outcome.exit_code, 0)
                self.assertFalse(outcome.succeeded)
                self.assertEqual(
                    outcome.report["summary"],
                    "Codex did not produce a valid structured report",
                )
                raw_path = settings.report_dir / f"{job.id}.raw.txt"
                self.assertTrue(raw_path.is_file())

    def test_invalid_prose_output_creates_redacted_bounded_raw_sidecar_and_failed_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory, token="secret-token-12345")
            settings.ensure_runtime_dirs()
            job = job_for(settings)

            secret_prose = (
                b"I completed the task. Token: secret-token-12345 and Bearer abcdef1234567890"
            )

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, stdout=secret_prose)

            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(
                outcome.report["summary"],
                "Codex did not produce a valid structured report",
            )
            raw_path = settings.report_dir / f"{job.id}.raw.txt"
            self.assertTrue(raw_path.is_file())
            content = raw_path.read_text(encoding="utf-8")
            self.assertNotIn("secret-token-12345", content)
            self.assertNotIn("abcdef1234567890", content)
            self.assertIn("[REDACTED]", content)
            import stat

            self.assertEqual(stat.S_IMODE(raw_path.stat().st_mode), 0o600)

    def test_prose_output_classifies_as_invalid_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            job = job_for(settings)

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, stdout=b"all done, no json here")

            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.failure_reason, FAILURE_INVALID_REPORT)
            self.assertEqual(
                describe_failure("codex", outcome),
                "Codex did not produce a valid structured report",
            )

    def test_valid_report_flagging_needs_attention_classifies_distinctly(self) -> None:
        # The 013 production specimen: exit 0, a fully VALID report, and the only
        # problem is a self-flagged human-intervention request. This must never
        # share a message with the crash or unparseable-report classes.
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            job = job_for(settings)
            payload = report(
                "Blocked before modification: target path is outside the writable sandbox",
                status="partial",
                sandbox_mode="workspace-write",
            ) | {"needs_attention": True}

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, payload)

            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.failure_reason, FAILURE_NEEDS_ATTENTION)
            message = describe_failure("codex", outcome)
            self.assertEqual(
                message,
                "Codex completed but flagged needs_attention: Blocked before"
                " modification: target path is outside the writable sandbox",
            )
            self.assertNotEqual(message, "Codex job failed")
            self.assertFalse((settings.report_dir / f"{job.id}.raw.txt").exists())

    def test_invalid_report_file_and_invalid_stdout_fail_closed_and_preserve_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            job = job_for(settings)

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                report_path = Path(argv[argv.index("-o") + 1])
                report_path.write_text('{"status":"success"}', encoding="utf-8")
                return FakeProcess(argv, stdout=b"extra stdout text")

            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(
                outcome.report["summary"],
                "Codex did not produce a valid structured report",
            )
            raw_path = settings.report_dir / f"{job.id}.raw.txt"
            self.assertTrue(raw_path.is_file())
            raw_content = raw_path.read_text(encoding="utf-8")
            self.assertIn('{"status":"success"}', raw_content)
            self.assertIn("extra stdout text", raw_content)

    def test_raw_sidecar_truncation_bound_holds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            job = job_for(settings)
            huge_output = b"A" * (MAX_RAW_DIAGNOSTIC_BYTES * 4)

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, stdout=huge_output)

            CodexRunner(settings, popen_factory=factory).run(job)
            raw_path = settings.report_dir / f"{job.id}.raw.txt"
            self.assertTrue(raw_path.is_file())
            self.assertLessEqual(raw_path.stat().st_size, MAX_RAW_DIAGNOSTIC_BYTES)
            self.assertEqual(raw_path.stat().st_size, MAX_RAW_DIAGNOSTIC_BYTES)

    def test_valid_rerun_clears_stale_raw_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()
            job = job_for(settings)
            raw_path = settings.report_dir / f"{job.id}.raw.txt"

            def failing(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, stdout=b"prose, not a structured report")

            CodexRunner(settings, popen_factory=failing).run(job)
            self.assertTrue(raw_path.is_file())

            def valid(argv: list[str], **kwargs: object) -> FakeProcess:
                return FakeProcess(argv, report(sandbox_mode="workspace-write"))

            outcome = CodexRunner(settings, popen_factory=valid).run(job)
            self.assertTrue(outcome.succeeded)
            self.assertFalse(raw_path.exists())

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

    def test_report_mode_is_normalized_to_job_mode_and_warns_on_discrepancy(self) -> None:
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
                    report(sandbox_mode="read-only"),
                )

            with self.assertLogs("bridge.codex_runner", level="WARNING") as logs:
                outcome = CodexRunner(settings, popen_factory=factory).run(job_for(settings))
            self.assertTrue(outcome.succeeded)
            self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")
            self.assertTrue(
                any("reported sandbox_mode 'read-only' differing from job sandbox_mode 'workspace-write'" in msg for msg in logs.output)
            )

        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            settings.ensure_runtime_dirs()

            def factory(argv: list[str], **kwargs: object) -> FakeProcess:
                payload = report()
                payload["sandbox_mode"] = "unrestricted"
                return FakeProcess(argv, payload)

            job = job_for(settings)
            outcome = CodexRunner(settings, popen_factory=factory).run(job)
            self.assertFalse(outcome.succeeded)
            self.assertEqual(
                outcome.report["summary"],
                "Codex did not produce a valid structured report",
            )
            raw_path = settings.report_dir / f"{job.id}.raw.txt"
            self.assertTrue(raw_path.is_file())

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
            self.assertTrue(outcome.succeeded)
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
