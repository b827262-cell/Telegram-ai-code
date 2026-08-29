# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Qoder Commit-Readiness RERUN Report

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 COMMIT READINESS RERUN |
| Implementer | Qoder / GLM-5.3-Flash xhigh |
| Date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke` |
| Bridge | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` (verified unchanged at start, mid-session, and report time; reflog top is the baseline commit) |
| Inputs | Current worktree bytes plus all prior 015 reports (`-CLAUDE-REVIEW`, `-QODER-REMEDIATION`, `-CLAUDE-REREVIEW`, `-CLAUDE-COMMIT-READINESS`, `-QODER-COMMIT-READINESS`). Prior verdicts were NOT trusted; every item below was independently re-derived this session. |
| Host | Linux 7.0.0-29-generic x64, Python 3.12.3, bubblewrap 0.9.0 functional (real-bwrap test leg executed for real) |
| Scope | Commit-candidate preparation only. NO add/stage/commit/push/amend/reset/rebase/clean/stash/checkout/restore. No Sites save/deploy/publish. No credentials read or output. No source modified (no new blocker found). |

## 1. Independently derived commit scope — exact minimal 015 manifest (11 paths)

Re-derived from `git diff HEAD` hunk-by-hunk plus the untracked files, then byte-verified by isolated-candidate delta diff (Section 4). Matches the prior readiness manifest exactly.

### Modified (8, tracked)

| # | Path | 015 content verified this session |
| --- | --- | --- |
| 1 | `gpt-codex-bridge/bridge/sandbox.py` | Full diff read: docstring isolation boundary (MEDIUM-3), `SandboxEnforcementError`, `SandboxLaunchPlan`, `_bwrap_unavailable_reason`/`_bwrap_argv`/`sandbox_launch_plan`, mechanism map, carve-out tables. Pre-existing `validate_sandbox_mode` untouched. |
| 2 | `gpt-codex-bridge/bridge/claude_runner.py` | Full diff read: bwrap plan in `command_for` after `validate_workspace`; fail-closed `SandboxEnforcementError` → failed/needs_attention report before `_popen`. |
| 3 | `gpt-codex-bridge/bridge/agy_runner.py` | Same shape as claude. LOW-2 revert confirmed against baseline bytes: `git show HEAD:...agy_runner.py` line 168 has identical `report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)`; the line appears in the diff only as context. |
| 4 | `gpt-codex-bridge/bridge/codex_runner.py` | Exactly 6 added / 0 removed lines: `FAILURE_SANDBOX_ENFORCEMENT`, `FAILURE_CLASSES` member, 4-line `describe_failure` branch. Codex argv path untouched. |
| 5 | `gpt-codex-bridge/bridge/config.py` | Exactly 2 added lines: `sandbox_bwrap_bin` field + `CODEX_SANDBOX_BWRAP_BIN` env read. |
| 6 | `gpt-codex-bridge/README.md` | 1 modified + 1 added prose line: runtime isolation attributed to codex `--sandbox` / claude+agy bubblewrap, wrapper non-goals (MEDIUM-3). |
| 7 | `gpt-codex-bridge/tests/test_agy_runner.py` | Full diff read: setUp probe mocks, exact-argv `expected_prefix`, temp `.gemini` home. 015-only. |
| 8 | `gpt-codex-bridge/tests/test_claude_router.py` | Full diff read: same setUp pattern + wrapper argv coverage + `sandbox_mode` report assertion. 015-only. |

### New (2, untracked)

| # | Path | Verified |
| --- | --- | --- |
| 9 | `gpt-codex-bridge/tests/test_sandbox_enforcement.py` | 606 lines read in full: 28 tests / 5 classes, all 015-scoped. |
| 10 | `gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh` | 57 lines read in full; POSIX sh, self-cleaning canaries. On-disk mode `775` (owner-x set → git records `100755`). |

### State (1, tracked)

| # | Path | Verified |
| --- | --- | --- |
| 11 | `reports/sites-version-state.json` | Diff is exactly `-1/+1` on the pre-existing `sandbox_enforcement_parity_015` field: `not_started` → `closed_pass_with_nonblocking_findings`. No schema invention. |

Evidence reports and `.prompt.txt` files remain excluded from the code commit (separate docs commit, per prior sequencing rationale — unchanged).

## 2. No unrelated pre-015 bytes swept in

- `git status --porcelain gpt-codex-bridge` contains exactly the 10 bridge paths of the manifest (8 M + 2 ??) and nothing else — re-verified at report time.
- Excluded modified files re-checked for 015 leakage: `skills/gpt-agy-claude-development-loop/SKILL.md` (git-finalize-sync docs) and `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css` (production web control plane work). Grep of their full diffs for `sandbox|bwrap|015|enforcement` matched only an incidental `Sandbox` UI label in the web sample and a CSS letter-spacing value — no 015 content.

## 3. Diff hygiene and git state

- `git diff --check` → exit 0, clean.
- Index: `git diff --cached --name-only` → 0 paths (empty throughout; nothing staged).
- HEAD `b137523...` unchanged; reflog top is the baseline commit; no new reflog entries.
- Branch `web/codex-sample-standard`, ahead 5 of `telegram-ai-code/web/codex-sample-standard` (pre-existing baseline commits); nothing pushed.
- Stash list: 1 pre-existing entry (`safety/pre-origin-integration`), unchanged.
- Working tree: 127 porcelain entries — the +1 vs the prior session's 126 is the prior rerun attempt's empty `...-RERUN.stdout.txt`, not this session's work. This session created nothing inside the repo except this report file.

## 4. Isolated candidate (fresh, no dirty-tree dependency)

- `git -C /home/b827262/project/e500-codex-smoke archive HEAD | tar -x` → `/home/b827262/e500-015-rerun-pristine`; tree-copied → `/home/b827262/e500-015-rerun-candidate`. Candidate has **no `.git`** (verified) and no dependency on the dirty working tree. No history mutation.
- Copied in ONLY the 11 manifest paths (`cp -p`, modes preserved).
- `diff -rq pristine candidate` → **exactly the 11 paths**: 9 differ, 2 only-in-candidate. Nothing else.
- All 11 files sha256-identical to working-tree bytes (11/11 OK). Fixture mode `775` in both trees.
- Rerun-specific process notes (honest methodology log, neither is a candidate defect):
  1. `git archive HEAD` run while the shell cwd is inside `gpt-codex-bridge/` produces a subtree-rooted archive (paths relative to that subdir). The first extraction attempt silently produced that layout and was detected via the pristine-vs-candidate delta (`Only in candidate: gpt-codex-bridge, reports`), then redone rooted at the repo with `git -C`. Future reruns must extract with the repo root as cwd or `-C`.
  2. The shell cwd resets between Bash tool calls in this harness; two gate invocations intended for the candidate initially executed at the repo root (manifesting as `Start directory is not importable: 'tests'` and 19 pytest collection errors). Re-run with explicit `cd <candidate> &&` in the same command — all green. Recorded so the transient failures are not mistaken for candidate regressions.

## 5. Test gates — exact counts

| Gate | Command (from `gpt-codex-bridge/`) | Main tree | Isolated candidate |
| --- | --- | --- | --- |
| Focused 015 | `python3 -m unittest tests.test_sandbox_enforcement` | **Ran 28 — OK** (0 failed, 0 skipped; real-bwrap leg executed) | **Ran 28 — OK** |
| Full unittest | `python3 -m unittest discover -s tests -t .` | **Ran 206 — OK** | **Ran 206 — OK** |
| Full pytest | `python3 -m pytest -q` | **229 passed, 74 subtests passed, 0 failed** | **229 passed, 74 subtests passed** |
| Compile | `python3 -m compileall -q bridge adapters tests` | n/a | clean |
| Import/collection | pkgutil-driven import of every `bridge.*` and `adapters.*` module | n/a | **14/14 OK** (`bridge.sandbox, claude_runner, agy_runner, codex_runner, config, models, queue, worker, api, workspaces, github_reporter, meeting, observability` + `adapters.telegram`) |

Host-context counts match the remediation and prior readiness reports exactly (28 / 206 / 229+74). The real `RealBubblewrapEnforcementTests` leg ran for real on this host (bwrap 0.9.0), including kernel-denial probes (`WORKSPACE_WRITE=denied`, `HOME_WRITE=denied`, `TMP_HOST_LEAK=0`).

## 6. No-regression probes — 32/32 PASS against isolated candidate code

Probe script (`/home/b827262/e500-015-rerun-probe.py`, run with candidate on `PYTHONPATH`, no live provider calls, no network):

- **Codex argv no-regression — PASS.** `CodexRunner.command_for` → `['codex','exec','--sandbox',<mode>,'-C',…]` for all three modes; no `bwrap`/`--ro-bind` anywhere; `sandbox_launch_plan("codex",…)` prefix empty; mechanism string unchanged.
- **All 9 provider×mode plan resolutions — PASS.** codex: prefix 0 all modes; claude/agy read-only & workspace-write: 21-token bwrap prefix, `--die-with-parent`; danger-full-access: prefix 0, mechanism `none (unrestricted by definition)`.
- **013 failure/needs_attention semantics — PASS end-to-end.** `FAILURE_CLASSES` exactly 7 members with `sandbox_enforcement` distinct. Live `ClaudeRunner.run()` through the real production path with a present-but-unusable bwrap (exit 1): no process launched, report `status="failed"`, `needs_attention=True`, `failure_reason="sandbox_enforcement"`, `describe_failure` → `Claude failed before launch: provider 'claude' cannot enforce sandbox mode 'read-only': sandbox wrapper self-test failed with exit code 1`; no secret leakage into the report; AgyRunner fail-closed likewise.
- **014 workspace/publication ceilings — PASS, gate ordering verified.** Non-allowlisted `/etc` workspace raises `ConfigurationError` for codex, claude, and agy — the 014 ceiling fires before any sandbox planning. No publication path in the diff.

## 7. MEDIUM-2 reassessment (env inheritance and /tmp workspace behavior)

- **Residual stands, non-blocking.** Re-verified in candidate code: `claude_environment()`/`agy_environment()` strip `TELEGRAM_BOT_TOKEN`, `MCP_BEARER_TOKEN`, `MEETING_API_TOKEN` (+ `GEMINI_API_KEY`, `GOOGLE_API_KEY` for agy); the bwrap argv contains no `--clearenv` (string scan of candidate `bridge/sandbox.py`). The child inherits the remaining environment. Disposition unchanged from the prior readiness: a guessed allowlist is the wrong failure mode (breaks production auth for a later job); protocol remains enumerate-names → staged canary jobs → proven allowlist → then `--clearenv` (flag confirmed present in bwrap 0.9.0). Blast radius stays bounded by filesystem read-only outside carve-outs + read-only OAuth re-binds. Documented in the `sandbox.py` docstring shipped in this commit.
- **`/tmp` workspace behavior re-verified mechanically:** for a workspace resident under `/tmp`, `sandbox_launch_plan("claude","workspace-write", /tmp/…)` emits the `--tmpfs /tmp` mount BEFORE the workspace `--bind`, and the workspace bind uses `--bind` (rw) — so `/tmp`-resident allowlisted workspaces still function (the workspace bind shadows the tmpfs at that path). Probe: `tmp-workspace-order` + `tmp-workspace-rw` PASS. Corollary unchanged: `/tmp`-resident non-workspace files (e.g. a probe fixture) would be shadowed — hence the candidate is kept under `/home/b827262`, not `/tmp`; the allowlist itself contains no `/tmp` path.

## 8. Artifacts that must stay excluded from the 015 commit

- Unrelated modified (pre-015, preserved): `skills/gpt-agy-claude-development-loop/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css`.
- Untracked directories: `.claude/`, `agents/`, `ai-meeting-room/`, `scripts/`, `transfer/`, `web/app/`, `web/main/`, `web/e500-control-plane*/`, `sites-project@0.1.0`, `vinext`.
- Root strays: `eslint`, `node` — 0-byte scratch files.
- All other untracked `reports/*` (93 entries): 011/012/V19/V20 evidence, prompt files, deltas, PNGs, `.sites_prompt_latest.txt`, and the empty `...-015-QODER-COMMIT-READINESS-RERUN.stdout.txt` from the prior aborted rerun attempt — runtime/scratch, unrelated.
- Repo-wide `find -name "sbx-acceptance-*"` → none exist (re-verified this session). Fixture canaries are self-cleaning; `.sbx-workspace-violation` exists only inside per-test TemporaryDirectories.
- Gitignored runtime state (never committable, never read): `gpt-codex-bridge/.env` (confirmed ignored), `__pycache__/`, `.pytest_cache/`.
- Scratch trees outside the repo (safe to delete, left in place for inspection): `/home/b827262/e500-015-candidate`, `-head-pristine`, `-v2-pristine`, `-v2-candidate` (prior sessions), `/home/b827262/e500-015-rerun-pristine`, `-rerun-candidate`, `/home/b827262/e500-015-rerun-probe.py` (this session), `/tmp/e500-015-probe`.

## 9. Commit instructions (not executed here)

```
git add gpt-codex-bridge/README.md gpt-codex-bridge/bridge/sandbox.py \
  gpt-codex-bridge/bridge/claude_runner.py gpt-codex-bridge/bridge/agy_runner.py \
  gpt-codex-bridge/bridge/codex_runner.py gpt-codex-bridge/bridge/config.py \
  gpt-codex-bridge/tests/test_agy_runner.py gpt-codex-bridge/tests/test_claude_router.py \
  gpt-codex-bridge/tests/test_sandbox_enforcement.py \
  gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh \
  reports/sites-version-state.json
```

After staging: `git diff --cached --stat` must show exactly the 11 paths and `new file mode 100755` for the fixture.

Recommended commit subject: `bridge: add fail-closed bwrap sandbox enforcement for claude/agy`

Post-commit conditions (none blocking): restart `gpt-codex-worker.service` (still on pre-015 code); land evidence reports as a separate docs commit; open MEDIUM-2 as a tracked follow-up.

## Verdict

Fresh rerun, independently re-derived end to end: manifest re-derived hunk-by-hunk from HEAD diff and byte-verified via isolated-candidate delta (exactly 11 paths, 11/11 sha256 parity, fixture mode 775→100755); no unrelated pre-015 bytes swept in (bridge porcelain = exactly the 10 candidate paths; excluded diffs grepped clean of 015 content); `git diff --check` clean; all gates green in both main tree and isolated candidate (28 / 206 / 229+74, compileall clean, 14/14 import sweep); 32/32 no-regression probes pass against candidate code (Codex argv, 9-combo plans, 013 fail-closed end-to-end, 014 ceilings first, MEDIUM-2 surface, /tmp workspace ordering); no `sbx-acceptance-*` artifacts; index empty; HEAD/push/Sites untouched. Two verification-methodology footguns (git archive cwd rooting; harness cwd resets) were caught by the parity checks themselves and are documented in Section 4 — neither affects the candidate.

**PASS_WITH_FINDINGS** — the single open finding remains the documented, re-review-endorsed MEDIUM-2 residual (environment allowlist proof deferred to a staged follow-up; no code change in this pass).

**READY_TO_COMMIT: YES** (commit itself deliberately not performed).

QODER_GLM53_FLASH_XHIGH_SANDBOX_PARITY_015_COMMIT_READINESS_RERUN_READY
