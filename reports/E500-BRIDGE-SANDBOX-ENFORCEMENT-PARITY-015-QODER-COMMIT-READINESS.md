# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Qoder Commit-Readiness Report

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 COMMIT READINESS |
| Implementer | Qoder / GLM-5.3-Flash xhigh |
| Date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke` |
| Bridge | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` (verified unchanged at session start and at report time) |
| Inputs | `E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-REVIEW.md`, `-QODER-REMEDIATION.md`, `-CLAUDE-REREVIEW.md` (plus prior readiness drafts in-tree, re-verified rather than trusted) |
| Session context | Fresh re-verification session. This report supersedes the prior draft of this file. Host-context session: bwrap 0.9.0 functional and nesting-capable here (`--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --die-with-parent -- /bin/true` exits 0), so the real-bwrap test leg executed for real. |
| Scope | Commit-candidate preparation only. NO add/stage/commit/push/amend/reset/rebase/clean/stash/checkout/restore/history rewrite. No Sites save/deploy/publish. No credentials read or output. No source modified (no blocker found). |

## 1. Proposed commit path list (exact — 11 paths)

Derived from `git diff HEAD` + the three reports, then byte-verified by isolated-candidate delta diff (Section 4). All 8 modified paths read hunk-by-hunk this session; every hunk is 015-scoped.

### Modified (8)

| # | Path | Class | Notes |
| --- | --- | --- | --- |
| 1 | `gpt-codex-bridge/bridge/sandbox.py` | code | Central `sandbox_launch_plan` / `SandboxEnforcementError` / `SandboxLaunchPlan` / bwrap argv construction; boundary docstring (MEDIUM-3 fix). Pure addition; pre-existing `validate_sandbox_mode` untouched. |
| 2 | `gpt-codex-bridge/bridge/claude_runner.py` | code | bwrap plan in `command_for` (after `validate_workspace`); fail-closed `SandboxEnforcementError` → failed/needs_attention before `_popen`. |
| 3 | `gpt-codex-bridge/bridge/agy_runner.py` | code | Same shape as claude. LOW-2 `mkdir` revert confirmed: `report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)` appears as diff context, byte-identical to baseline. |
| 4 | `gpt-codex-bridge/bridge/codex_runner.py` | code | Exactly 6 added lines: `FAILURE_SANDBOX_ENFORCEMENT` constant, `FAILURE_CLASSES` member, `describe_failure` branch. Codex argv path untouched. |
| 5 | `gpt-codex-bridge/bridge/config.py` | code | Exactly 2 added lines: `sandbox_bwrap_bin` field + `CODEX_SANDBOX_BWRAP_BIN` env read. |
| 6 | `gpt-codex-bridge/README.md` | doc | 1 modified + 1 added prose line: stale pre-015 clause corrected (runtime isolation attributed to codex `--sandbox` / claude+agy bubblewrap) + explicit wrapper non-goals (MEDIUM-3 fix). |
| 7 | `gpt-codex-bridge/tests/test_agy_runner.py` | test | `setUp` probe mocks (`_which`, `_execute_probe`), exact-argv `expected_prefix`, temp `.gemini` home. |
| 8 | `gpt-codex-bridge/tests/test_claude_router.py` | test | Same `setUp` mock pattern + wrapper argv coverage + `sandbox_mode` report assertion. |

### New (2, currently untracked)

| # | Path | Class | Notes |
| --- | --- | --- | --- |
| 9 | `gpt-codex-bridge/tests/test_sandbox_enforcement.py` | test | 606 lines, 28 tests / 5 classes (`SandboxLaunchPlanMappingTests`, `SandboxFailClosedTests`, `RealBwrapGateTests`, `RealBubblewrapEnforcementTests`, `LoopbackBindEnvironmentTests`). |
| 10 | `gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh` | test fixture (deliberate) | 57 lines, POSIX `sh`, self-cleaning canaries. **On-disk mode `775`** (corrects the prior draft's `755`); with `core.fileMode=true` git records only the owner-x bit, so the committed mode will be `100755` either way — no commit impact. |

### State (1, tracked)

| # | Path | Class | Notes |
| --- | --- | --- | --- |
| 11 | `reports/sites-version-state.json` | evidence/state | Requirement 8: the file already tracks 015 (`sandbox_enforcement_parity_015` key pre-existed with `not_started`, alongside 013/014 precedent fields; `b137523` touched this file for 014). Working tree updates exactly that one field to `closed_pass_with_nonblocking_findings` — diff is literally `-1/+1`, no schema invented. Accurate at commit time; MEDIUM-2 residual remains a tracked follow-up. |

Evidence reports (`-CLAUDE-REVIEW/-QODER-REMEDIATION/-CLAUDE-REREVIEW.md` + `.prompt.txt`) are classified evidence but **not** in this commit: recommended as a separate docs commit after the post-commit host-context bwrap run, per the prior readiness review's sequencing rationale (keeps the security-relevant code diff clean; lets the docs commit carry the enforcement proof neither sandboxed reviewer could produce).

## 2. Excluded artifacts (verified NOT part of 015)

- Unrelated modified (pre-015, untouched): `skills/gpt-agy-claude-development-loop/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css`.
- Untracked directories: `.claude/`, `agents/`, `ai-meeting-room/`, `scripts/`, `transfer/`, `web/app/`, `web/main/`, `web/e500-control-plane*/`, `sites-project@0.1.0`, `vinext`.
- Root strays: `eslint`, `node` — untracked **0-byte** scratch files; must not be committed.
- All other untracked `reports/*` (011/012/V19/V20 evidence, PNGs, `job-*.json`, `sites-deltas/`, `web-compare/`, `.sites_prompt_latest.txt`) — runtime/scratch, unrelated.
- Runtime acceptance files: repo-wide `find -name "sbx-acceptance-*"` → **none exist** (re-verified this session). Fixture canaries (`.sbx-tmp-canary`, `.sbx-home-violation`, `.sbx-host-canary-*`) are self-cleaning; `.sbx-workspace-violation` is written only inside a test `TemporaryDirectory`.
- Gitignored runtime state (never committable): `gpt-codex-bridge/.env` (mode `0600`, never read), `__pycache__/`, `.pytest_cache/`.
- Prior-session scratch trees outside the repo: `/home/b827262/e500-015-candidate`, `/home/b827262/e500-015-head-pristine`, and this session's `/home/b827262/e500-015-v2-pristine`, `/home/b827262/e500-015-v2-candidate`, `/tmp/e500-015-probe/` — safe to delete.

## 3. No unrelated pre-015 work swept in (Requirement 3)

`git status --porcelain gpt-codex-bridge` contains **exactly** the 10 bridge paths of the manifest and nothing else. The 11th path is the one-field state update in `reports/sites-version-state.json` (requirement 8). Full working-tree diffs of all 8 modified paths were read this session: no cosmetic or unrelated churn remains (LOW-2 reverted to baseline bytes; codex diff is 6 lines, all failure-class related; README diff is prose only).

## 4. Isolated-gate construction (Requirement 6)

Fresh candidate built this session, independent of any prior attempt:

- `git archive HEAD | tar -x` → `/home/b827262/e500-015-v2-pristine`; tree-copied → `/home/b827262/e500-015-v2-candidate`. Candidate has **no `.git`** (verified) and no dependency on the dirty working tree. No `git commit-tree`, no history mutation.
- Copied in ONLY the 11 proposed paths (`cp -p`, preserving modes). First copy pass accidentally left a stray `README.md` inside `bridge/`; removed and delta re-verified.
- `diff -rq pristine candidate` → **exactly the 11 paths**: 9 differ (8 bridge + state json), 2 only-in-candidate (new test + fixture). Nothing else.
- All 11 files sha256-identical to the working tree. Fixture mode in candidate: `775`.
- Candidate location is outside `/tmp` (under `/home/b827262`) — a candidate under `/tmp` would be shadowed by the wrapper's `--tmpfs /tmp` for anything that is not the workspace itself (the workspace bind is deliberately emitted last, so `/tmp` workspaces still work; only `/tmp`-resident *non-workspace* files — like a probe fixture — would be hidden). Test-harness-location behavior, not a production issue: the allowlist contains no `/tmp` path.
- Gates run in the candidate: import/collection sweep (**16/16 modules import, 0 collection errors**), `compileall -q bridge adapters tests` clean, plus the three suites below.

## 5. Test gates — exact counts

| Gate | Command (from `gpt-codex-bridge/`) | Main tree | Isolated candidate |
| --- | --- | --- | --- |
| Diff hygiene | `git diff --check` (repo root) | exit 0, clean | n/a |
| Focused 015 | `python3 -m unittest tests.test_sandbox_enforcement` | **Ran 28 — OK** (0 failed, 0 skipped; real-bwrap leg executed) | **Ran 28 — OK** |
| Full unittest | `python3 -m unittest discover -s tests -t .` | **Ran 206 — OK** | **Ran 206 — OK** |
| Full pytest | `python3 -m pytest -q` | **229 passed, 74 subtests passed, 0 failed** | **229 passed, 74 subtests passed** |
| Import/collection | compileall + 16-module import sweep | n/a | **16/16 OK, 0 collection errors** |

These host-context counts match the remediation report exactly. Nested-sandbox reviewer counts (28 OK/7 skipped, 206 OK, 222+7) differ only in the skip column — the functional gate skipping honestly where namespaces are unavailable, by design.

One transient probe-side error during this session was in my own scratch verification script (non-callable probe stub, wrong report key, wrong outcome attribute), not in the candidate; corrected, then all probes passed.

## 6. No-regression verification (Requirement 7 — 23/23 probes PASS against candidate code)

- **Codex argv no-regression — PASS.** `CodexRunner.command_for` → `['codex','exec','--sandbox',<mode>,'-C',…]` for all three modes; no `bwrap`, no `--ro-bind` anywhere in argv; `sandbox_launch_plan("codex", …)` → `prefix_len=0`, mechanism `provider CLI --sandbox flag (native landlock/seccomp)` in all modes.
- **All 9 provider×mode plan resolutions — PASS.** codex: prefix_len=0 all modes. claude/agy: read-only & workspace-write build a 21-token bwrap prefix with mechanism `bubblewrap filesystem isolation`; danger-full-access → `prefix_len=0`, mechanism `none (unrestricted by definition)` — acquires no publication privilege.
- **013 failure/needs_attention semantics — PASS, end-to-end.** `FAILURE_CLASSES` has exactly 7 members with `sandbox_enforcement` distinct. Live `ClaudeRunner.run()` with a present-but-unusable fake `bwrap` (exit 1) through the real production path: process **never launched** (popen factory would have raised), report written `status="failed"`, `needs_attention=True`, `outcome.failure_reason == "sandbox_enforcement"`; `describe_failure` → `"Claude failed before launch: provider 'claude' cannot enforce sandbox mode 'read-only': sandbox wrapper self-test failed with exit code 1"`.
- **014 workspace/publication ceilings — PASS, ordering verified.** Non-allowlisted `/etc` workspace raises `ConfigurationError: job workspace is not in CODEX_ALLOWED_WORKSPACES` for codex, claude, and agy — the 014 ceiling gates **first**, no `SandboxEnforcementError` leakage past it. No publication path in the diff.

## 7. MEDIUM-2 disposition (remaining finding)

**Open residual, non-blocking — endorsed by the independent re-review and readiness review.** The sandboxed Claude/AGY child inherits the host environment minus the secrets the runner helpers strip (`TELEGRAM_BOT_TOKEN`, `MCP_BEARER_TOKEN`, `MEETING_API_TOKEN`, + `GEMINI_API_KEY`/`GOOGLE_API_KEY` for agy); the bwrap argv has no `--clearenv`. Refusing a guessed allowlist is the correct call: the failure mode (dropping a required runtime variable) breaks production auth for every subsequent job, the condition pre-dates 015 and is not a regression, and 015 shrinks its blast radius (filesystem read-only outside carve-outs; OAuth credential files re-bound read-only). Documented durably in `bridge/sandbox.py`'s docstring. Follow-up protocol: enumerate variable *names* from the worker's live environment → staged canary jobs through both providers incl. a token-refresh path → enable `--clearenv` with a proven allowlist (`--clearenv` confirmed present in bwrap 0.9.0). LOW-1 / LOW-3 / LOW-4 remain NO-ACTION with recorded rationale; LOW-5 (report wording `755`→`775`) is corrected in this report.

## 8. Git state (verified at report time)

- HEAD `b1375230a0027aa00b2842a1f6fb669846a67d75` — unchanged; reflog top is the baseline commit; no commit/amend/reset/rebase this session.
- Branch `web/codex-sample-standard`; ahead-count vs `telegram-ai-code` = 5, entirely the pre-existing baseline commit; nothing pushed.
- `git diff --check` exit 0. Stash list: 1 pre-existing entry (`safety/pre-origin-integration`), nothing new.
- Working tree: 126 porcelain entries (grew from 123 only by the prior readiness drafts + prompt files). Nothing staged. No push, no Sites deploy, no external publication, no credential output.

## 9. Commit instructions for the user (not executed here)

```
git add gpt-codex-bridge/README.md gpt-codex-bridge/bridge/sandbox.py \
  gpt-codex-bridge/bridge/claude_runner.py gpt-codex-bridge/bridge/agy_runner.py \
  gpt-codex-bridge/bridge/codex_runner.py gpt-codex-bridge/bridge/config.py \
  gpt-codex-bridge/tests/test_agy_runner.py gpt-codex-bridge/tests/test_claude_router.py \
  gpt-codex-bridge/tests/test_sandbox_enforcement.py \
  gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh \
  reports/sites-version-state.json
```

After staging, confirm `git diff --cached --stat` shows exactly the 11 paths and `new file mode 100755` for the fixture.

Recommended commit subject:

```
bridge: add fail-closed bwrap sandbox enforcement for claude/agy
```

Post-commit conditions (none blocking): restart `gpt-codex-worker.service` (it runs pre-015 code); capture the host-context `RealBubblewrapEnforcementTests` green run under the worker's actual runtime context as durable enforcement evidence; open MEDIUM-2 as a tracked follow-up; land the evidence reports as a separate docs commit.

## Verdict

All required commit-readiness items completed in a fresh session: exact 11-path manifest re-derived from the HEAD diff and byte-verified via isolated-candidate delta; no unrelated work swept in; `git diff --check` clean; all gates green in both the main tree and the isolated candidate (28 / 206 / 229+74, plus 16/16 import sweep); 23/23 no-regression probes pass against candidate code (Codex argv, 9-combo plan resolution, 013 fail-closed semantics end-to-end, 014 ceiling ordering); no `sbx-acceptance-*` artifacts exist; 015 state field updated per requirement 8. The only open item is the documented, re-review-endorsed MEDIUM-2 residual.

**READY_TO_COMMIT: YES** (commit itself deliberately not performed).

QODER_GLM53_FLASH_XHIGH_SANDBOX_PARITY_015_COMMIT_READINESS_READY
