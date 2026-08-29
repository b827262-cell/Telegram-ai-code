# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Independent Final RE-REVIEW

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 REREVIEW |
| Reviewer | Claude Opus 5 / medium, independent final reviewer |
| Mode | READ ONLY (no file, service, Git, or external mutation) |
| Review date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` (verified unchanged) |
| Remediator under review | Qoder / GLM-5.3-Flash xhigh, uncommitted working tree |
| Prior review | `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-REVIEW.md` (PASS_WITH_NONBLOCKING_FINDINGS) |
| Host | Linux 7.0.0-29-generic, bubblewrap 0.9.0 at `/usr/bin/bwrap` |

### Reviewer environment (unchanged from prior review — and this time it is the load-bearing test)

This re-review ran from inside the *same* nested-sandbox confinement as the prior review. Re-confirmed:

```
/ ro,nosuid,nodev,relatime,errors=remount-ro
/proc/self/uid_map:  1000  0  1        # nested user namespace
bwrap present at /usr/bin/bwrap, version 0.9.0, but cannot nest
```

Last time this environment was a *liability* — it produced 5 red tests that said nothing about the code, and I had to argue at length that they were artifacts. This time it is the single most valuable instrument available, because MEDIUM-1 was precisely a claim about how the suite behaves in an environment exactly like this one. **I did not have to take Qoder's word for the MEDIUM-1 fix; I was able to falsify or confirm it directly.** See MEDIUM-1 below.

The standing limitation is unchanged and I restate it without softening: **host-side runtime bwrap enforcement remains uncertifiable from this vantage point.** Qoder's reported real-bwrap green run (6 tests, kernel-level EROFS denials) was collected outside this confinement and I can neither reproduce nor contradict it. It is marked ENVIRONMENT-BLOCKED, not PASS.

---

## Remediation diff actually verified (requirement 1)

I diffed the working tree against baseline directly rather than reading Qoder's file list. Full 015 diffstat vs `b137523`:

```
 gpt-codex-bridge/README.md                   |   3 +-
 gpt-codex-bridge/bridge/agy_runner.py        |  60 ++++++--
 gpt-codex-bridge/bridge/claude_runner.py     |  55 ++++++--
 gpt-codex-bridge/bridge/codex_runner.py      |   6 +
 gpt-codex-bridge/bridge/config.py            |   2 +
 gpt-codex-bridge/bridge/sandbox.py           | 185 +++++++++++++++++++++-
 gpt-codex-bridge/tests/test_agy_runner.py    |  50 ++++++-
 gpt-codex-bridge/tests/test_claude_router.py |  31 +++-
 8 files changed, 358 insertions(+), 34 deletions(-)
```
plus untracked `tests/test_sandbox_enforcement.py` and `tests/fixtures/sandbox_probe_tool.sh`.

Discrepancy against Qoder's report, in Qoder's favour: Qoder listed four touched paths. `gpt-codex-bridge/README.md` is a **new path in 015 that the prior review never saw** (it was clean at first review). I read it in full rather than accepting it as doc-only. Confirmed: the change is 1 modified line + 1 added line, both prose, no code. Nothing else outside Qoder's declared set moved.

The claimed LOW-2 revert is real and verifiable by construction: `report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)` now appears in the diff as **context**, not as a `-`/`+` pair — i.e. it is byte-identical to baseline. Qoder did not merely re-reorder; it restored.

---

## Requirement 2 — MEDIUM-1 re-evaluation: FIXED, and independently proven

The gate now reads:

```python
REAL_BWRAP_UNAVAILABLE_REASON = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None)

@unittest.skipUnless(
    REAL_BWRAP_UNAVAILABLE_REASON is None,
    f"functional bwrap enforcement unavailable: {REAL_BWRAP_UNAVAILABLE_REASON}",
)
class RealBubblewrapEnforcementTests(unittest.TestCase): ...
```

**Same authoritative probe — verified, not assumed.** `_bwrap_unavailable_reason` is the identical module-level function `sandbox_launch_plan` calls at `sandbox.py:202` on the production path, imported from `bridge.sandbox`, invoked with the same `DEFAULT_BWRAP_BIN` and `probe=None` (so it uses the real `_execute_probe` subprocess, not a mock). There is no second, parallel, or reimplemented probe. This is what the prior review asked for, exactly.

**Behaviour in a constrained environment — directly observed.** The prior review recorded `Ran 24 — 19 pass, 4 failures + 1 error`. Re-running the identical command in the identical environment now:

```
Ran 28 tests in 0.038s
OK (skipped=7)
```

Six `RealBubblewrapEnforcementTests` + one gate test skip with the honest reason `functional bwrap enforcement unavailable: sandbox wrapper self-test failed with exit code 1`. Zero failures. **The exact false-alarm this finding was about is gone, confirmed by reproduction rather than by report.**

**Both required directions are covered, and the coverage is not circular.** I checked the new `RealBwrapGateTests` for the failure mode these tests usually have — asserting the gate against itself:

- `test_present_but_unusable_bwrap_reports_a_gate_reason` — puts a real `exit 1` script named `bwrap` first on `PATH` and calls the real probe. Passes here. This is a genuine present-but-unusable case, not a mock.
- `test_present_but_unusable_bwrap_fails_closed_before_launch` — same fake, **no mocked probe**, drives production `sandbox_launch_plan("claude","workspace-write",…)` and requires `SandboxEnforcementError`. This is the important one: it proves present-but-unusable bwrap is fail-closed *in production*, not merely skipped in tests. A weaker remediation would have fixed the skip and left this untested.
- `test_skip_state_matches_the_production_functional_probe` — ties the class's actual skip state to the probe result. I checked for an import-time/runtime desync (decorator uses the import-time constant; this test recomputes at call time) and found none: the test does not patch `PATH`, so on any stable host both evaluations see the same environment. Not a latent flake.
- `test_functional_bwrap_still_builds_real_enforcement_plans` — the usable-bwrap direction, itself gated, asserting a real `bwrap` prefix with `--die-with-parent` **and** that the real class is not skipped. Correctly skipped here.

**Does the new gate mask a genuinely broken host?** This was my main concern about the fix, since a functional gate can convert a real defect into a silent skip. It does not, for two reasons: the skip reason is printed verbatim on every run (an operator sees *why*, not just a dot), and `test_present_but_unusable_bwrap_fails_closed_before_launch` runs **unconditionally** — a host where bwrap is broken still gets an ungated, always-executed assertion that production fails closed. The safety property is never skipped; only the positive-enforcement proof is. That is the correct split.

The `getattr(cls, "__unittest_skip__", False)` detail Qoder flagged is correct on Python 3.12.3: `skipUnless(True, …)` returns the identity decorator and attaches no attribute. Direct access would `AttributeError` on exactly the healthy hosts the suite most needs to pass on.

**MEDIUM-1 — CLOSED.**

## Requirement 3 — MEDIUM-3 re-evaluation: FIXED, no overclaim

`sandbox.py` docstring (lines 10–16) and `README.md:56` both now scope the boundary. I grepped both files for isolation vocabulary and read every hit. Findings:

- Every capability word (`network`, `PID`, `IPC`, `UTS`, `seccomp`, resource limits, `full container`) appears **only inside a negation**. There is no sentence anywhere that asserts network, PID, IPC, or syscall isolation.
- The docstring goes further than asked, adding the constraint `"Statements about what a sandboxed job cannot do must be limited to filesystem writes outside the allowlisted write targets."` — a standing instruction to future editors, which is the right durable form for this class of finding.
- It also states the child "inherits the caller's environment minus the adapter secrets the runner environment helpers strip" — i.e. MEDIUM-2 is documented in code, not only in a report that will be archived.
- The one `seccomp` mention outside a negation is `"codex": "provider CLI --sandbox flag (native landlock/seccomp)"`, which describes **codex's** native mechanism and is accurate; it is not a claim about the bwrap wrapper.
- `SANDBOX_MECHANISM_BY_PROVIDER` still reads `"bubblewrap filesystem isolation"` — unchanged, and I re-endorse it.
- The README edit additionally corrects a genuinely stale pre-015 clause that attributed runtime isolation to "each provider CLI's own sandbox," which after 015 was simply false for claude/agy. Good catch by Qoder; the prior review did not flag it because README was untouched at that time.

No network/PID/IPC/seccomp/full-container overclaim exists in the 015 tree. **MEDIUM-3 — CLOSED.**

## Requirement 4 — Is leaving MEDIUM-2 open acceptable before commit?

**Yes. It is non-blocking, and I judge Qoder's refusal to implement it the correct engineering call — not an evasion.** My reasoning, independent of Qoder's:

1. **It is not a regression.** The denylist-plus-inherit pattern is baseline behaviour, pre-dating 015. Committing 015 leaves environment handling exactly where it already is while strictly improving filesystem enforcement. Blocking on MEDIUM-2 would hold a security improvement hostage to an unrelated pre-existing weakness.
2. **The blast radius genuinely shrinks under 015.** Environment inheritance is most dangerous when combined with unrestricted filesystem write. 015 removes that combination for read-only/workspace-write: `/` is read-only, credential files are re-bound read-only over writable state dirs. A leaked variable no longer converts into arbitrary filesystem mutation.
3. **The failure modes are asymmetric, and the asymmetry favours waiting.** A too-broad denylist risks information exposure to a provider CLI the operator already chose to run and already hands credentials to. A too-narrow allowlist breaks authentication in production for every subsequent job. The task brief's own hard requirement is "never drop required auth/runtime variables." Guessing an allowlist against an unread `0600` `.env` is precisely the speculative allowlisting requirement 4 tells me not to demand.
4. **The residual is properly recorded** in `sandbox.py`'s docstring — so it survives in the codebase, not just in a report — and Qoder verified `--clearenv` exists in bwrap 0.9.0, so the future pass is unblocked on mechanism and blocked only on evidence.

I did not read `.env` (mode `0600`), consistent with the prior review. I am confirming a structural judgement, not a measured leak.

Recommendation: track MEDIUM-2 as a **follow-up task**, not a commit blocker. The right sequence is: enumerate variable *names* from the worker's live environment, stage canary jobs through both providers including a token-refresh path, then enable `--clearenv` with a proven allowlist. **Not blocking.**

## Requirement 5 — Non-regression re-verification (executed, not read)

I re-derived each of these by running code, not by trusting either report.

**Codex no-regression — PASS.** Live `CodexRunner.command_for` for all three modes:

```
read-only          ['codex','exec','--sandbox','read-only','-C']         bwrap:False  --ro-bind:False
workspace-write    ['codex','exec','--sandbox','workspace-write','-C']   bwrap:False  --ro-bind:False
danger-full-access ['codex','exec','--sandbox','danger-full-access','-C'] bwrap:False --ro-bind:False
```

`sandbox_launch_plan("codex", …)` returns `prefix_len=0` for all three modes. The codex diff is 6 lines: the new constant, its `FAILURE_CLASSES` member, and a `describe_failure` branch. Argv path untouched.

**Claude/AGY fail-closed machine enforcement — PASS (static + fail-closed leg live).** All 9 provider×mode combinations re-resolved live:

```
codex  read-only/workspace-write/danger-full-access   OK  prefix_len=0
claude read-only        FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
claude workspace-write  FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
claude danger-full-access  OK  prefix_len=0  mech=none (unrestricted by definition)
agy    read-only        FAIL-CLOSED: ...
agy    workspace-write  FAIL-CLOSED: ...
agy    danger-full-access  OK  prefix_len=0  mech=none (unrestricted by definition)
```

Placed in an environment that cannot enforce, the code refuses to launch rather than running unsandboxed while reporting a sandboxed mode. That is the 014 PARTIAL defect, and it does not regress under adverse conditions. Both runners catch `SandboxEnforcementError` in `run()` **before** `_popen` and convert it to a failed report. The positive kernel-denial leg remains ENVIRONMENT-BLOCKED.

**013 failure/needs_attention semantics — PASS.** `FAILURE_CLASSES` has exactly 7 members with `sandbox_enforcement` distinct. Live `describe_failure`:

```
Claude failed before launch: provider 'claude' cannot enforce sandbox mode 'read-only': no bwrap
succeeded: False | failure_reason: sandbox_enforcement
```

Distinct class, `needs_attention=True`, `status="failed"`, no collision with `needs_attention` or `agent_reported_failure`. The codex mode-mismatch check is untouched.

**014 workspace/publication ceilings — PASS, ordering verified.** I specifically tested that the 014 ceiling still gates *before* sandbox logic, since 015 inserts a new failure path into the same method. With a non-allowlisted `/etc` workspace, all three runners raise `ConfigurationError: job workspace is not in CODEX_ALLOWED_WORKSPACES` — **not** `SandboxEnforcementError`. The ceiling remains authoritative and first; `danger-full-access` acquires no publication privilege. No publication path is touched by the diff.

## Requirement 6 — Test evidence (re-run by this reviewer)

| Suite | Command | Result now | Prior review |
| --- | --- | --- | --- |
| Focused 015 | `python3 -m unittest tests.test_sandbox_enforcement` | **Ran 28 — OK (skipped=7)** | Ran 24 — 4 failures + 1 error |
| Full unittest | `python3 -m unittest discover -s tests -t .` | **Ran 206 — OK (skipped=7)** | Ran 202 — 4 failures + 1 error |
| Full pytest | `python3 -m pytest -q` | **222 passed, 7 skipped, 74 subtests, 0 failed** | 220 passed, 5 failed |
| Diff hygiene | `git diff --check` | **CLEAN** | PASS |

**Zero failures in a constrained environment, where the prior review had five.** The single warning is `PytestCacheWarning` from the read-only `.pytest_cache` mount — a reviewer-environment artifact, not a code issue.

Environment-artifact vs. host-defect distinction, stated exactly as required: the 7 skips are **environment artifacts** (nested namespace unavailable), correctly and honestly reported by the new gate rather than surfacing as failures. My counts differ from Qoder's host counts (28 OK/0 skipped, 206 OK, 229 passed) precisely and only in the skip column, which is the expected and correct difference between a functional-bwrap host and a nested sandbox. **The two result sets are consistent, and their difference is itself evidence the gate works.**

## Requirement 7 — Publication check: PASS

- `git rev-parse HEAD` = `b1375230a0027aa00b2842a1f6fb669846a67d75` — unchanged.
- `git reflog -n 5` — top entry is still the baseline commit. No commit, amend, reset, or rebase occurred during remediation or this review.
- `git stash list` — only the one pre-existing `safety/pre-origin-integration` stash; nothing new.
- Current branch `web/codex-sample-standard` shows `[ahead 5]` vs `telegram-ai-code`. **Verified pre-existing:** `git log telegram-ai-code/web/codex-sample-standard..b137523 | wc -l` = 5, so the entire ahead-count is attributable to the baseline commit. Nothing was pushed.
- All 015 changes remain uncommitted. No Sites save/deploy/publish, no `gh` invocation, no external publication observed or performed.

I mutated nothing: no tracked file, service, Git state, or external system. All probes were read-only or confined to `/tmp` via `TemporaryDirectory`.

## Requirement 8 — New defects introduced by remediation

**None found.** I actively looked for the four ways this specific remediation could have gone wrong:

1. *Functional gate masking a real host defect* — ruled out; the fail-closed assertion is ungated and the skip reason is always printed (see MEDIUM-1).
2. *Import-time vs. runtime probe desync in the consistency test* — ruled out; no `PATH` patch in that test.
3. *Doc changes overclaiming* — ruled out by grep + full read; all capability words are negations.
4. *LOW-2 "revert" that silently changed something else* — ruled out; the line is diff context, byte-identical to baseline.

Additionally: the `RealBwrapGateTests` fake-`bwrap` fixture writes only into a `TemporaryDirectory` and patches `PATH` via `patch.dict` with `ExitStack` cleanup, so it cannot leak into the host `PATH` or leave artifacts. No `sbx-acceptance-*` artifacts were created by any run.

## Residual findings (all non-blocking, all carried forward unchanged)

- **MEDIUM-2** — environment inheritance; open by design, documented in `sandbox.py`. Follow-up task, not a blocker. Rationale above.
- **LOW-3** — `CODEX_SANDBOX_BWRAP_BIN` governs only claude/agy. Qoder's NO ACTION is correct: renaming is a config-interface break that would silently stop applying an operator's existing setting. Better addressed with a doc note than a rename.
- **LOW-4** — no `.resolve()` on carve-out paths. Qoder's NO ACTION comes with analysis that **improves on my prior review**, and I accept it: `.resolve()` is not behaviour-preserving, because a carve-out symlinking under a later-bound path would be shadowed by the workspace bind and the carve-out silently broken. Binding the symlink path itself is the safer default. My original suggestion was the weaker option; I withdraw it.
- **Verification owed before production reliance (unchanged, not a code change):** confirm on the host, outside any nested sandbox, that `RealBubblewrapEnforcementTests` passes under the worker's actual runtime context. Qoder reports 6/6 green in normal host context, which substantially answers this, but the worker service still runs pre-015 code and must be restarted after commit.

---

## Verdict on Qoder's claims

| Claim | Verified independently | Verdict |
| --- | --- | --- |
| MEDIUM-1 fixed via production functional probe | Same function as `sandbox.py:202`; suite now `OK (skipped=7)` in the very environment that previously produced 5 failures | **CONFIRMED** |
| Both directions tested (unusable → skip/fail-closed; usable → runs) | Read all 4 gate tests; fail-closed test is ungated and uses production path | **CONFIRMED** |
| MEDIUM-3 fixed, scoped to filesystem/mount-namespace | Docstring + README read in full; grep shows capability words only in negations | **CONFIRMED** |
| LOW-2 reverted | Line is diff context, byte-identical to baseline | **CONFIRMED** |
| MEDIUM-2 intentionally open | Verified unchanged in code, documented in docstring | **CONFIRMED, and I endorse the decision** |
| Host gates 28/28, 206/206, 229 + 74 subtests | Cannot reproduce host-side (nested sandbox). My constrained-env equivalents are 28 OK/7 skipped, 206 OK, 222+7 — consistent, differing only in the skip column | **CONSISTENT / ENVIRONMENT-BLOCKED** |
| `git diff --check` clean | Re-run | **CONFIRMED** |
| No commit/push | HEAD, reflog, stash, ahead-count all verified | **CONFIRMED** |

Qoder's report was accurate everywhere I could check it. Its one omission — not listing `README.md` in the headline four while describing it in the body — was in the honest direction, and the file's content matched the description.

## Answer to the primary question

015 provides **truthful, fail-closed, machine-enforced** sandbox semantics for Claude and AGY without weakening Codex, 014, or 013.

- **Truthful** — no silent downgrade or upgrade; mechanism strings accurately scoped; enforcement never faked with provider CLI permission flags. The remediation strengthened this by documenting the boundary in code and correcting a stale README claim.
- **Fail-closed** — re-verified live for all 9 combinations plus the present-but-unusable-bwrap case, which now has a dedicated ungated production-path test.
- **Machine-enforced** — sound by construction; positive host runtime proof remains ENVIRONMENT-BLOCKED from this vantage point, unchanged from the prior review and honestly so.
- **Non-regression** — Codex argv byte-identical, 014 ceilings verified to gate *first*, 013 semantics preserved and cleanly extended.

## FINAL_VERDICT

**PASS_WITH_NONBLOCKING_FINDINGS**

Both blocking-candidate findings from the prior review are closed. MEDIUM-1 is not merely claimed fixed — it is the one finding I could falsify from this environment, and it survived: the suite that previously produced 5 false failures here now reports 7 honest skips and zero failures, with the safety assertion deliberately left ungated so a broken host cannot hide behind the skip. MEDIUM-3 is closed with wording that constrains future editors rather than just patching today's text. MEDIUM-2 is correctly left open: it is a pre-existing, non-regressing condition whose only available fix in this pass would have been a guessed allowlist that could break production authentication — exactly the speculative change this review was told not to demand. No new defect was introduced by the remediation.

### 015 is READY_TO_COMMIT — **YES**

Commit conditions (none blocking, all post-commit):
1. Restart `gpt-codex-worker.service` after commit; it currently runs pre-015 code.
2. Record the host-context `RealBubblewrapEnforcementTests` green run under the worker's runtime context as the durable enforcement evidence this reviewer could not produce from inside the nested sandbox.
3. Open MEDIUM-2 (`--clearenv` + proven allowlist) as a tracked follow-up with the canary-job protocol described above.

CLAUDE_OPUS5_MEDIUM_SANDBOX_PARITY_015_REREVIEW_COMPLETE
