# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Independent Commit-Readiness Final Review

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 COMMIT-READINESS FINAL REVIEW |
| Reviewer | Claude Opus 5 / medium, independent final reviewer |
| Mode | READ ONLY — no tracked file, service, Git state, or external system mutated |
| Date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` — verified unchanged at start and end |
| Under review | Qoder / GLM-5.3-Flash xhigh commit-readiness report claiming exact 11 paths, READY_TO_COMMIT=YES |
| Prior artifacts | `-CLAUDE-REVIEW.md`, `-QODER-REMEDIATION.md`, `-CLAUDE-REREVIEW.md` |
| Note | This report supersedes an earlier draft of this same file in the handoff directory. |

### Reviewer environment (material to two findings)

Same nested-sandbox confinement as the prior two reviews, re-confirmed this session:

```
/ ro,nosuid,nodev,relatime,errors=remount-ro
/proc/self/uid_map:  1000  0  1          # nested user namespace
bwrap 0.9.0 present at /usr/bin/bwrap; nesting probe exits 1
  ("No permissions to create new namespace")
```

Two consequences I state up front rather than burying:

1. **Host-context runtime bwrap enforcement remains uncertifiable from here.** Qoder's real-bwrap green leg (28 OK / 0 skipped) was collected outside this confinement. I can neither reproduce nor contradict it. Marked ENVIRONMENT-BLOCKED, not PASS — unchanged from the re-review.
2. **`/home/b827262` is read-only to me.** I therefore could not place my verification candidate where Qoder placed theirs. I built it under `/tmp` instead. This is not a defect in Qoder's method — it is my constraint, and it turned out to be a useful natural experiment for Requirement 8 (below).

I mutated nothing. My only writes were a `/tmp` scratch candidate, since deleted, and this report.

---

## Requirement 1 — Independent derivation of the commit path set

I derived the set from the repository, not from Qoder's manifest, then compared.

Full working-tree diffstat vs `b137523`, restricted to nothing (whole repo):

```
 gpt-codex-bridge/README.md                      |   3 +-
 gpt-codex-bridge/bridge/agy_runner.py           |  60 ++++++--
 gpt-codex-bridge/bridge/claude_runner.py        |  55 +++++--
 gpt-codex-bridge/bridge/codex_runner.py         |   6 +
 gpt-codex-bridge/bridge/config.py               |   2 +
 gpt-codex-bridge/bridge/sandbox.py              | 185 +++++++++++++++++++++-
 gpt-codex-bridge/tests/test_agy_runner.py       |  50 +++++-
 gpt-codex-bridge/tests/test_claude_router.py    |  31 +++-
 reports/sites-version-state.json                |   2 +-
 skills/gpt-agy-claude-development-loop/SKILL.md |  50 +++++-   <- NOT 015
 web/README.md                                   |  51 +++++-   <- NOT 015
 web/STANDARD.md                                 |   9 +-       <- NOT 015
 web/sample/src/App.tsx                          | 196 ++++++--- <- NOT 015
 web/sample/src/styles.css                       | 113 +++++++-  <- NOT 015
```

plus untracked `gpt-codex-bridge/tests/test_sandbox_enforcement.py` and `gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh`.

**Completeness.** `git status --porcelain gpt-codex-bridge` returns **exactly ten** entries — the 8 modified + 2 untracked in the manifest, nothing more. There is no 015 bridge byte outside the manifest. The 11th path, `reports/sites-version-state.json`, is the requirement-8 state update. Nothing 015-related is missing.

**Minimality.** The five excluded modified paths (`skills/…/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css`) are pre-015 work in unrelated subtrees; none is imported by or referenced from the bridge. Excluding them is correct and necessary — sweeping them in would make a security-relevant commit carry unrelated frontend churn.

**Conclusion: the 11-path set is complete and minimal.** I derived it independently and arrived at the same set. Confirmed.

---

## Requirement 2 — Per-path 015 attribution (read hunk-by-hunk, not summarized)

| # | Path | Verdict |
| --- | --- | --- |
| 1 | `bridge/sandbox.py` | **015.** Pure addition: module docstring with the isolation-boundary statement, `DEFAULT_BWRAP_BIN`, `SANDBOX_MECHANISM_BY_PROVIDER`, `_HARNESS_STATE_DIRS`, `_CREDENTIAL_FILES_READ_ONLY`, `SandboxEnforcementError`, `SandboxLaunchPlan`, `_execute_probe`, `_which`, `_bwrap_unavailable_reason`, `_bwrap_argv`, `sandbox_launch_plan`. Pre-existing `validate_sandbox_mode` / `SandboxModeError` / `SANDBOX_MODES` untouched — verified as diff context, no `-`/`+` pair. |
| 2 | `bridge/claude_runner.py` | **015.** `command_for` now captures `validate_workspace`'s return and wraps argv via `plan.enforce_argv`; `run()` gains a `try/except SandboxEnforcementError` that writes a `status="failed"`, `needs_attention=True` report and returns `explicit_failure=FAILURE_SANDBOX_ENFORCEMENT` **before** `_popen`. Ordering verified: `validate_workspace` precedes `sandbox_launch_plan`. |
| 3 | `bridge/agy_runner.py` | **015.** Structurally identical to claude. LOW-2 revert confirmed by construction: `report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)` appears as **diff context**, byte-identical to baseline — a restore, not a re-order. |
| 4 | `bridge/codex_runner.py` | **015, exactly 6 added lines.** `FAILURE_SANDBOX_ENFORCEMENT` constant, its `FAILURE_CLASSES` member, and a 4-line `describe_failure` branch. No deletions. Codex argv construction untouched. |
| 5 | `bridge/config.py` | **015, exactly 2 added lines.** `sandbox_bwrap_bin: str = "bwrap"` field + `values.get("CODEX_SANDBOX_BWRAP_BIN", "bwrap")` in `from_env`. No deletions. |
| 6 | `README.md` | **015, doc-only.** 1 modified + 1 added line. The modified line corrects a genuinely stale pre-015 clause that attributed runtime isolation to "各 provider CLI 自己"; the added line states the bubblewrap boundary and its explicit non-goals (network/PID/IPC/UTS/seccomp/resource limits, "不是完整容器"). Prose only, no code, no unrelated README churn. |
| 7 | `tests/test_agy_runner.py` | **015.** `setUp` patches `bridge.sandbox._which` and `_execute_probe` so plan-building does not depend on host bwrap; adds an exact `expected_prefix` argv assertion and a temp `.gemini` home. Existing assertions preserved, now prefix-aware. |
| 8 | `tests/test_claude_router.py` | **015.** Same `setUp` pattern; argv assertions re-anchored on the `--` separator (`provider_argv = argv[argv.index("--")+1:]`) so provider argv is still asserted byte-exactly; adds `sandbox_bwrap_bin` argv[0] check and a `sandbox_mode` report assertion. The `--dangerously-skip-permissions` / `--channels` negative assertions were correctly widened to the **full** argv, which is strictly stronger. |
| 9 | `tests/test_sandbox_enforcement.py` | **015, new.** 606 lines, 28 tests across 5 classes. Read in full. |
| 10 | `tests/fixtures/sandbox_probe_tool.sh` | **015, new.** 57 lines POSIX `sh`. Deliberate and necessary — it probes what the *kernel* allowed rather than what a prompt claims. Canaries self-clean (`rm -f` for tmp/home); `.sbx-workspace-violation` is written only inside a `TemporaryDirectory` workspace. |
| 11 | `reports/sites-version-state.json` | **015, single-field.** Diff is literally `-1/+1`: `sandbox_enforcement_parity_015` `"not_started"` → `"closed_pass_with_nonblocking_findings"`. Verified: the key **pre-existed** at HEAD (no schema invented), the file parses as valid JSON with 60 keys, the value string matches the `needs_attention_semantics_013` precedent exactly, and the file has commit precedent in this task series (`b137523`, `ef32b18`, `3a99346`). |

**No unrelated or missing bytes detected in any of the 11 paths.**

---

## Requirement 3 — Fixture executable mode

On-disk mode is **`775`**, not `755`. Qoder's commit-readiness report states `775` and flags this as a correction to its own earlier draft (LOW-5) — accurate.

Does it matter, and is it represented?

- **It matters.** The fixture is invoked as `CLAUDE_BIN` / `AGY_BIN` and executed directly by `_popen`. Committed non-executable, every `RealBubblewrapEnforcementTests` test would fail with `PermissionError` on a functional-bwrap host — i.e. exactly the positive-enforcement evidence this task exists to produce would break.
- **It is represented correctly.** `core.fileMode=true` on this repo. Git records only the owner-execute bit, so both `775` and `755` normalize to blob mode `100755`. The commit is mode-correct either way; the `775` vs `755` distinction is cosmetic umask residue with **no commit impact**.

I could not run `git hash-object -w` to demonstrate this directly — the object database is on the read-only mount in my confinement — so this is derived from `core.fileMode=true` plus git's documented two-mode normalization, not measured. The verification instruction in Qoder's §9 (confirm `new file mode 100755` in `git diff --cached --stat` after staging) is the right check and should be performed.

**Recommendation retained:** after `git add`, confirm the fixture shows `new file mode 100755`.

---

## Requirement 4 — Qoder's isolated-candidate method, independently reproduced

I did not audit Qoder's candidate; I built my own from scratch and compared outcomes.

Method: `git archive HEAD | tar -x` → `pristine`; `cp -a` → `candidate`; then `cp -p` of **only** the 11 proposed paths into `candidate`.

| Property | Result |
| --- | --- |
| No `.git` in pristine or candidate | **Confirmed** — `git archive` emits no `.git`; grep for a `.git` entry returns nothing in both trees |
| No dependency on the dirty working tree | **Confirmed** — tree is built from `HEAD` + explicitly named files only |
| Exact delta | **Confirmed — exactly 11 entries.** 9 "differ" (8 bridge + state json) + 2 "only in candidate" (new test + fixture). Nothing else. |
| Byte parity with working tree | **Confirmed** — all 11 sha256-identical |
| Fixture mode preserved | **Confirmed** — `775` in candidate via `cp -p` |
| No history mutation | **Confirmed** — no `commit-tree`, no staging, HEAD unchanged |
| Candidate outside `/tmp` | **Not reproducible by me** — see below |

Gates run inside my candidate: `python3 -m unittest tests.test_sandbox_enforcement` → **Ran 28, OK (skipped=7)**; `python3 -m unittest discover -s tests -t .` → **Ran 206, OK (skipped=7)**; `python3 -m compileall -q bridge tests` → clean.

**On candidate location.** Qoder's claim is that the candidate must sit outside `/tmp`. I was forced to violate that constraint (read-only `/home`), and the candidate worked correctly anyway — 28/206 green, exact delta, byte parity. That is expected: the `--tmpfs /tmp` shadowing only matters when a *sandboxed child* must read a `/tmp`-resident non-workspace file, which is the fixture-execution path exercised only by `RealBubblewrapEnforcementTests` — the class that skips here. So Qoder's constraint is **correct and worth keeping for host-context runs**, and my `/tmp`-located candidate is sufficient for everything I could actually execute. Qoder's method is sound; I reproduced its verifiable core exactly.

---

## Requirement 5 — Test gates re-run, with skip attribution

| Gate | Command | My result (nested sandbox) | Qoder (host) |
| --- | --- | --- | --- |
| Focused 015 | `python3 -m unittest tests.test_sandbox_enforcement` | **Ran 28 — OK (skipped=7)** | Ran 28 — OK (0 skipped) |
| Full unittest | `python3 -m unittest discover -s tests -t .` | **Ran 206 — OK (skipped=7)** | Ran 206 — OK |
| Candidate focused | same, in isolated candidate | **Ran 28 — OK (skipped=7)** | Ran 28 — OK |
| Candidate full | same, in isolated candidate | **Ran 206 — OK (skipped=7)** | Ran 206 — OK |
| compileall | `compileall -q bridge tests` | **clean** | clean |
| Diff hygiene | `git diff --check` | **exit 0, clean** | exit 0 |

**Skip attribution, stated precisely as required.** The 7 skips are **nested-bwrap environment artifacts, not host defects.** Evidence, not assertion:

- The host probe `bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --die-with-parent -- /bin/true` exits **1** here with `"No permissions to create new namespace"`, and `/proc/self/uid_map` shows `1000 0 1` — I am already inside a user namespace that forbids nesting.
- The 7 skips are exactly the 6 `RealBubblewrapEnforcementTests` + `test_functional_bwrap_still_builds_real_enforcement_plans`, and the printed reason is the verbatim production probe string: `functional bwrap enforcement unavailable: sandbox wrapper self-test failed with exit code 1`.
- The gate uses `_bwrap_unavailable_reason` — the **same** module-level function `sandbox_launch_plan` calls on the production path — not a reimplementation.

**Zero failures, zero errors, in both the main tree and the isolated candidate.** My counts differ from Qoder's only in the skip column, which is exactly the expected difference between a functional-bwrap host and a nested sandbox. The two result sets are consistent, and their difference is itself evidence the gate discriminates correctly.

---

## Requirement 6 — No-regression re-verification (executed live, not read)

**Codex argv — PASS, no regression.** Live `CodexRunner.command_for(job, report_path)`:

```
read-only            ['codex','exec','--sandbox','read-only','-C']          bwrap:False  --ro-bind:False
workspace-write      ['codex','exec','--sandbox','workspace-write','-C']    bwrap:False  --ro-bind:False
danger-full-access   ['codex','exec','--sandbox','danger-full-access','-C'] bwrap:False  --ro-bind:False
```

No `bwrap`, no `--ro-bind` anywhere in codex argv, in any mode. `sandbox_launch_plan("codex", …)` returns `prefix_len=0` with mechanism `provider CLI --sandbox flag (native landlock/seccomp)` for all three modes.

**All 9 provider×mode resolutions — PASS.**

```
codex  read-only/workspace-write/danger-full-access  OK prefix_len=0
claude read-only        FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
claude workspace-write  FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
claude danger-full-access  OK prefix_len=0  mech=none (unrestricted by definition)
agy    read-only        FAIL-CLOSED: (same)
agy    workspace-write  FAIL-CLOSED: (same)
agy    danger-full-access  OK prefix_len=0  mech=none (unrestricted by definition)
```

In an environment that cannot enforce, the code refuses to launch rather than running unsandboxed while reporting a sandboxed mode. That is precisely the 014 PARTIAL defect, and it does not regress under adverse conditions.

**013 failure/needs_attention semantics — PASS.** Live `FAILURE_CLASSES` = 7 members: `{none, timeout, exit_nonzero, invalid_report, needs_attention, agent_reported_failure, sandbox_enforcement}`. `sandbox_enforcement` is distinct, does not collide with `needs_attention` or `agent_reported_failure`, and `test_sandbox_enforcement_failure_class_is_distinct` asserts all five `describe_failure` strings are pairwise unique. The fail-closed path sets `status="failed"`, `needs_attention=True`, `explicit_failure=FAILURE_SANDBOX_ENFORCEMENT`, and `test_report_contract_and_needs_attention_semantics_survive_fail_closed` additionally asserts the bot token does not leak into the summary. The codex mode-mismatch check is untouched.

**014 workspace/publication ceilings — PASS, ordering verified.** With a non-allowlisted `/etc` workspace, **all three** runners raise `ConfigurationError: job workspace is not in CODEX_ALLOWED_WORKSPACES` — not `SandboxEnforcementError`. The 014 ceiling still gates **first**; 015's new failure path does not leak past it. `danger-full-access` yields `prefix_len=0` and acquires no publication privilege. No publication code path appears anywhere in the diff.

**MEDIUM-2 (env inheritance) — still open, verified unchanged.** `grep -c clearenv bridge/sandbox.py` → **0**; no `--unshare-*`, `--new-session`, or `--hostname` flags either. `claude_environment()` strips `TELEGRAM_BOT_TOKEN`, `MCP_BEARER_TOKEN`, `MEETING_API_TOKEN`; `agy_environment()` strips those plus `GEMINI_API_KEY`, `GOOGLE_API_KEY`. Everything else in `os.environ` is inherited by the sandboxed child. Confirmed as described.

---

## Requirement 7 — Is MEDIUM-2 still non-blocking for commit?

**Yes. Non-blocking. I reach this independently and agree with the re-review.**

1. **It is not a regression.** Denylist-plus-inherit is baseline behaviour predating 015 (both helpers exist at HEAD unchanged). Committing 015 leaves environment handling exactly where it already is while strictly improving filesystem enforcement. Blocking here would hold a security improvement hostage to an unrelated pre-existing weakness.
2. **Blast radius genuinely shrinks under 015.** Environment inheritance is most dangerous combined with unrestricted filesystem write. For read-only/workspace-write, `/` is now read-only and OAuth credential files are re-bound read-only *over* their writable state dirs — so a leaked variable no longer converts into arbitrary filesystem mutation or credential replacement.
3. **The failure asymmetry favours waiting.** A too-broad denylist exposes information to a provider CLI the operator already chose to run and already hands credentials to. A too-narrow `--clearenv` allowlist breaks authentication in production for **every subsequent job**. Guessing an allowlist against an unread `0600` `.env` is exactly the speculative change the task brief forbids.
4. **The residual is durably recorded in code**, not only in a report: `bridge/sandbox.py`'s docstring states the child "inherits the caller's environment minus the adapter secrets the runner environment helpers strip." It survives report archival.

I did not read `.env` beyond the single `CODEX_ALLOWED_WORKSPACES` line needed for Requirement 8. No credential was read or output. This is a structural judgement, not a measured leak.

**Follow-up protocol (post-commit, tracked):** enumerate variable *names* from the worker's live environment → run staged canary jobs through both providers including a token-refresh path → enable `--clearenv` with a proven allowlist. `--clearenv` is confirmed present in bwrap 0.9.0, so the future pass is blocked only on evidence, not mechanism.

---

## Requirement 8 — The `/tmp` operational note: test/candidate-location behaviour, not a production issue

Qoder claims the `--tmpfs /tmp` shadowing is harness-location behaviour only. **I verified this directly rather than accepting it, and it holds.** Two independent legs:

**Leg 1 — argv ordering makes `/tmp` workspaces work.** Inspecting the constructed prefix for a hypothetical `/tmp`-resident workspace:

```
workspace = /tmp/some-workspace         → --tmpfs idx=8, workspace bind idx=23  (bind AFTER tmpfs)
workspace = /home/.../e500-codex-smoke  → --tmpfs idx=8, workspace bind idx=23  (bind AFTER tmpfs)
```

The workspace bind is emitted **last, after `--tmpfs /tmp`**, with an explicit source comment saying so. bwrap applies operations in order, so a `/tmp`-resident workspace is bound *over* the fresh tmpfs and remains fully accessible. The shadowing therefore affects only `/tmp`-resident files that are **not** the workspace.

**Leg 2 — no `/tmp` path is in the allowlist.** Live `Settings`:

```
allowed workspaces: ['/home/b827262/project/e500-codex-smoke/ai-meeting-room',
                     '/home/b827262/project/e500-codex-smoke/gpt-codex-bridge']
```

Neither is under `/tmp`. So the shadowed-non-workspace case cannot arise for any dispatchable production job.

**Decision: test/candidate-location behaviour, not a production correctness or security issue.** In fact the shadowing is a deliberate *security property* — a private `/tmp` prevents the sandboxed child from reading or tampering with host temp files, which is precisely what `TMP_HOST_LEAK=0` asserts in the fixture. Where it bites is only the test harness: a fixture or candidate physically under `/tmp` becomes invisible to the sandboxed child, which would break `RealBubblewrapEnforcementTests` on a functional-bwrap host.

**Operational note to carry forward (not a code change, not a blocker):** run the host-context real-bwrap leg from a checkout **outside `/tmp`**. If a `/tmp` workspace is ever added to the allowlist, re-verify leg 1 — the ordering is correct today but is load-bearing and comment-protected rather than test-protected for that specific case.

---

## Requirement 9 — Evidence-report inclusion policy

**The 11-path code/state commit is sufficient on its own.** It is self-contained: source, tests, fixture, and the state field that records the outcome. Nothing in it references or requires the evidence reports to build, run, or be understood — I verified this by building an isolated candidate containing only those 11 paths and running the full suite green (206 OK) with no missing-file errors.

**Evidence should be a separate docs commit, not the same commit, and not left uncommitted.** Reasoning:

- **Keep the security-relevant diff auditable.** A future reviewer bisecting or auditing sandbox enforcement should see 11 focused paths, not ~14 files of Markdown and prompt transcripts diluting the signal.
- **The evidence is not yet complete.** The most valuable artifact — the host-context `RealBubblewrapEnforcementTests` green run under the worker's actual runtime — does not exist yet, because neither sandboxed reviewer could produce it and the worker still runs pre-015 code. A docs commit made *after* that run carries the enforcement proof; one made now would be missing its most important piece.
- **Leaving it uncommitted is the worst option.** These reports are the audit trail for a security change. Losing them to a stray `git clean` would leave a sandbox-enforcement commit with no recorded review history.

I concur with Qoder's classification, with one sharpening: "optional" understates it. The evidence commit is **deferred and expected**, not discretionary.

---

## Requirement 10 — Publication check and artifacts that must stay excluded

**No staging, commit, push, Sites operation, or external publication occurred.** Verified:

- `git rev-parse HEAD` = `b1375230a0027aa00b2842a1f6fb669846a67d75` — unchanged at start and end.
- `git reflog -n 3` — top entry is still `b137523 commit: bridge: add durable workspace alias routing`. No commit, amend, reset, or rebase.
- `git diff --cached --name-only | wc -l` = **0** — index is empty, nothing staged.
- `git stash list` — one pre-existing entry (`safety: preserve local workspace before origin integration`); nothing new.
- Working tree: 126 porcelain entries, all pre-existing or evidence artifacts. No `gh` invocation, no Sites save/deploy/publish, no credential output.
- `git diff --check` exit 0.

**Artifacts that must stay excluded from the commit:**

| Class | Items |
| --- | --- |
| Unrelated modified (pre-015) | `skills/gpt-agy-claude-development-loop/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css` |
| Zero-byte scratch strays | `eslint`, `node` (both 0 bytes at repo root — verified; must never be committed) |
| Untracked dirs | `.claude/`, `agents/`, `ai-meeting-room/`, `scripts/`, `transfer/`, `web/app/`, `web/main/`, `web/e500-control-plane*/`, `sites-project@0.1.0`, `vinext`, `skills/e500-chatgpt-sites-sync/`, `skills/multi-agent-git-programming/` |
| Unrelated reports | all other untracked `reports/*` (011/012/V19/V20 evidence, PNGs, `job-*.json`, `sites-deltas/`, `web-compare/`, `.sites_prompt_latest.txt`) |
| Runtime state | `gpt-codex-bridge/.env` (mode `0600`), `__pycache__/`, `.pytest_cache/` |
| Sandbox run artifacts | `find` for `sbx-acceptance-*` and `.sbx-*` across the repo → **none exist** (re-verified after all my test runs) |
| My scratch | `/tmp/e500-015-claude-verify` — **deleted by me**. Qoder's `/home/b827262/e500-015-*` trees are outside the repo and safe to delete. |

Note: `web/sample/.gitignore` and `web/sample/package-lock.json` are untracked and unrelated to 015 — also excluded.

---

## Verdict on Qoder's commit-readiness claims

| Claim | Independently verified | Verdict |
| --- | --- | --- |
| Exact 11-path candidate, complete and minimal | Re-derived from repo; `git status --porcelain gpt-codex-bridge` = exactly 10 + state json | **CONFIRMED** |
| All 8 modified paths are 015-scoped | Read hunk-by-hunk; codex=6 lines, config=2 lines, README prose-only | **CONFIRMED** |
| LOW-2 reverted to baseline | `mkdir(...mode=0o700)` is diff context, not a `-`/`+` pair | **CONFIRMED** |
| State json is a single-field update, key pre-existed | Diff is `-1/+1`; key present at HEAD; value matches 013 precedent; valid JSON | **CONFIRMED** |
| Fixture mode `775`, commits as `100755` | `stat` = 775; `core.fileMode=true`; git two-mode normalization | **CONFIRMED** (derived, not measured — see Req 3) |
| Isolated candidate: archive HEAD, no `.git`, exact delta, byte parity | Built my own; delta = exactly 11 entries; all 11 sha256-match | **CONFIRMED** |
| Candidate outside `/tmp` | Could not reproduce (read-only `/home`); built under `/tmp`, gates still green | **CONSTRAINT VALID, NOT REPRODUCED** |
| Host gates 28/206/229+74, 0 skips | Cannot reproduce host-side | **CONSISTENT / ENVIRONMENT-BLOCKED** |
| Codex argv no-regression | Live, all 3 modes, no bwrap/`--ro-bind` | **CONFIRMED** |
| 013 semantics preserved, 7 distinct failure classes | Live `FAILURE_CLASSES`, distinctness test read | **CONFIRMED** |
| 014 ceilings gate first | Live `/etc` probe: `ConfigurationError` for all 3 runners | **CONFIRMED** |
| MEDIUM-2 open, non-blocking | `grep -c clearenv` = 0; env helpers read; reasoning re-derived | **CONFIRMED, and I endorse the decision** |
| `/tmp` note is harness-only | Verified by argv ordering + live allowlist | **CONFIRMED, with sharpening** |
| No commit/push/publication | HEAD, reflog, index, stash all verified | **CONFIRMED** |

Qoder's report was accurate everywhere I could check it, including its self-correction from `755` to `775`. I found **no overstated claim and no missed path**.

---

## FINAL_VERDICT

**PASS_WITH_NONBLOCKING_FINDINGS**

The 11-path candidate is complete, minimal, and correctly attributed. I derived the set independently and reached the same answer; built my own isolated candidate and got exactly the same 11-entry delta with full byte parity; and re-executed every no-regression claim live rather than reading it. All suites are green in both the main tree and the isolated candidate, with the 7 skips positively attributed to nested-namespace unavailability — evidenced by a failing host bwrap nesting probe and a `1000 0 1` uid_map — rather than to any host or code defect.

Two items remain open, neither blocking:

- **MEDIUM-2 (environment inheritance)** — pre-existing, non-regressing, blast radius reduced by 015, documented in `bridge/sandbox.py`, and fixable only with evidence this session could not safely gather. Correctly deferred.
- **Host-context runtime enforcement proof** — ENVIRONMENT-BLOCKED from inside a nested sandbox, as it was for both prior reviews. Qoder's host-context green run substantially answers it, but it must be recaptured under the worker's runtime after the service is restarted on 015 code.

### READY_TO_COMMIT — **YES**

**Approved path list (exact, 11 paths, no corrections):**

```
gpt-codex-bridge/README.md
gpt-codex-bridge/bridge/agy_runner.py
gpt-codex-bridge/bridge/claude_runner.py
gpt-codex-bridge/bridge/codex_runner.py
gpt-codex-bridge/bridge/config.py
gpt-codex-bridge/bridge/sandbox.py
gpt-codex-bridge/tests/test_agy_runner.py
gpt-codex-bridge/tests/test_claude_router.py
gpt-codex-bridge/tests/test_sandbox_enforcement.py
gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh
reports/sites-version-state.json
```

**Recommended commit subject:**

```
bridge: add fail-closed bwrap sandbox enforcement for claude/agy
```

**Evidence reports: separate docs commit**, made *after* the host-context `RealBubblewrapEnforcementTests` run so the docs commit carries the enforcement proof. Not the same commit (keeps the security diff auditable); not left uncommitted (they are the audit trail for a security change).

**Post-commit conditions (none blocking):**
1. After staging, confirm `git diff --cached --stat` shows exactly these 11 paths and `new file mode 100755` for the fixture.
2. Restart `gpt-codex-worker.service` — it currently runs pre-015 code.
3. Capture the host-context `RealBubblewrapEnforcementTests` green run from a checkout **outside `/tmp`**, under the worker's runtime context.
4. Open MEDIUM-2 (`--clearenv` + proven allowlist) as a tracked follow-up using the canary-job protocol.
5. Land the evidence reports as the separate docs commit.

Nothing was committed, staged, pushed, or published by this review.

CLAUDE_OPUS5_MEDIUM_SANDBOX_PARITY_015_COMMIT_READINESS_COMPLETE
