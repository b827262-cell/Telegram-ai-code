# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Host Runtime + Evidence Finalization

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 POST-COMMIT HOST RUNTIME + EVIDENCE FINALIZATION |
| Implementer | Qoder / GLM-5.3-Flash xhigh |
| Date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke` |
| Bridge | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `6b40084fc36539900bee966527d4424431f77a3d` (`bridge: add fail-closed bwrap sandbox enforcement for claude/agy`) — verified exact at session start and unchanged through report time |
| Inputs | All prior 015 durable reports in `reports/` plus the committed 015 code at HEAD |
| Host | Linux 7.0.0-29-generic x64, Python 3.12.3, bubblewrap 0.9.0 at `/usr/bin/bwrap` (functional self-test exit 0, host context, not nested) |
| Scope | Post-commit service restart + host-context real-bwrap acceptance + evidence docs commit. NO push, NO Sites deploy/save, NO amend/reset/rebase/clean/stash, NO code changes, NO credentials read or output. |

## 1. Pre-touch gates

- `git rev-parse HEAD` → `6b40084fc36539900bee966527d4424431f77a3d` (exact match required; PASS).
- `git status --porcelain -- gpt-codex-bridge/` → empty (tracked bridge tree clean; PASS). Untracked `gpt-codex-bridge/.pytest_cache/` and `__pycache__/` are gitignored.
- Unrelated dirty/untracked work elsewhere in the repo (web/, skills/, other reports) observed and preserved untouched.

## 2. Service restart (gpt-codex-worker.service)

The worker runs as a **user** systemd unit (`~/.config/systemd/user/gpt-codex-worker.service`; the bridge repo only ships the api unit). Verified before restart:

- Pre-restart state: active (running), Main PID `3267111`, started Sat 2026-08-29 17:09:00 CST.
- Safety check: SQLite queue (`/home/b827262/.local/state/gpt-codex-bridge/jobs.sqlite3`) had **zero** jobs with `status='running'`; five most recent jobs all `claude`/`succeeded` — no in-flight work to interrupt.
- Restart: `systemctl --user restart gpt-codex-worker.service` → ok.
- Post-restart state (re-verified at 19:58:58 CST): `MainPID=3298365`, `ActiveState=active`, `ExecMainStartTimestamp=Sat 2026-08-29 19:53:06 CST`; stable across a 10 s soak. The unit's `WorkingDirectory` is the committed bridge tree, so the restart loads the 015 code at HEAD.
- No unrelated service was restarted.

## 3. Host-context real-bwrap acceptance (direct, controlled specimens)

Method: a probe harness executed **on the host** (not inside any nested sandbox) against the committed `bridge.sandbox` code. It built real launch plans via `sandbox_launch_plan(...)`, then ran the controlled specimen `gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh` (and one `/bin/sh` content-write specimen) inside real bubblewrap with a specimen workspace at `gpt-codex-bridge/.sbx-acceptance-015/workspace` (scratch, removed after evidence capture). Specimen runs are local-filesystem and loopback-bind only: no external publication, no `git push`, no `gh`, no Sites operation, no network egress. No queue jobs were submitted (no job IDs); specimen identifiers are the paths above plus the harness at `/tmp/sbx-acceptance-015/run_acceptance.py` (outside the repo).

Harness assertions all passed (`ok: true`, exit 0):

| Specimen | Mode | Observed |
| --- | --- | --- |
| Claude probe tool | read-only | `WORKSPACE_WRITE=denied`, `HOME_WRITE=denied`; `.sbx-workspace-violation` NOT created |
| Claude probe tool | workspace-write | `WORKSPACE_WRITE=allowed`, `HOME_WRITE=denied`; `.sbx-workspace-violation` created |
| Claude `/bin/sh` content write | workspace-write | `printf intended-write > specimen-write-target.txt` → exit 0, file content exactly `intended-write` |
| AGY probe tool | read-only | `WORKSPACE_WRITE=denied`, `HOME_WRITE=denied`; violation file NOT created |
| AGY probe tool | workspace-write | `WORKSPACE_WRITE=allowed`, `HOME_WRITE=denied`; violation file created |

Additional per-run observations across all four mode/provider combinations: bwrap `--ro-bind / /` root mount confirmed, fresh private `/tmp` (`TMP_HOST_LEAK=0`), kernel-level EROFS denials (not prompt cooperation).

**Fail-closed (intentionally invalid enforcement setup, controlled):** a broken wrapper script exiting 77 was substituted via `sandbox_launch_plan("claude", "read-only", ..., bwrap_bin=<broken>)`. Result: `SandboxEnforcementError` raised **before any provider launch**, reason `sandbox wrapper self-test failed with exit code 77`. The wrapper invocation log shows exactly one invocation — the `/bin/true` self-test probe — proving the provider process never started.

## 4. Focused 015 tests and full suite

| Gate | Command (from `gpt-codex-bridge/`) | Result |
| --- | --- | --- |
| Focused 015 | `python3.12 -m pytest tests/test_sandbox_enforcement.py -v` | **28 passed, 5 subtests passed, 0 failed, 0 skipped** |
| Full suite | `python3.12 -m pytest tests/` | **229 passed, 0 failed, 0 skipped** (13.49 s) |

Host-context vs nested-sandbox distinction: this session ran in host context with functional bwrap (`_bwrap_unavailable_reason` → `None`), so all 7 `RealBubblewrapEnforcementTests` **executed for real** (kernel-level denial proofs) instead of skipping. This is the leg the prior nested-sandbox reviewer sessions (CLAUDE-REVIEW / CLAUDE-REREVIEW) had to skip honestly; it is now exercised green on the host. No skip markers were reported anywhere in either run.

## 5. Evidence/docs scope for the docs-only commit

Included (14 files — all 015-scoped, cross-referenced by the chain):

Durable reports (8):
1. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-REVIEW.md`
2. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-QODER-REMEDIATION.md`
3. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-REREVIEW.md`
4. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-COMMIT-READINESS.md`
5. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-QODER-COMMIT-READINESS.md`
6. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-QODER-COMMIT-READINESS-RERUN.md`
7. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-CLAUDE-COMMIT-READINESS-RERUN.md`
8. `reports/E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015-HOST-RUNTIME-FINALIZATION.md` (this report)

Corresponding task prompts (6, each 1:1 with a durable report): `-CLAUDE.prompt.txt`, `-QODER-REMEDIATION.prompt.txt`, `-CLAUDE-REREVIEW.prompt.txt`, `-CLAUDE-COMMIT-READINESS.prompt.txt`, `-QODER-COMMIT-READINESS.prompt.txt`, `-QODER.prompt.txt`.

This matches the sequencing decision recorded in the chain (CLAUDE-COMMIT-READINESS-RERUN §9: land 015 `.md` + `.prompt.txt` evidence in a separate docs commit after the post-commit host bwrap run; QODER-COMMIT-READINESS-RERUN §1 concurs).

Excluded (and why):
- **stdout scratch:** `-HOST-RUNTIME-QODER.stdout.txt` (empty), `-QODER-COMMIT-READINESS-RERUN.stdout.txt` — scratch, never part of the durable chain.
- **Runtime specimens:** `gpt-codex-bridge/.sbx-acceptance-015/` (removed after capture), `/tmp/sbx-acceptance-015/` (outside repo).
- **Caches:** `.pytest_cache/`, `__pycache__/` (gitignored, untouched).
- **Unrelated dirty work:** `skills/gpt-agy-claude-development-loop/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css`.
- **Unrelated untracked content:** all other `reports/*` tasks, `.claude/`, `agents/`, `ai-meeting-room/`, and the rest of the pre-existing porcelain set — preserved, not staged.
- **Secrets:** no `.env`, credentials, tokens, or key material staged; `.env` was read only as key names for the queue path lookup.

## 6. Final verdict

All runtime gates green: HEAD exact and bridge tree clean before and after; worker restarted and active (PID 3298365, started 2026-08-29 19:53:06 CST); Claude and AGY host real-bwrap acceptance fully green for read-only denial, workspace-write allow, intended content write, and fail-closed-before-launch; focused 015 suite 28 passed with the real-bwrap leg executed (not skipped); full suite 229 passed. Evidence scope is exact: docs-only commit staged by explicit path, no code/state paths, stdout scratch and specimens excluded.

**VERDICT: PASS**

QODER_GLM53_FLASH_XHIGH_SANDBOX_PARITY_015_HOST_RUNTIME_FINALIZED
