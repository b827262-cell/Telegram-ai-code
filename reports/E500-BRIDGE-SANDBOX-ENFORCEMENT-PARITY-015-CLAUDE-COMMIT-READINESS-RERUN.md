# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Claude Commit-Readiness FINAL REVIEW RERUN

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 COMMIT READINESS FINAL REVIEW RERUN |
| Reviewer | Claude Opus 5 / medium — independent final reviewer |
| Date | 2026-08-29 |
| Mode | READ ONLY (no tracked-file modification, no staging/commit/push, no service or external action) |
| Workspace | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` — verified unchanged at start and at report time |
| Primary input | `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-QODER-COMMIT-READINESS-RERUN.md` |
| Trust posture | Fresh second review. Prior verdicts (Claude and Qoder) treated as evidence only; every claim below re-derived from current bytes this session. |

---

## Executive summary

The 015 change is coherent, correctly scoped, fail-closed by construction, and the
11-path manifest is independently confirmed complete and minimal. All gates I can
run in this environment are green, and my own no-regression probes pass on an
isolated candidate built without any dependency on the dirty working tree.

I reached the same disposition as Qoder, but **not by the same evidence**: one of
Qoder's headline test-count claims does not reproduce here, and the discrepancy is
explained by reviewer-environment nesting rather than by a candidate defect. That
distinction is set out in §5 because it changes what the green gates actually prove.

**FINAL_VERDICT: PASS_WITH_NONBLOCKING_FINDINGS**
**READY_TO_COMMIT: YES** — with the 11-path manifest exactly as proposed, no corrections.

---

## 1. Manifest independently derived — 11 paths, complete and minimal

Derived from `git status --porcelain` + `git diff HEAD` hunk-by-hunk, then
byte-verified by an isolated-candidate delta diff built fresh this session (§4).

### Modified (8, tracked)

| # | Path | 015 attribution verified |
| --- | --- | --- |
| 1 | `gpt-codex-bridge/bridge/sandbox.py` | +185/−? — module docstring incl. explicit isolation-boundary paragraph (MEDIUM-3), `SandboxEnforcementError`, `SandboxLaunchPlan`, `_bwrap_unavailable_reason`, `_bwrap_argv`, `sandbox_launch_plan`, mechanism map, carve-out tables. Pre-existing `validate_sandbox_mode`/`SANDBOX_MODES` untouched. All 015. |
| 2 | `gpt-codex-bridge/bridge/claude_runner.py` | `command_for` captures `validate_workspace` return, builds plan, wraps argv; `run()` catches `SandboxEnforcementError` → failed/needs_attention report before `_popen`. All 015. |
| 3 | `gpt-codex-bridge/bridge/agy_runner.py` | Same shape. **LOW-2 revert independently confirmed**: baseline `HEAD:gpt-codex-bridge/bridge/agy_runner.py:168` and current line 186 are byte-identical `report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)`; appears in the diff as context only. Same verified for `claude_runner.py` (baseline 122 → current 135). |
| 4 | `gpt-codex-bridge/bridge/codex_runner.py` | Exactly +6/−0: `FAILURE_SANDBOX_ENFORCEMENT` constant, `FAILURE_CLASSES` member, 4-line `describe_failure` branch. Codex argv construction untouched. |
| 5 | `gpt-codex-bridge/bridge/config.py` | Exactly +2: `sandbox_bwrap_bin` field + `CODEX_SANDBOX_BWRAP_BIN` env read. |
| 6 | `gpt-codex-bridge/README.md` | 1 modified + 1 added prose line; attributes runtime isolation to codex `--sandbox` vs claude/agy bubblewrap and states the wrapper non-goals. Matches shipped code. |
| 7 | `gpt-codex-bridge/tests/test_agy_runner.py` | setUp probe mocks (`_which`, `_execute_probe`), exact-argv `expected_prefix`, temp `.gemini` HOME. 015-only. |
| 8 | `gpt-codex-bridge/tests/test_claude_router.py` | Same setUp pattern, wrapper-aware argv slicing at `--`, `sandbox_mode` report assertion. 015-only. |

### New (2, untracked)

| # | Path | Verified |
| --- | --- | --- |
| 9 | `gpt-codex-bridge/tests/test_sandbox_enforcement.py` | 606 lines; 28 tests across 5 classes, all 015-scoped. |
| 10 | `gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh` | 57 lines POSIX `sh`; on-disk mode `775` → git records `100755`. §3 below. |

### State (1, tracked)

| # | Path | Verified |
| --- | --- | --- |
| 11 | `reports/sites-version-state.json` | Diff is exactly −1/+1 on the pre-existing `sandbox_enforcement_parity_015` key: `not_started` → `closed_pass_with_nonblocking_findings`. No schema invention, no other key touched. |

**Completeness/minimality:** `git status --porcelain -- gpt-codex-bridge` returns
exactly the 10 bridge paths (8 `M` + 2 `??`) and nothing else. Adding
`reports/sites-version-state.json` gives 11. Nothing in the 015 code imports or
requires a path outside this set — verified by a 14/14 `bridge.*`/`adapters.*`
import sweep and a full compileall on the isolated candidate. Manifest is
**complete and minimal**. Confirms Qoder's manifest; no corrections.

## 2. No unrelated bytes swept in

The five excluded modified files (`skills/gpt-agy-claude-development-loop/SKILL.md`,
`web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`,
`web/sample/src/styles.css`) were grepped across their full diffs for
`sandbox|bwrap|enforcement|015`. Three incidental hits, all pre-existing and
unrelated to 015:

- `SKILL.md`: a policy sentence "Web 不得繞過 … sandbox policy" (dispatch policy prose, not 015).
- `App.tsx`: a `<span>Sandbox</span>` UI label moved by the web control-plane reindentation.

No 015 content leaks into the excluded set. `git diff --check` → exit 0.

## 3. Executable fixture mode semantics — verified

On-disk mode is `775` (`rwxrwxr-x`), preserved as `775` in the isolated candidate
via `cp -p`. Git stores only the owner-execute bit, so the recorded index mode will
be `100755`, not `100775` — the post-staging expectation in Qoder's §9 is correct.
The fixture is a `#!/bin/sh` POSIX script; it needs the x-bit because
`test_sandbox_enforcement.py` executes it directly as a fake provider CLI. The
canaries it writes are self-cleaning (`rm -f` for `/tmp` and `$HOME` probes), and
the one non-cleaned file (`.sbx-workspace-violation`) is written only into
per-test `TemporaryDirectory` workspaces. Repo-wide `find` for `sbx-acceptance-*`
and `.sbx-*` returns nothing. Correct and commit-safe.

## 4. Isolated-candidate method — independently rebuilt, and Qoder's footguns assessed

I did **not** reuse Qoder's candidate. I built my own at `/tmp/e500-015-claude-rerun`:

- `git -C <repo-root> archive HEAD | tar -x -C pristine` — the `-C` form avoids
  footgun #1 by construction. Verified the extracted top level is the repo root
  layout (`gpt-codex-bridge`, `reports`, `skills`, `web`, `*.md`), not a subtree.
- `cp -a pristine/. candidate/`, then `cp -p` of exactly the 11 manifest paths.
- Candidate contains **no `.git`** (verified) → zero dependence on the dirty tree.
- `diff -rq pristine candidate` → **exactly 11 entries**: 9 differing files + 2
  only-in-candidate. Nothing else.
- **11/11 sha256 parity** between candidate and working-tree bytes.

**On Qoder's two verification footguns (review requirement 8):** both are real
properties of this harness — I hit the same cwd-reset behaviour (every Bash call
resets cwd, visible in my own transcript). Both are *methodology* hazards that
produce false negatives (spurious failures), never false positives, and both were
caught by the parity checks themselves. Because I rebuilt the candidate
independently with `git -C` and used absolute `cd … &&` in every gate invocation,
my results are not exposed to either. **Neither affects commit readiness.**

**One deviation worth recording:** Qoder deliberately kept its candidate outside
`/tmp`. My agent sandbox makes `/home/b827262` read-only, so `/tmp` was the only
writable location. This is safe here — the `/tmp` shadowing concern applies to a
sandboxed *child's* view of `/tmp` at runtime, not to a host-side directory used
for static gate execution. No test binds or tmpfs-shadows my candidate path.

## 5. Gates rerun independently — and a correction to Qoder's counts

| Gate | Main tree (my run) | Isolated candidate (my run) | Qoder's claim |
| --- | --- | --- | --- |
| Focused 015 | Ran 28 — **OK (skipped=7)** | Ran 28 — **OK (skipped=7)** | "Ran 28 — OK (0 failed, **0 skipped**; real-bwrap leg executed for real)" |
| Full unittest | Ran 206 — OK (skipped=7) | Ran 206 — OK (skipped=7) | Ran 206 — OK |
| Full pytest | **222 passed, 7 skipped**, 74 subtests | **222 passed, 7 skipped**, 74 subtests | "**229 passed**, 74 subtests, 0 failed" |
| compileall | — | clean | clean |
| Import sweep | — | 14/14 modules OK | 14/14 OK |

**The discrepancy is environmental, not a candidate defect.** The 7 skips are
exactly the real-kernel enforcement leg (`RealBubblewrapEnforcementTests` ×6 +
`RealBwrapGateTests.test_functional_bwrap_still_builds_real_enforcement_plans`),
all reporting `functional bwrap enforcement unavailable: sandbox wrapper self-test
failed with exit code 1`. Root cause, diagnosed directly:

- `bwrap 0.9.0` **is** installed at `/usr/bin/bwrap`.
- `kernel.unprivileged_userns_clone=1`, `user.max_user_namespaces=2147483647` — the host permits userns.
- But my `/proc/self/uid_map` is `1000 0 1` (single-uid) and my userns is
  `user:[4026534772]` — I am **already inside a non-initial user namespace** that
  forbids nesting. `bwrap … -- /bin/true` fails with "No permissions to create new namespace".

This is precisely the "present-but-unusable bwrap (nested sandboxes, hardened CI)"
case the gate was written for, and it skips *honestly* rather than failing — which
is itself the correct behaviour, verified by
`test_skip_state_matches_the_production_functional_probe`, which **passed**.

**Conclusion:** Qoder's 28/0-skipped and 229-passed figures are credible for the
real host (initial user namespace, where the real-bwrap leg runs); my 7 skips and
222 passed are an artifact of the reviewer sandbox. `222 + 7 = 229` reconciles the
two exactly. **Finding LOW-A** below records the reporting imprecision, not a defect.

**Consequence for readiness:** I could not personally witness kernel-level denial
(`WORKSPACE_WRITE=denied`, `HOME_WRITE=denied`, `TMP_HOST_LEAK=0`). That leg is
attested by Qoder's rerun and by the prior 015 remediation/re-review reports, and
the argv it depends on is verified structurally by my own probes (§6). I treat
this as adequately evidenced but explicitly note it as second-hand — see LOW-B.

## 6. No-regression probes against the isolated candidate — all pass

Run with the candidate on `sys.path`, no live provider calls, no network.

- **Codex argv no-regression — PASS.** For all three modes,
  `CodexRunner.command_for(job, report_path)` → `['codex','exec','--sandbox',<mode>,…]`;
  no `bwrap`/`--ro-bind`/`--die-with-parent` token anywhere in the argv;
  `sandbox_launch_plan("codex",…).argv_prefix == ()` for every mode; mechanism
  string is the native-CLI one. Codex is fully untouched.
- **All 9 provider×mode plan resolutions — PASS.** codex: empty prefix, all modes.
  claude/agy `danger-full-access`: empty prefix, mechanism `none (unrestricted by
  definition)`. claude/agy `read-only`/`workspace-write`: well-formed wrapper —
  `argv[0]=='bwrap'`, terminates `--die-with-parent --`, workspace bound
  `--ro-bind` in read-only and `--bind` in workspace-write.
  *Correction to Qoder:* the prefix is **not** a fixed 21 tokens — it is
  HOME-state-dependent: **15** with no carve-out dirs present, **21** for agy with
  `.gemini` + creds, **27** for claude with all three state dirs + creds. Qoder's
  "21-token" figure is the agy case generalised. Cosmetic reporting imprecision
  only; the `_HARNESS_STATE_DIRS` "exclude, never create" behaviour is correct and
  is covered by `test_missing_state_carve_outs_are_excluded_not_created`.
- **Credential protection — PASS.** `.claude/.credentials.json` and
  `.gemini/oauth_creds.json` are re-bound `--ro-bind` **even in workspace-write**.
- **013 failure/needs_attention semantics — PASS end-to-end, both runners.**
  Through the real `run()` path with an unusable bwrap: no process launched
  (popen factory would have raised), `status="failed"`, `needs_attention=True`,
  `explicit_failure="sandbox_enforcement"`, and
  `describe_failure` → `Claude failed before launch: provider 'claude' cannot
  enforce sandbox mode 'read-only': sandbox wrapper self-test failed with exit
  code 1` (Agy equivalent). `FAILURE_CLASSES` is exactly 7 members with
  `sandbox_enforcement` distinct. No secret material in the report JSON.
- **014 workspace/publication ceilings — PASS, ordering correct.** A
  non-allowlisted `/etc` workspace raises `ConfigurationError` for codex, claude,
  **and** agy — the 014 admission ceiling fires *before* any sandbox planning, so
  015 cannot widen the dispatch boundary. No publication path in the diff.

## 7. MEDIUM-2 (env inheritance) — reassessed, NON-BLOCKING

Independently re-verified on candidate bytes:

- `claude_environment()` / `agy_environment()` strip `TELEGRAM_BOT_TOKEN`,
  `MCP_BEARER_TOKEN`, `MEETING_API_TOKEN` (+ `GEMINI_API_KEY`, `GOOGLE_API_KEY`
  for agy) — measured, 0 leaked of the 5 named.
- `grep -c clearenv bridge/sandbox.py` → **0**. The wrapper does not clear the
  environment; **71** other variables are inherited by the child.
- `bwrap 0.9.0 --help` does list `--clearenv`, so the fix is available when wanted.

**Assessment — I agree this is non-blocking, and the reasoning matters.** 015 does
not *introduce* this exposure: env inheritance is the pre-015 status quo, and 015
strictly reduces blast radius (root read-only, private `/tmp`, credentials
re-bound read-only). Shipping a *guessed* `--clearenv` allowlist is the more
dangerous change — it fails by silently breaking production auth for a later job,
a worse failure mode than a documented, bounded residual. The correct sequence is
enumerate → staged canary → proven allowlist → `--clearenv`. The limitation is
honestly documented in the shipped `sandbox.py` docstring and the README, which is
what makes deferral legitimate rather than a silent gap. **Track as follow-up; do
not block the commit.**

## 8. `/tmp` workspace behaviour — Qoder's claim verified TRUE

Requirement 7 asked me to check Qoder's claim that the workspace bind is emitted
*after* the tmpfs `/tmp`. **Confirmed mechanically**, not just by reading the
comment: for a `/tmp`-resident workspace,
`sandbox_launch_plan("claude","workspace-write", /tmp/…)` emits `--tmpfs /tmp` at
index 8 and the workspace `--bind` at index 10 — tmpfs first, workspace bind after,
so the later bind shadows the tmpfs at that path and the workspace stays writable.
The bind is `--bind` (rw) as required. The code comment in `_bwrap_argv` states
this intent explicitly and matches behaviour.

Corollary (unchanged, correct): `/tmp`-resident **non-workspace** files are
shadowed inside the sandbox. This has no bearing on the commit — the workspace
allowlist contains no `/tmp` path, and it affects only runtime child processes,
never host-side gate execution.

## 9. Evidence-report inclusion policy — decision

**Exclude all `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-*.md` and
`.prompt.txt` from the 015 code commit; land them as a separate docs commit.**
Rationale: the code commit should be revertable as a single behavioural unit
without dragging review prose with it, and the evidence set is still growing (this
report is being added after Qoder's). The one state file
`reports/sites-version-state.json` is the deliberate exception — it is
machine-read control-plane state whose value must flip atomically with the code it
describes, not narrative evidence. I endorse Qoder's sequencing unchanged.

## 10. Containment — no staging, commit, push, or publication occurred

- HEAD is still `b1375230a0027aa00b2842a1f6fb669846a67d75`; reflog top is that same
  commit with no new entries.
- Index empty throughout (`git diff --cached --name-only` → 0 paths), before and after my review.
- No `git add`/`commit`/`push`/`amend`/`reset`/`rebase`/`checkout`/`restore`/`stash`/`clean` executed.
- No Sites save/deploy/publish; no external network write; no credentials read or printed.
- No tracked file modified by me. My only writes were to `/tmp/e500-015-claude-rerun`
  (outside the repo) and this report (outside the repo).
- Working tree porcelain: 128 entries. This is +1 vs Qoder's 127 and is accounted
  for by Qoder's own `-RERUN.md` report file, created after its count was taken.

**Excluded scratch/runtime artifacts (must not be staged):**

- Unrelated modified: `skills/gpt-agy-claude-development-loop/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css`.
- Untracked dirs: `.claude/`, `agents/`, `ai-meeting-room/`, `scripts/`, `transfer/`, `web/app/`, `web/main/`, `web/e500-control-plane*/`, `sites-project@0.1.0`, `vinext`, `reports/sites-deltas/`, `reports/web-compare/`, `skills/e500-chatgpt-sites-sync/`, `skills/multi-agent-git-programming/`.
- Root strays: `eslint`, `node` (0-byte scratch).
- All other untracked `reports/*`: 011/012/V19/V20 evidence, prompts, deltas, PNGs, `.sites_prompt_latest.txt`, and the empty `…-015-QODER-COMMIT-READINESS-RERUN.stdout.txt`.
- Gitignored runtime state, never committable: `gpt-codex-bridge/.env` (confirmed ignored via `gpt-codex-bridge/.gitignore:1`), `__pycache__/`, `.pytest_cache/`.
- Off-repo scratch, safe to delete: `/tmp/e500-015-claude-rerun` (mine), plus Qoder's `/home/b827262/e500-015-*` trees.

---

## Findings

| ID | Severity | Finding | Blocking |
| --- | --- | --- | --- |
| MEDIUM-2 | Medium | bwrap wrapper does not `--clearenv`; 71 non-secret env vars inherited by the sandboxed child. Named adapter secrets *are* stripped. Pre-existing exposure, reduced but not eliminated by 015; documented in shipped docstring + README. | **No** — deferred to a staged follow-up by design; a guessed allowlist is the worse failure mode. |
| LOW-A | Low | Qoder's rerun reports "0 skipped / 229 passed"; the run is environment-dependent. On any host without initial-userns access the honest result is 7 skipped / 222 passed. Future evidence reports should state the skip count and the userns context rather than an absolute. | No — reporting precision only. |
| LOW-B | Low | The real-kernel denial leg could not be witnessed by this reviewer (nested userns). It is attested by Qoder's rerun and prior 015 reports, and the argv it exercises is structurally verified here. Worth one post-commit confirmation run on the real host. | No. |
| LOW-C | Low | Qoder describes the bwrap prefix as a flat "21-token" prefix; it is actually 15/21/27 tokens depending on which HOME state dirs and credential files exist. Behaviour is correct; only the description is imprecise. | No. |

No HIGH or blocking findings. No correctness defect found in any of the 11 paths.

---

## Approved path list — exactly as proposed, no corrections

```
gpt-codex-bridge/README.md
gpt-codex-bridge/bridge/sandbox.py
gpt-codex-bridge/bridge/claude_runner.py
gpt-codex-bridge/bridge/agy_runner.py
gpt-codex-bridge/bridge/codex_runner.py
gpt-codex-bridge/bridge/config.py
gpt-codex-bridge/tests/test_agy_runner.py
gpt-codex-bridge/tests/test_claude_router.py
gpt-codex-bridge/tests/test_sandbox_enforcement.py
gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh
reports/sites-version-state.json
```

After staging, `git diff --cached --stat` must show exactly these 11 paths, and the
fixture must appear as `new file mode 100755`.

Recommended subject: `bridge: add fail-closed bwrap sandbox enforcement for claude/agy`

**Post-commit, non-blocking:** restart `gpt-codex-worker.service` (still running
pre-015 code); land 015 evidence reports as a separate docs commit; open MEDIUM-2
as a tracked follow-up; run the real-bwrap leg once on the host to close LOW-B.

---

**FINAL_VERDICT: PASS_WITH_NONBLOCKING_FINDINGS**
**READY_TO_COMMIT: YES**

Commit deliberately not performed. No staging, push, or publication occurred.

CLAUDE_OPUS5_MEDIUM_SANDBOX_PARITY_015_COMMIT_READINESS_RERUN_COMPLETE
