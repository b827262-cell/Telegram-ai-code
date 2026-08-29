# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Qoder Remediation Report

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 REMEDIATION |
| Implementer | Qoder / GLM-5.3-Flash xhigh |
| Date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke` |
| Bridge | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` (unchanged; no commits made) |
| Input review | `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-REVIEW.md` (preserved, not modified) |
| Scope | Non-blocking Opus findings only; 013/014/015 semantics preserved; unrelated dirty/untracked files untouched |
| Host | Linux 7.0.0-29-generic x64, Python 3.12.3, bubblewrap 0.9.0 at `/usr/bin/bwrap`, functional (self-test passes) |

All 015 work remains uncommitted. No git commit/push/amend/reset/rebase/clean/stash was performed. No Sites save/deploy/publish. No credentials read or output.

---

## Exact changed files since pre-remediation (015) state

Only these four paths were touched by remediation:

1. `gpt-codex-bridge/tests/test_sandbox_enforcement.py`
   - Removed `import shutil` and `BWRAP_PRESENT = shutil.which("bwrap") is not None` (binary-presence gate).
   - Added `_bwrap_unavailable_reason` to the `bridge.sandbox` import list.
   - Added module-level `REAL_BWRAP_UNAVAILABLE_REASON = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None)` — the exact authoritative functional self-test used by production enforcement (`sandbox_launch_plan` calls the same function with the same probe).
   - `RealBubblewrapEnforcementTests` is now decorated `@unittest.skipUnless(REAL_BWRAP_UNAVAILABLE_REASON is None, f"functional bwrap enforcement unavailable: {REAL_BWRAP_UNAVAILABLE_REASON}")`.
   - Added new class `RealBwrapGateTests` with 4 tests (see MEDIUM-1 below).
2. `gpt-codex-bridge/bridge/sandbox.py`
   - Module docstring only: added an explicit "Isolation boundary" paragraph. No executable-code change.
3. `gpt-codex-bridge/README.md`
   - "Security boundary" section, doc-only: corrected the now-stale clause "runtime isolation depends on each provider CLI's own sandbox" to attribute runtime enforcement to codex CLI `--sandbox` (codex) and the bubblewrap wrapper (claude/agy), and added one bullet stating exactly what the wrapper enforces and does not enforce.
4. `gpt-codex-bridge/bridge/agy_runner.py`
   - LOW-2 revert only: `report_path.parent.mkdir(...)` keyword order restored to the baseline (`parents=True, exist_ok=True, mode=0o700`). The line now matches baseline and appears only as diff context. No behavior change.

The prior Opus review, all other 015 files, and all unrelated dirty/untracked files (`skills/…`, `web/…`, `reports/…`, `.claude/`, `agents/`, `ai-meeting-room/`, etc.) are preserved untouched.

---

## Finding dispositions

### MEDIUM-1 — Real-bwrap test gating was binary-presence only → FIXED

The gate now reuses the production functional probe:

```python
REAL_BWRAP_UNAVAILABLE_REASON = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None)

@unittest.skipUnless(
    REAL_BWRAP_UNAVAILABLE_REASON is None,
    f"functional bwrap enforcement unavailable: {REAL_BWRAP_UNAVAILABLE_REASON}",
)
class RealBubblewrapEnforcementTests(unittest.TestCase): ...
```

New `RealBwrapGateTests` (4 tests) proves both required behaviors:

- `test_present_but_unusable_bwrap_reports_a_gate_reason` — installs a fake `bwrap` script (`exit 1`) first on `PATH` and calls the real `_bwrap_unavailable_reason("bwrap", None)`: returns a non-None reason containing `self-test failed with exit code 1`, i.e. the skip condition the class gates on. Present-but-unusable → skipped honestly, not 5 red failures.
- `test_present_but_unusable_bwrap_fails_closed_before_launch` — same fake on `PATH`, no mocked probe: production `sandbox_launch_plan("claude", "workspace-write", …)` raises `SandboxEnforcementError` mentioning `'claude'` and `'workspace-write'` before any launch. Present-but-unusable bwrap is fail-closed in production, not just in tests.
- `test_skip_state_matches_the_production_functional_probe` — the class's actual skip state equals (`reason is not None`), and the skip reason embeds the probe's reason. Note: verified empirically on Python 3.12.3 that `skipUnless(True, …)` attaches no `__unittest_skip__` attribute, so the test uses `getattr(cls, "__unittest_skip__", False)`.
- `test_functional_bwrap_still_builds_real_enforcement_plans` (same skip-gated condition) — on a host where the functional probe passes, `sandbox_launch_plan` with the real probe builds a real wrapper argv (`argv_prefix[0] == "bwrap"`, `--die-with-parent` present) and the real-bwrap class is not skipped. Usable bwrap still runs.

### MEDIUM-2 — `--clearenv` + explicit environment allowlist → NOT IMPLEMENTED (residual documented)

Per the task's own escape hatch, the code is unchanged and the finding stands as residual. Reasons it is not safely provable in this pass:

- Proving the allowlist requires knowing the worker's full required-variable set. The worker runs with an `EnvironmentFile=.env` (mode `0600`, not read, per the same protocol the Opus review followed); variable *names* were not enumerated and values were never read or output.
- Even a bounded live Claude/AGY auth probe can only show that the allowlist is sufficient for that invocation, not that it is complete across runtime paths that matter later (proxy variables, token-refresh code paths, locale/encoding, Node runtime variables). The first job that needs a missed variable would fail authentication in production.
- The task's hard requirement — "Never drop required auth/runtime variables" — makes a guessed allowlist the unacceptable failure mode. A denylist that keeps everything else cannot fail that way.
- Current state verified in code: `claude_environment()` / `agy_environment()` strip `TELEGRAM_BOT_TOKEN`, `MCP_BEARER_TOKEN`, `MEETING_API_TOKEN` (plus `GEMINI_API_KEY`, `GOOGLE_API_KEY` for agy) and pass the remainder via `env=` to `_popen`; `sandbox.py`'s new docstring states this boundary explicitly. bwrap mount isolation plus the read-only credential re-bind (`_CREDENTIAL_FILES_READ_ONLY`) bound the impact of leaked variables to read/write-exposed state, not filesystem mutation.
- Feasibility confirmed for a future pass: `bwrap --help` on this host (0.9.0) lists `--clearenv` ("Unset all environment variables"), so the flag itself is available; only the proof of a complete allowlist is missing.

### MEDIUM-3 — Enforcement scope documentation → FIXED

- `bridge/sandbox.py` module docstring now states verbatim that the bubblewrap wrapper is "filesystem and mount-namespace isolation ONLY", does NOT provide network, PID, IPC, UTS, seccomp/syscall-filtering, or resource-limit isolation, is not a full container, and that a wrapped child keeps full network access. Claims about what a sandboxed job cannot do are scoped to filesystem writes.
- `README.md` Security boundary now carries the same explicit non-goals for operators, and the stale pre-015 sentence attributing runtime isolation to "each provider CLI" was corrected. No isolation capability is claimed beyond what exists; the mechanism string `"bubblewrap filesystem isolation"` is unchanged.

### LOW findings

- LOW-1 (socket-test skip handling) — NO ACTION. The review verified the handling is correct (`PermissionError` caught before `OSError`; normal bind still asserted). Changing it would be churn.
- LOW-2 (unrelated `mkdir` kwarg reorder in `agy_runner.py`) — FIXED by revert. Behavior-identical, restores diff focus; the line now matches baseline.
- LOW-3 (`CODEX_SANDBOX_BWRAP_BIN` naming) — NO ACTION. Renaming the variable is a config-interface change: an operator `.env`/systemd setting for the current name would silently stop applying. Not behavior-preserving; documented here instead.
- LOW-4 (`.resolve()` on carve-out paths) — NO ACTION, with analysis beyond the review's note: `.resolve()` is *not* strictly behavior-preserving. If a carve-out path is a symlink whose target lies under a later-bound path (e.g. inside the workspace in `read-only` mode), binding the resolved target would be shadowed by the later workspace bind and the carve-out would be broken — while the current behavior (binding the symlink path itself) preserves the carve-out regardless of symlink topology. The review itself rated the symlink case not exploitable (requires prior write access to HOME).

---

## Runtime evidence

- Host bubblewrap is functional: `bwrap --version` → `bubblewrap 0.9.0`; the production self-test argv (`--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --die-with-parent -- /bin/true`) exits 0 on this host.
- `RealBubblewrapEnforcementTests` ran (not skipped) and passed, executing the real wrapper end-to-end through both runners with the deterministic fixture tool:
  - `test_claude_read_only_kernel_denies_workspace_write` — ok (`TMP_HOST_LEAK=0`, `TMP_WRITE=allowed`, `WORKSPACE_WRITE=denied`, `HOME_WRITE=denied`, `LOOPBACK_BIND=ok`, no workspace violation file)
  - `test_claude_workspace_write_allows_workspace_and_denies_home` — ok
  - `test_claude_danger_full_access_keeps_existing_unrestricted_behavior` — ok
  - `test_agy_read_only_kernel_denies_workspace_write` — ok
  - `test_agy_workspace_write_allows_workspace_and_denies_home` — ok
  - `test_claude_argv_carries_the_wrapper` — ok
- These are the read-only/workspace-write machine-enforcement probes the task asked for, in normal host context. No live provider API calls were made and no external publication occurred (the fixture tool is a local shell script; `danger-full-access` leg runs the same local fixture).
- Gate behavior under an unusable-bwrap environment is proven by the new `RealBwrapGateTests` (fake `bwrap` on `PATH`), which also demonstrates exactly what the Opus review environment would have seen: an honest skip instead of 5 failures.
- `bwrap --help` on this host lists `--clearenv`, `--unshare-net/--unshare-pid/--unshare-ipc`, `--seccomp` — recorded as evidence for the MEDIUM-2 residual (flag available, allowlist proof absent) and for the MEDIUM-3 wording (capabilities exist but are deliberately not used).

---

## Validation (exact commands and counts, all run from `gpt-codex-bridge/`)

| Check | Command | Result |
| --- | --- | --- |
| Diff hygiene | `git diff --check` | PASS (clean) |
| Focused 015 suite | `python3 -m unittest tests.test_sandbox_enforcement` | Ran 28 — OK (24 pre-existing incl. 6 real-bwrap + 4 new gate tests; 0 failed, 0 skipped) |
| Full unittest | `python3 -m unittest discover -s tests -t .` | Ran 206 — OK (baseline 202 + 4 new) |
| Full pytest | `python3 -m pytest -q` | 229 passed, 74 subtests passed (baseline 225 + 4 new), 0 failed |
| Provider suites | `python3 -m unittest tests.test_codex_runner tests.test_claude_router tests.test_agy_runner` | Ran 45 — OK |
| Routing/security/state suites | `python3 -m unittest tests.test_workspaces tests.test_api_workspace_routing tests.test_telegram_workspace_routing tests.test_security tests.test_queue tests.test_worker tests.test_api` | Ran 97 — OK |

One transient failure was observed and fixed during development: the first version of two gate tests accessed `RealBubblewrapEnforcementTests.__unittest_skip__` directly, which does not exist when the skip is inactive (Python 3.12.3 `skipUnless` returns the identity decorator in that case). Fixed with `getattr(..., False)`; both tests then passed.

## Non-regression verification

- **Codex argv unchanged** — direct probe: `CodexRunner.command_for(job, report)` returns `['codex', 'exec', '--sandbox', 'workspace-write', '-C', …]`; no wrapper, no `--ro-bind`. `sandbox_launch_plan("codex", <mode>, ws)` returns an empty prefix for all three modes. Codex diff remains only the new failure constant plus its `describe_failure` branch.
- **014 workspace/publication ceilings unchanged** — direct probe: `ClaudeRunner.command_for` with a non-allowlisted workspace (`/etc`) raises `ConfigurationError` before any sandbox plan is built; routing suites (97 tests) pass; no publication path touched.
- **013 report/needs_attention semantics unchanged** — `FAILURE_CLASSES` has its 7 members (`none`, `timeout`, `exit_nonzero`, `invalid_report`, `needs_attention`, `agent_reported_failure`, `sandbox_enforcement`), the new class is distinct, fail-closed reports are `status="failed"` with `needs_attention=True`, and the report-contract tests pass (`test_report_contract_and_needs_attention_semantics_survive_fail_closed`, `test_sandbox_enforcement_failure_class_is_distinct`).
- `git rev-parse HEAD` = `b1375230a0027aa00b2842a1f6fb669846a67d75` throughout; working tree uncommitted; no remote operations.

## Residual risks

1. **MEDIUM-2 residual (open):** the sandboxed Claude/AGY child still inherits the host environment beyond the denylist-stripped secrets. Mitigations in place: known adapter secrets stripped; filesystem read-only outside carve-outs; OAuth credential files read-only. A future pass should enumerate required variables from the worker's actual environment (names only), then prove with staged canary jobs before enabling `--clearenv`.
2. **Isolation scope (documented, not a defect):** enforcement is filesystem/mount-namespace only. Network, PID, IPC, UTS, seccomp, and resource limits are NOT isolated in any mode, and this is not full container isolation. The code and README now say so explicitly; no isolation claim beyond filesystem writes is made anywhere.
3. **Nested-sandbox contexts** (e.g., a future reviewer running inside the same sandbox design) will now see the real-bwrap class skipped with an honest reason rather than 5 false failures; those environments still cannot certify host-side runtime enforcement.
4. The worker service is still running pre-015 code (015 is uncommitted); real-bwrap enforcement tests pass in the normal host context of this session, which corroborates the worker-context check the Opus review requested, but the service itself must be restarted with the committed change before production reliance.

## Verdict

All required remediation items are resolved: MEDIUM-1 fixed with production-probe gating plus both requested proof directions (present-but-unusable → skip/fail-closed; usable → still runs); MEDIUM-3 fixed in module docstring and operator README; LOW-2 reverted; LOW-1/LOW-3/LOW-4 dispositioned with rationale; MEDIUM-2 left unchanged per the task's explicit conditional, documented as an open residual. No 013/014/015 semantics were weakened; Codex behavior is unchanged; all suites are green in the normal host context.

**PASS_WITH_FINDINGS** — remediation complete; the single open item is the documented MEDIUM-2 residual (environment allowlist requires an auth-completeness proof that cannot be safely produced in this pass).

QODER_GLM53_FLASH_XHIGH_SANDBOX_PARITY_015_REMEDIATION_READY
