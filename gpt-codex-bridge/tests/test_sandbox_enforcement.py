from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bridge.agy_runner import AGY_MODEL, AgyRunner
from bridge.claude_runner import CLAUDE_EFFORT, CLAUDE_MODEL, ClaudeRunner
from bridge.codex_runner import (
    FAILURE_AGENT_REPORTED,
    FAILURE_CLASSES,
    FAILURE_INVALID_REPORT,
    FAILURE_NEEDS_ATTENTION,
    FAILURE_SANDBOX_ENFORCEMENT,
    FAILURE_TIMEOUT,
    RunOutcome,
    describe_failure,
)
from bridge.config import Settings
from bridge.models import Job
from bridge.sandbox import (
    DEFAULT_BWRAP_BIN,
    SANDBOX_MECHANISM_BY_PROVIDER,
    SandboxEnforcementError,
    SandboxModeError,
    _bwrap_unavailable_reason,
    sandbox_launch_plan,
)
from bridge.worker import Worker

FIXTURE_TOOL = Path(__file__).resolve().parent / "fixtures" / "sandbox_probe_tool.sh"
# Gate the real-bwrap class on the same functional self-test production
# enforcement uses, not on binary presence: a present-but-unusable bwrap
# (nested sandboxes, hardened CI) must skip honestly instead of producing
# meaningless failures.
REAL_BWRAP_UNAVAILABLE_REASON = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None)


def probe_ok(argv: list[str]) -> SimpleNamespace:
    return SimpleNamespace(returncode=0)


def probe_exit(returncode: int):
    def _probe(argv: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode)

    return _probe


def probe_boom(argv: list[str]) -> SimpleNamespace:
    raise OSError("probe exploded")


def make_settings(directory: str, **extra: str) -> Settings:
    workspace = Path(directory) / "repo"
    workspace.mkdir()
    return Settings.from_env(
        {
            "CODEX_ALLOWED_WORKSPACES": str(workspace),
            "CODEX_DEFAULT_WORKSPACE": str(workspace),
            "CODEX_BRIDGE_DATA_DIR": str(Path(directory) / "state"),
            "TELEGRAM_BOT_TOKEN": "bot-secret",
            "TELEGRAM_ALLOWED_CHAT_ID": "42",
            **extra,
        },
        root_dir=Path(__file__).resolve().parents[1],
    )


def fake_home(directory: str) -> Path:
    home = Path(directory) / "home"
    for relative in (
        ".claude",
        ".cache/claude-cli-nodejs",
        ".local/state/claude",
        ".gemini",
    ):
        (home / relative).mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("credential", encoding="utf-8")
    (home / ".gemini" / "oauth_creds.json").write_text("credential", encoding="utf-8")
    return home


def claude_job(settings: Settings, *, sandbox_mode: str) -> Job:
    return Job(
        id="job-sandboxclaude01",
        chat_id="42",
        prompt="probe the sandbox",
        workspace=settings.default_workspace,
        status="running",
        created_at="now",
        provider="claude",
        sandbox_mode=sandbox_mode,
    )


def agy_job(settings: Settings, *, sandbox_mode: str) -> Job:
    return Job(
        id="job-sandboxagy00001",
        chat_id="42",
        prompt="probe the sandbox",
        workspace=settings.default_workspace,
        status="running",
        created_at="now",
        provider="agy",
        sandbox_mode=sandbox_mode,
    )


class SandboxLaunchPlanMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stack = ExitStack()
        self.addCleanup(self._stack.close)
        self.directory = self._stack.enter_context(tempfile.TemporaryDirectory())
        self.workspace = Path(self.directory) / "repo"
        self.workspace.mkdir()
        self.home = fake_home(self.directory)
        self._stack.enter_context(
            patch.dict("os.environ", {"HOME": str(self.home)})
        )
        self._stack.enter_context(
            patch("bridge.sandbox._execute_probe", side_effect=probe_ok)
        )

    def expected_bwrap_prefix(self, *, workspace_write: bool) -> list[str]:
        flag = "--bind" if workspace_write else "--ro-bind"
        return [
            DEFAULT_BWRAP_BIN,
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(self.home / ".claude"),
            str(self.home / ".claude"),
            "--bind",
            str(self.home / ".cache" / "claude-cli-nodejs"),
            str(self.home / ".cache" / "claude-cli-nodejs"),
            "--bind",
            str(self.home / ".local" / "state" / "claude"),
            str(self.home / ".local" / "state" / "claude"),
            "--ro-bind",
            str(self.home / ".claude" / ".credentials.json"),
            str(self.home / ".claude" / ".credentials.json"),
            flag,
            str(self.workspace),
            str(self.workspace),
            "--die-with-parent",
            "--",
        ]

    def test_codex_uses_native_cli_mechanism_with_no_wrapper_for_all_modes(self) -> None:
        for mode in ("read-only", "workspace-write", "danger-full-access"):
            with self.subTest(mode=mode):
                plan = sandbox_launch_plan("codex", mode, self.workspace)
                self.assertEqual(plan.argv_prefix, ())
                self.assertEqual(plan.sandbox_mode, mode)
                self.assertEqual(plan.mechanism, SANDBOX_MECHANISM_BY_PROVIDER["codex"])

    def test_claude_and_agy_mechanism_is_declared_centralized(self) -> None:
        self.assertEqual(
            SANDBOX_MECHANISM_BY_PROVIDER["claude"],
            "bubblewrap filesystem isolation",
        )
        self.assertEqual(
            SANDBOX_MECHANISM_BY_PROVIDER["agy"],
            "bubblewrap filesystem isolation",
        )

    def test_claude_read_only_prefix_is_exact_ro_workspace_bind(self) -> None:
        plan = sandbox_launch_plan("claude", "read-only", self.workspace)
        self.assertEqual(list(plan.argv_prefix), self.expected_bwrap_prefix(workspace_write=False))

    def test_claude_workspace_write_prefix_is_exact_rw_workspace_bind(self) -> None:
        plan = sandbox_launch_plan("claude", "workspace-write", self.workspace)
        self.assertEqual(list(plan.argv_prefix), self.expected_bwrap_prefix(workspace_write=True))

    def test_agy_prefix_matches_claude_layout_with_gemini_state(self) -> None:
        plan = sandbox_launch_plan("agy", "read-only", self.workspace)
        prefix = list(plan.argv_prefix)
        self.assertEqual(prefix[0], DEFAULT_BWRAP_BIN)
        self.assertIn("--bind", prefix)
        gemini_rw = prefix.index("--bind")
        self.assertEqual(prefix[gemini_rw + 1], str(self.home / ".gemini"))
        self.assertIn(
            (
                "--ro-bind",
                str(self.home / ".gemini" / "oauth_creds.json"),
            ),
            list(zip(prefix, prefix[1:])),
        )
        self.assertNotIn(str(self.home / ".claude"), prefix)

    def test_danger_full_access_is_unrestricted_by_definition(self) -> None:
        for provider in ("claude", "agy"):
            with self.subTest(provider=provider):
                plan = sandbox_launch_plan(provider, "danger-full-access", self.workspace)
                self.assertEqual(plan.argv_prefix, ())
                self.assertEqual(plan.mechanism, "none (unrestricted by definition)")
                self.assertEqual(plan.sandbox_mode, "danger-full-access")

    def test_missing_state_carve_outs_are_excluded_not_created(self) -> None:
        empty_home = Path(self.directory) / "empty-home"
        empty_home.mkdir()
        plan = sandbox_launch_plan(
            "claude",
            "read-only",
            self.workspace,
            env={"HOME": str(empty_home)},
        )
        prefix = list(plan.argv_prefix)
        self.assertNotIn(str(empty_home / ".claude"), prefix)
        self.assertIn("--ro-bind", prefix)
        workspace_index = prefix.index("--ro-bind", 10)
        self.assertEqual(prefix[workspace_index + 1], str(self.workspace))

    def test_unknown_provider_fails_closed(self) -> None:
        with self.assertRaises(SandboxEnforcementError) as raised:
            sandbox_launch_plan("unknown-provider", "read-only", self.workspace)
        self.assertIn("no sandbox enforcement mapping", raised.exception.reason)

    def test_invalid_mode_is_rejected_without_fallback(self) -> None:
        with self.assertRaises(SandboxModeError):
            sandbox_launch_plan("claude", "unrestricted", self.workspace)

    def test_enforce_argv_prepends_prefix_exactly_once(self) -> None:
        plan = sandbox_launch_plan("claude", "read-only", self.workspace)
        combined = plan.enforce_argv(["claude", "-p", "hi"])
        self.assertEqual(
            combined,
            [*plan.argv_prefix, "claude", "-p", "hi"],
        )


class SandboxFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stack = ExitStack()
        self.addCleanup(self._stack.close)
        self.directory = self._stack.enter_context(tempfile.TemporaryDirectory())
        self.home = fake_home(self.directory)
        self._stack.enter_context(
            patch.dict("os.environ", {"HOME": str(self.home)})
        )

    def test_probe_failure_fails_closed_before_any_process_launch(self) -> None:
        settings = make_settings(self.directory)
        settings.ensure_runtime_dirs()
        launched: list[list[str]] = []

        def factory(argv: list[str], **kwargs: object) -> object:
            launched.append(argv)
            raise AssertionError("no process may be launched")

        with patch("bridge.sandbox._execute_probe", side_effect=probe_exit(1)):
            outcome = ClaudeRunner(settings, popen_factory=factory).run(
                claude_job(settings, sandbox_mode="read-only")
            )

        self.assertEqual(launched, [])
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_reason, FAILURE_SANDBOX_ENFORCEMENT)
        self.assertEqual(outcome.report["status"], "failed")
        self.assertTrue(outcome.report["needs_attention"])
        self.assertEqual(outcome.report["sandbox_mode"], "read-only")
        self.assertIn("self-test failed with exit code 1", outcome.report["summary"])
        self.assertIn("'claude'", outcome.report["summary"])
        self.assertIn("'read-only'", outcome.report["summary"])

    def test_missing_bwrap_binary_fails_closed_before_probe(self) -> None:
        settings = make_settings(self.directory)
        with patch("bridge.sandbox._execute_probe", side_effect=probe_boom) as probe:
            with self.assertRaises(SandboxEnforcementError) as raised:
                sandbox_launch_plan(
                    "claude",
                    "workspace-write",
                    settings.default_workspace,
                    bwrap_bin="/nonexistent/bwrap",
                )
        probe.assert_not_called()
        self.assertIn("is not installed", raised.exception.reason)

    def test_probe_crash_is_reported_as_class_name_only(self) -> None:
        settings = make_settings(self.directory)
        with patch("bridge.sandbox._execute_probe", side_effect=probe_boom):
            with self.assertRaises(SandboxEnforcementError) as raised:
                sandbox_launch_plan(
                    "claude",
                    "read-only",
                    settings.default_workspace,
                )
        self.assertIn("self-test failed: OSError", raised.exception.reason)
        self.assertNotIn("probe exploded", raised.exception.reason)

    def test_agy_fail_closed_writes_distinct_failed_report(self) -> None:
        settings = make_settings(self.directory)
        settings.ensure_runtime_dirs()

        def factory(argv: list[str], **kwargs: object) -> object:
            raise AssertionError("no process may be launched")

        with patch("bridge.sandbox._execute_probe", side_effect=probe_exit(1)):
            outcome = AgyRunner(settings, popen_factory=factory).run(
                agy_job(settings, sandbox_mode="workspace-write")
            )

        self.assertEqual(outcome.failure_reason, FAILURE_SANDBOX_ENFORCEMENT)
        self.assertEqual(outcome.report["sandbox_mode"], "workspace-write")
        message = describe_failure("agy", outcome)
        self.assertTrue(message.startswith("Agy failed before launch: "))
        self.assertIn("cannot enforce sandbox mode", message)

    def test_sandbox_enforcement_failure_class_is_distinct(self) -> None:
        def report(summary: str) -> dict:
            return {
                "status": "failed",
                "summary": summary,
                "changed_files": [],
                "tests": [],
                "git_status": "",
                "needs_attention": True,
                "sandbox_mode": "read-only",
            }

        outcomes = {
            FAILURE_TIMEOUT: RunOutcome(Path("/p"), report("t"), -1, timed_out=True),
            FAILURE_INVALID_REPORT: RunOutcome(
                Path("/p"), report("bad report shape"), 0,
                explicit_failure=FAILURE_INVALID_REPORT,
            ),
            FAILURE_NEEDS_ATTENTION: RunOutcome(
                Path("/p"), report("needs a human"), 0,
                explicit_failure=FAILURE_NEEDS_ATTENTION,
            ),
            FAILURE_AGENT_REPORTED: RunOutcome(Path("/p"), report("agent says no"), 0),
            FAILURE_SANDBOX_ENFORCEMENT: RunOutcome(
                Path("/p"),
                report(
                    "provider 'claude' cannot enforce sandbox mode "
                    "'read-only': sandbox wrapper self-test failed with exit code 1"
                ),
                0,
                explicit_failure=FAILURE_SANDBOX_ENFORCEMENT,
            ),
        }
        messages = {
            reason: describe_failure("claude", outcome)
            for reason, outcome in outcomes.items()
        }
        self.assertEqual(len(set(messages.values())), len(messages), messages)
        self.assertIn(FAILURE_SANDBOX_ENFORCEMENT, FAILURE_CLASSES)
        self.assertTrue(messages[FAILURE_SANDBOX_ENFORCEMENT].startswith("Claude failed before launch: "))

    def test_worker_records_fail_closed_error_without_launching(self) -> None:
        settings = make_settings(self.directory)
        settings.ensure_runtime_dirs()
        from bridge.queue import JobQueue

        queue = JobQueue(Path(self.directory) / "jobs.sqlite3")
        job = queue.submit(
            chat_id="42",
            prompt="probe",
            workspace=settings.default_workspace,
            provider="claude",
            sandbox_mode="read-only",
        )

        class NoLaunchRunner:
            def run(self, job: Job) -> RunOutcome:
                raise AssertionError("must not be used")

        claude = ClaudeRunner(
            settings,
            popen_factory=lambda argv, **kwargs: (_ for _ in ()).throw(
                AssertionError("no process may be launched")
            ),
        )
        with patch("bridge.sandbox._execute_probe", side_effect=probe_exit(1)):
            Worker(queue, NoLaunchRunner(), claude_runner=claude).run_once()

        finished = queue.get(job.id)
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "failed")
        self.assertIn("Claude failed before launch", finished.error or "")

    def test_report_contract_and_needs_attention_semantics_survive_fail_closed(self) -> None:
        settings = make_settings(self.directory)
        settings.ensure_runtime_dirs()

        def factory(argv: list[str], **kwargs: object) -> object:
            raise AssertionError("no process may be launched")

        with patch("bridge.sandbox._execute_probe", side_effect=probe_exit(1)):
            outcome = ClaudeRunner(settings, popen_factory=factory).run(
                claude_job(settings, sandbox_mode="read-only")
            )
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_reason, FAILURE_SANDBOX_ENFORCEMENT)
        self.assertNotIn("bot-secret", outcome.report["summary"])


class RealBwrapGateTests(unittest.TestCase):
    """The real-bwrap class must gate on functional capability, not presence."""

    def setUp(self) -> None:
        self._stack = ExitStack()
        self.addCleanup(self._stack.close)
        self.directory = self._stack.enter_context(tempfile.TemporaryDirectory())

    def _prepend_unusable_bwrap_to_path(self) -> None:
        fake = Path(self.directory) / "bin" / "bwrap"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake.chmod(0o755)
        self._stack.enter_context(
            patch.dict(
                os.environ,
                {"PATH": f"{fake.parent}:{os.environ.get('PATH', '')}"},
            )
        )

    def test_present_but_unusable_bwrap_reports_a_gate_reason(self) -> None:
        self._prepend_unusable_bwrap_to_path()
        reason = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None)
        self.assertIsNotNone(reason)
        self.assertIn("self-test failed with exit code 1", reason)

    def test_present_but_unusable_bwrap_fails_closed_before_launch(self) -> None:
        self._prepend_unusable_bwrap_to_path()
        settings = make_settings(self.directory)
        with self.assertRaises(SandboxEnforcementError) as raised:
            sandbox_launch_plan(
                "claude",
                "workspace-write",
                settings.default_workspace,
            )
        self.assertIn("self-test failed with exit code 1", raised.exception.reason)
        self.assertIn("'claude'", str(raised.exception))
        self.assertIn("'workspace-write'", str(raised.exception))

    def test_skip_state_matches_the_production_functional_probe(self) -> None:
        reason = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None)
        # skipUnless only attaches __unittest_skip__ when the skip is active.
        skip_active = getattr(RealBubblewrapEnforcementTests, "__unittest_skip__", False)
        self.assertEqual(skip_active, reason is not None)
        if skip_active:
            self.assertIn(reason, RealBubblewrapEnforcementTests.__unittest_skip_why__)

    @unittest.skipUnless(
        REAL_BWRAP_UNAVAILABLE_REASON is None,
        f"functional bwrap enforcement unavailable: {REAL_BWRAP_UNAVAILABLE_REASON}",
    )
    def test_functional_bwrap_still_builds_real_enforcement_plans(self) -> None:
        settings = make_settings(self.directory)
        plan = sandbox_launch_plan(
            "claude",
            "workspace-write",
            settings.default_workspace,
        )
        self.assertEqual(plan.argv_prefix[0], DEFAULT_BWRAP_BIN)
        self.assertIn("--die-with-parent", plan.argv_prefix)
        self.assertFalse(
            getattr(RealBubblewrapEnforcementTests, "__unittest_skip__", False),
            "a functional bwrap host must run the real enforcement tests",
        )


@unittest.skipUnless(
    REAL_BWRAP_UNAVAILABLE_REASON is None,
    f"functional bwrap enforcement unavailable: {REAL_BWRAP_UNAVAILABLE_REASON}",
)
class RealBubblewrapEnforcementTests(unittest.TestCase):
    """Run the real wrapper end-to-end through each runner.

    These tests prove machine enforcement (kernel-level EROFS denials), not
    prompt cooperation: the fixture tool always attempts the writes and only
    reports what the kernel actually allowed.
    """

    def setUp(self) -> None:
        self._stack = ExitStack()
        self.addCleanup(self._stack.close)
        self.directory = self._stack.enter_context(tempfile.TemporaryDirectory())
        self.settings = make_settings(
            self.directory,
            CLAUDE_BIN=str(FIXTURE_TOOL),
            AGY_BIN=str(FIXTURE_TOOL),
        )
        self.settings.ensure_runtime_dirs()
        self.host_canary = (
            Path(tempfile.gettempdir())
            / f".sbx-host-canary-{os.getpid()}-{id(self)}"
        )
        self.host_canary.write_text("host", encoding="utf-8")
        self._stack.callback(self.host_canary.unlink, missing_ok=True)

    def _run_claude(self, sandbox_mode: str):
        return ClaudeRunner(self.settings).run(
            claude_job(self.settings, sandbox_mode=sandbox_mode)
        )

    def _run_agy(self, sandbox_mode: str):
        return AgyRunner(self.settings).run(
            agy_job(self.settings, sandbox_mode=sandbox_mode)
        )

    def test_claude_read_only_kernel_denies_workspace_write(self) -> None:
        outcome = self._run_claude("read-only")
        self.assertTrue(outcome.succeeded, outcome.report["summary"])
        summary = outcome.report["summary"]
        self.assertIn("TMP_HOST_LEAK=0", summary)
        self.assertIn("TMP_WRITE=allowed", summary)
        self.assertIn("WORKSPACE_WRITE=denied", summary)
        self.assertIn("HOME_WRITE=denied", summary)
        self.assertFalse(
            (self.settings.default_workspace / ".sbx-workspace-violation").exists()
        )
        if "LOOPBACK_BIND=permission_denied" in summary:
            self.skipTest("sandbox enforces socket() denial (PermissionError)")
        self.assertIn("LOOPBACK_BIND=ok", summary)

    def test_claude_workspace_write_allows_workspace_and_denies_home(self) -> None:
        outcome = self._run_claude("workspace-write")
        self.assertTrue(outcome.succeeded, outcome.report["summary"])
        summary = outcome.report["summary"]
        self.assertIn("WORKSPACE_WRITE=allowed", summary)
        self.assertIn("HOME_WRITE=denied", summary)
        self.assertIn("TMP_HOST_LEAK=0", summary)
        self.assertTrue(
            (self.settings.default_workspace / ".sbx-workspace-violation").exists()
        )
        if "LOOPBACK_BIND=permission_denied" in summary:
            self.skipTest("sandbox enforces socket() denial (PermissionError)")

    def test_claude_danger_full_access_keeps_existing_unrestricted_behavior(self) -> None:
        outcome = self._run_claude("danger-full-access")
        self.assertTrue(outcome.succeeded, outcome.report["summary"])
        summary = outcome.report["summary"]
        self.assertIn("WORKSPACE_WRITE=allowed", summary)
        self.assertIn("TMP_HOST_LEAK=1", summary)
        self.assertEqual(outcome.report["sandbox_mode"], "danger-full-access")

    def test_agy_read_only_kernel_denies_workspace_write(self) -> None:
        outcome = self._run_agy("read-only")
        self.assertTrue(outcome.succeeded, outcome.report["summary"])
        summary = outcome.report["summary"]
        self.assertIn("TMP_HOST_LEAK=0", summary)
        self.assertIn("WORKSPACE_WRITE=denied", summary)
        self.assertIn("HOME_WRITE=denied", summary)
        self.assertFalse(
            (self.settings.default_workspace / ".sbx-workspace-violation").exists()
        )
        if "LOOPBACK_BIND=permission_denied" in summary:
            self.skipTest("sandbox enforces socket() denial (PermissionError)")
        self.assertIn("LOOPBACK_BIND=ok", summary)

    def test_agy_workspace_write_allows_workspace_and_denies_home(self) -> None:
        outcome = self._run_agy("workspace-write")
        self.assertTrue(outcome.succeeded, outcome.report["summary"])
        summary = outcome.report["summary"]
        self.assertIn("WORKSPACE_WRITE=allowed", summary)
        self.assertIn("HOME_WRITE=denied", summary)
        self.assertTrue(
            (self.settings.default_workspace / ".sbx-workspace-violation").exists()
        )

    def test_claude_argv_carries_the_wrapper(self) -> None:
        argv = ClaudeRunner(self.settings).command_for(
            claude_job(self.settings, sandbox_mode="read-only")
        )
        self.assertEqual(argv[0], self.settings.sandbox_bwrap_bin)
        self.assertIn("--", argv)
        provider_argv_start = argv.index("--") + 1
        self.assertEqual(
            argv[provider_argv_start:],
            [
                self.settings.claude_bin,
                "-p",
                "probe the sandbox",
                "--model",
                CLAUDE_MODEL,
                "--effort",
                CLAUDE_EFFORT,
            ],
        )


class LoopbackBindEnvironmentTests(unittest.TestCase):
    def test_normal_environment_loopback_bind_executes(self) -> None:
        bound = socket.socket()
        self.addCleanup(bound.close)
        bound.bind(("127.0.0.1", 0))
        self.assertTrue(bound.getsockname()[0].startswith("127."))


if __name__ == "__main__":
    unittest.main()
