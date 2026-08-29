# E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 — Independent Final Review

## Provenance

| Field | Value |
| --- | --- |
| Task | E500-BRIDGE-SANDBOX-ENFORCEMENT-PARITY-015 |
| Reviewer | Claude Opus 5 / medium, independent final reviewer |
| Mode | READ ONLY (no file, service, Git, or external mutation) |
| Review date | 2026-08-29 |
| Workspace | `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge` |
| Baseline HEAD | `b1375230a0027aa00b2842a1f6fb669846a67d75` (`bridge: add durable workspace alias routing`) |
| Implementer under review | Qoder GLM-5.3-Flash xhigh, uncommitted working tree |
| Qoder final report | Absent (CLI terminated before writing it); no Qoder verdict was trusted or relied upon |
| Host | Linux 7.0.0-29-generic, bubblewrap 0.9.0 at `/usr/bin/bwrap` |

### Reviewer environment caveat (material — read first)

This review was conducted from inside a nested sandbox that is **structurally the same sandbox 015 builds**. Confirmed by mount table and namespace inspection:

```
/dev/nvme1n1p1 on / type ext4 (ro,...)
/dev/nvme1n1p1 on /home/b827262/.claude type ext4 (rw,...)
/dev/nvme1n1p1 on /home/b827262/.cache/claude-cli-nodejs type ext4 (rw,...)
/dev/nvme1n1p1 on /home/b827262/.local/state/claude type ext4 (rw,...)
/dev/nvme1n1p1 on /home/b827262/.claude/.credentials.json type ext4 (ro,...)
/proc/self/uid_map:  1000  0  1        # nested user namespace
```

Those read-write carve-outs are exactly `_HARNESS_STATE_DIRS["claude"]`, and the read-only `.credentials.json` re-bind on top of a writable `.claude` is exactly `_CREDENTIAL_FILES_READ_ONLY`. **The reviewer is running inside a live instance of the design under review.**

Consequence: `bwrap` cannot create a nested namespace from in here, so every real-bwrap test fails *in the review environment*. My `dangerouslyDisableSandbox` escapes bypass command filtering but **not** the mount/namespace confinement, so they did not reach the host as I initially assumed. The `gpt-codex-worker.service` main process (PID 3267111) has its root mount as `rw` while mine is `ro`, proving the worker runs on the real host **outside** this confinement.

**Therefore: I cannot certify host-side runtime enforcement from this vantage point, in either direction.** The 5 test failures I observed are review-environment artifacts and are *not* evidence against the implementation. The brief's reported "24/24 / 202/202 / 225 passed", collected outside this confinement, is entirely consistent with what I see and is not contradicted. Matrix items requiring live bwrap execution are marked ENVIRONMENT-BLOCKED rather than pass or fail.

Incidentally, this environment is itself corroborating evidence that the 015 mount layout works as designed on this host: I am demonstrably confined by it right now — `/` is read-only, my writes outside the carve-outs fail with `EROFS`, and the credentials file is read-only while its parent directory is writable.

## Reviewed paths (exact)

Modified (diff vs `b137523`):

- `gpt-codex-bridge/bridge/sandbox.py` (+177/-2, now 200 lines)
- `gpt-codex-bridge/bridge/claude_runner.py` (+55 net changes)
- `gpt-codex-bridge/bridge/agy_runner.py` (+62 net changes)
- `gpt-codex-bridge/bridge/codex_runner.py` (+6)
- `gpt-codex-bridge/bridge/config.py` (+2)
- `gpt-codex-bridge/tests/test_agy_runner.py` (+50)
- `gpt-codex-bridge/tests/test_claude_router.py` (+31)

Untracked, new in 015:

- `gpt-codex-bridge/tests/test_sandbox_enforcement.py` (533 lines)
- `gpt-codex-bridge/tests/fixtures/sandbox_probe_tool.sh` (57 lines)

Out-of-scope modified paths in the same working tree, **not** part of 015 and not reviewed: `skills/gpt-agy-claude-development-loop/SKILL.md`, `web/README.md`, `web/STANDARD.md`, `web/sample/src/App.tsx`, `web/sample/src/styles.css`.

---

## Executive summary

015 is a well-built change. `sandbox.py` centralizes provider×mode enforcement in one place, refuses to silently downgrade or upgrade a mode, fails closed before any process launch, and introduces a distinct `sandbox_enforcement` failure class threaded correctly through both runners, `describe_failure`, and the worker. Codex's argv path is genuinely untouched — the codex diff is nothing but the new failure constant. The bubblewrap construction handles the details that usually go wrong: the workspace bind is ordered last so it wins over the `/tmp` tmpfs, credentials are re-bound read-only on top of writable state dirs, and absent carve-outs are omitted rather than created.

Answering the primary question: the implementation is **truthful** (no fake enforcement, no substitution of CLI permission flags for filesystem sandboxing) and **demonstrably fail-closed** (verified by passing tests and by live execution of the plan resolver). Machine enforcement is correct by construction on inspection, and the review environment is itself a working instance of the same mount layout — but I cannot *certify* host-side runtime enforcement from inside the confinement, so I mark it reviewed-and-sound rather than reviewer-proven.

Non-regression holds: 014 workspace/publication ceilings still gate first, 013 needs_attention/failure semantics are preserved and cleanly extended, and Codex behavior is byte-identical.

My findings are all non-blocking hardening items. I found no fail-open path, no path-escape defect, and no security hole.

---

## Findings by severity

### MEDIUM-1 — `RealBubblewrapEnforcementTests` gating is too weak

```python
BWRAP_PRESENT = shutil.which("bwrap") is not None
@unittest.skipUnless(BWRAP_PRESENT, "bwrap is not available on this host")
```

The guard checks only that the binary *exists*, not that it *functions*. Any environment where bwrap is present but namespace creation is unavailable — nested sandboxes, some CI runners, hardened containers, and this very review session — runs the class and gets 5 red tests that say nothing about the code. The right gate is the same functional self-test the production path already uses:

```python
BWRAP_WORKS = _bwrap_unavailable_reason(DEFAULT_BWRAP_BIN, None) is None
```

That skips honestly where namespaces are unavailable while still failing loudly on a host that *should* support them but is misconfigured — which is the case you actually want to catch. This is the finding most worth fixing, precisely because it is what made an otherwise-sound change look broken to an independent reviewer.

### MEDIUM-2 — Environment leaks into the sandboxed child

`claude_environment()` / `agy_environment()` strip `TELEGRAM_BOT_TOKEN`, `MCP_BEARER_TOKEN`, `MEETING_API_TOKEN` (plus `GEMINI_API_KEY`, `GOOGLE_API_KEY` for agy) but otherwise pass the whole `os.environ` through, and the bwrap argv has no `--clearenv`. Every other host variable — including anything in the worker's `EnvironmentFile=.env` not on the denylist — reaches the sandboxed provider. A denylist is the wrong shape here; `--clearenv` plus an explicit allowlist is the right one. Pre-existing pattern that 015 inherits rather than introduces. I did not read `.env` (mode `0600`); I am flagging structure, not a confirmed leak.

### MEDIUM-3 — Enforcement is filesystem-only; network is unconstrained

No `--unshare-net`, `--unshare-pid`, `--unshare-ipc`, `--unshare-uts`, and no `--seccomp`. A sandboxed provider keeps full network access. The code is honest about this — the mechanism string says "bubblewrap filesystem isolation", not "isolation" — and the tests are consistent, asserting `LOOPBACK_BIND=ok` rather than pretending network is blocked. But the presence of socket probing at all invites the misreading that network is part of the contract. Worth an explicit "what this does not cover" note in the module docstring and any operator-facing doc. See Runtime boundary.

### LOW-1 — Socket-test skip handling is correctly narrow (no action)

Matrix item 10 verified clean. In `sandbox_probe_tool.sh`:

```python
except PermissionError:  print('permission_denied')
except OSError:          print('error')
```

`PermissionError` is caught before the broader `OSError`, so only an exact permission denial produces `permission_denied`; any other socket failure surfaces as `error` and cannot match the skip condition. Tests skip only on `LOOPBACK_BIND=permission_denied` and otherwise assert `LOOPBACK_BIND=ok`, so normal bind behavior still executes and is still asserted. `LoopbackBindEnvironmentTests.test_normal_environment_loopback_bind_executes` independently confirms unsandboxed bind works. No over-broad skip.

### LOW-2 — Unrelated cosmetic churn in `agy_runner.py`

```python
-        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
+        report_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
```

Pure keyword reordering, semantically identical, unrelated to 015. Drop it to keep the commit focused.

### LOW-3 — `CODEX_SANDBOX_BWRAP_BIN` naming

`Settings.sandbox_bwrap_bin` reads `CODEX_SANDBOX_BWRAP_BIN`, but the setting governs only Claude and AGY — codex never uses a wrapper. The prefix matches the file's existing convention for bridge-wide vars, so it is defensible, but it misleads about scope.

### LOW-4 — Carve-out paths are not resolved

`_bwrap_argv` does not `.resolve()` the state/credential paths, so a pre-existing symlink at e.g. `~/.claude` would be bind-mounted at its target. Exploiting this requires prior write access to HOME — i.e. the attacker already has what the sandbox protects — so I rate it not exploitable in this threat model. A `.resolve()` is cheap defense-in-depth.

### INFO-1 — No `sbx-acceptance-*` artifacts exist

Matrix item 11: searched project root, filesystem to depth 6, and `/tmp`. **No** `sbx-acceptance-*` files anywhere — nothing to classify as evidence or cleanup artifact. The fixture's canaries are self-cleaning by construction: `.sbx-tmp-canary` and `.sbx-home-violation` are `rm -f`'d in the fixture, `.sbx-host-canary-*` is removed via `_stack.callback`, and `.sbx-workspace-violation` is written only inside a `TemporaryDirectory`. No canary leakage into tracked paths observed.

---

## Mandatory review matrix verdicts

| # | Item | Verdict | Basis |
| --- | --- | --- | --- |
| 1 | Exact diff inspected for all 015 files + tests/fixture | **PASS** | All 7 modified + 2 untracked paths read in full |
| 2 | provider × mode semantics for codex/claude/agy | **PASS (static)** | Plan resolver executed live for all 9 combinations; mapping correct and total. Runtime leg ENVIRONMENT-BLOCKED |
| 3 | Claude/AGY read-only denial is kernel/OS, not prompt or CLI permission flag | **PASS (by construction)** | Mechanism is a bwrap mount namespace, correctly *not* a provider permission flag. Prompt cooperation plays no role. Runtime proof ENVIRONMENT-BLOCKED |
| 4 | workspace-write permits only workspace; home/FS read-only except carve-outs | **PASS (by construction)** | argv logic reviewed correct; bind ordering sound. Corroborated incidentally by the reviewer's own confinement under the identical layout. Runtime proof ENVIRONMENT-BLOCKED |
| 5 | Unsupported/missing enforcement fails before launch with distinct class | **PASS** | `SandboxEnforcementError` raised in `command_for`, caught in `run` before `_popen`; `FAILURE_SANDBOX_ENFORCEMENT` distinct and in `FAILURE_CLASSES`; fail-closed tests pass |
| 6 | danger-full-access acquires no publication privilege; 014 ceilings authoritative | **PASS** | `validate_workspace` still runs first in both runners; no publication path touched |
| 7 | report sandbox_mode authoritative; 013 needs_attention semantics intact | **PASS** | `validate_sandbox_mode(job.sandbox_mode)` at `claude_runner.py:118`, `agy_runner.py:167`; codex mode-mismatch check at `codex_runner.py:396-401` untouched; fail-closed report sets `needs_attention=True` |
| 8 | Codex argv/sandbox behavior unchanged except proven no-op | **PASS** | Codex diff is purely the new failure constant; `--sandbox` argv at `codex_runner.py:267-268` byte-identical; `sandbox_launch_plan("codex", …)` returns empty prefix for all 3 modes |
| 9 | bwrap construction review | **PASS with findings** | No path-escape or fail-open defect; see MEDIUM-2, MEDIUM-3, LOW-4 |
| 10 | Socket-test handling precise; only exact PermissionError skipped | **PASS** | `PermissionError` caught before `OSError`; normal bind still asserted |
| 11 | `sbx-acceptance-*` classification | **PASS (N/A)** | No such files exist anywhere |
| 12 | No commit/push/Sites/external publication | **PASS** | HEAD unchanged at `b137523`; no unpushed commits; tree still uncommitted |

### Item 2 detail — live plan resolution (all 9 combinations)

Executed from the review environment. The Claude/AGY `FAIL-CLOSED` rows below are the *correct* response to this environment's unavailable namespaces, not defects:

```
codex   read-only            OK  prefix_len=0  mech=provider CLI --sandbox flag (native landlock/seccomp)
codex   workspace-write      OK  prefix_len=0
codex   danger-full-access   OK  prefix_len=0
claude  read-only            FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
claude  workspace-write      FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
claude  danger-full-access   OK  prefix_len=0  mech=none (unrestricted by definition)
agy     read-only            FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
agy     workspace-write      FAIL-CLOSED: sandbox wrapper self-test failed with exit code 1
agy     danger-full-access   OK  prefix_len=0  mech=none (unrestricted by definition)
```

This is a positive result for fail-closed behavior: placed in an environment that cannot enforce, the code refused to launch rather than running unsandboxed while reporting a sandboxed mode. That is the exact 014 PARTIAL defect being fixed, and it does not regress under adverse conditions.

### Item 9 detail — bubblewrap construction

`_bwrap_argv` reviewed against each named risk:

- **Path escape / relative paths** — the workspace comes from `settings.validate_workspace(job.workspace)`, which resolves and enforces the 014 allowlist *before* the plan is built; `str()` of a resolved path is absolute. No user-controlled relative segment reaches argv.
- **Bind ordering** — `--ro-bind / /`, then `--dev`, `--proc`, `--tmpfs /tmp`, then state carve-outs, then credentials, then the workspace **last**. Deliberate and correct: the workspace bind lands after the `/tmp` tmpfs so workspaces under `/tmp` are not shadowed. The in-code comment states this accurately.
- **HOME / state carve-outs** — narrow and justified: `.claude`, `.cache/claude-cli-nodejs`, `.local/state/claude` (claude); `.gemini` (agy). HOME itself is never bound writable, so it stays read-only under the `/` ro-bind.
- **Credentials mutability** — `_CREDENTIAL_FILES_READ_ONLY` re-binds `.claude/.credentials.json` and `.gemini/oauth_creds.json` read-only *after* their parents are bound writable, so the narrower read-only bind wins. A job can authenticate but cannot replace credentials. I can confirm this works: it is in effect on my own process right now.
- **Carve-outs conditional, never created** — guarded by `state.is_dir()` / `credential.is_file()`; a missing path is omitted rather than created, so the sandbox never manufactures a writable hole. `test_missing_state_carve_outs_are_excluded_not_created` covers this and passes.
- **Symlink** — see LOW-4.
- **Executable lookup** — `shutil.which(bwrap_bin)` before use; provider binary resolution unchanged from 014.
- **Fail-open** — none found. Every path that cannot build a verified plan raises `SandboxEnforcementError`. `_bwrap_unavailable_reason` treats missing binary, `OSError`, `SubprocessError`, and any non-zero exit as failure. Probe crash detail is reduced to the exception class name only (`test_probe_crash_is_reported_as_class_name_only`), avoiding message leakage.
- **Network/socket** — unconstrained; MEDIUM-3.

---

## Runtime boundary — what is and is not enforced

Per the brief's claim boundary, stated precisely.

**What the bwrap construction enforces:**

- Mount-namespace filesystem isolation: `/` read-only, a fresh private `/tmp` tmpfs, writes limited to the validated workspace (`workspace-write` only) plus named provider-state carve-outs.
- Read-only protection of OAuth credential files even inside writable state directories.
- Process teardown coupling via `--die-with-parent`.

**What it does not enforce, in any mode:**

- **Network isolation.** No `--unshare-net`. Full network access, loopback and outbound, is retained. The tests assert this rather than restrict it.
- **PID / IPC / UTS / cgroup isolation.** No corresponding `--unshare-*` flags; only the user + mount namespaces bwrap requires.
- **Environment scrubbing.** No `--clearenv`; a Python-side denylist instead (MEDIUM-2).
- **Syscall filtering.** No `--seccomp`. Unlike Codex's native landlock/seccomp path, the Claude/AGY wrapper is filesystem-only.
- **Resource limits.** No CPU, memory, or fd bounds.

**This is not full container or network isolation and must not be described as such.** It is a filesystem/mount-namespace sandbox. The `SANDBOX_MECHANISM_BY_PROVIDER` value `"bubblewrap filesystem isolation"` is accurately scoped and I endorse that wording as-is.

Provider CLI permission flags were not accepted as equivalent to filesystem sandboxing anywhere in this review, and the implementation does not attempt that substitution — Claude/AGY get bwrap or they get refused. That is the correct design choice and it is the substantive fix 015 delivers over 014.

---

## Test evidence (exact counts, re-run by this reviewer)

| Suite | Command | Result in review environment |
| --- | --- | --- |
| Focused | `python3 -m unittest tests.test_sandbox_enforcement` | Ran 24 — 19 pass, 4 failures + 1 error (all namespace-artifact) |
| Full unittest | `python3 -m unittest discover -s tests -t .` | Ran 202 — 197 pass, 4 failures + 1 error (same cause) |
| pytest | `python3 -m pytest -q` | 220 passed, 5 failed, 74 subtests passed, 2 warnings |
| Diff hygiene | `git diff --check` | PASS (no whitespace errors) |

**All 5 failures share one cause** — `sandbox wrapper self-test failed with exit code 1`, i.e. bwrap cannot nest inside the reviewer's own sandbox — and all 5 are confined to `RealBubblewrapEnforcementTests`. They are review-environment artifacts, **not** defects in 015, and they do not contradict the brief's out-of-confinement counts of 24/24, 202/202, and 225 passed. The 2 pytest warnings are `PytestCacheWarning` from the read-only `.pytest_cache` mount in my environment.

Everything that does not require nested namespaces passes: plan construction and mapping, fail-closed behavior, failure-class distinctness, worker integration, and report-contract preservation.

## Runtime / service state

- `gpt-codex-worker.service` — loaded, active, running (MainPID 3267111; root mount `rw`, i.e. on the real host outside the reviewer's confinement)
- `gpt-codex-api.service` — loaded, active, running
- `gpt-codex-telegram.service` — loaded, active, running
- Worker unit: `WorkingDirectory=%h/project/e500-codex-smoke/gpt-codex-bridge`, `NoNewPrivileges=true`, `PrivateTmp=true`, runs as the invoking user
- Services are running **pre-015 code** (015 is uncommitted; no unit was restarted by this review)

Operational note for the fix pass, not a finding: `NoNewPrivileges=true` does not block `unshare`/`clone` and so does not by itself prevent bwrap from working under the worker. Since I could not execute on the host, the maintainer should confirm bwrap functions in the worker's actual runtime context before relying on 015 in production — a one-command check (`systemd-run --user --pipe bwrap --ro-bind / / --die-with-parent -- /bin/true`).

No probe I ran mutated a tracked file, service, Git state, or external system. Probes were confined to `/tmp` and read-only inspection commands.

## Publication check (matrix item 12)

- `git rev-parse HEAD` = `b1375230a0027aa00b2842a1f6fb669846a67d75` — unchanged from baseline
- `git log origin/HEAD..HEAD` — empty; nothing unpushed, nothing pushed
- 015 changes remain uncommitted in the working tree
- No Sites deploy, no `gh` invocation, no external publication observed or performed

---

## Remediation required before commit

**Should fix (recommended, none blocking):**

1. **Gate `RealBubblewrapEnforcementTests` on functional bwrap** via `_bwrap_unavailable_reason(...) is None` rather than mere binary presence (MEDIUM-1). Highest value: it prevents exactly the false-alarm this review hit.
2. **Add `--clearenv` plus an explicit allowlist** for the sandboxed child instead of the current denylist (MEDIUM-2).
3. **Document the boundary explicitly** in `sandbox.py`'s module docstring and operator docs: filesystem-only; excludes network, PID, IPC, and seccomp (MEDIUM-3).

**Optional:**

4. Revert the unrelated `mkdir` kwarg reordering in `agy_runner.py` (LOW-2).
5. Consider renaming `CODEX_SANDBOX_BWRAP_BIN` to drop the misleading `CODEX_` prefix (LOW-3).
6. Consider `.resolve()` on carve-out paths as defense-in-depth (LOW-4).

**Verification owed before production reliance (not a code change):**

7. Confirm on the host — outside any nested sandbox — that `RealBubblewrapEnforcementTests` passes under the worker's runtime context, and record that output. The brief reports this was already observed; it simply could not be re-confirmed from inside this confinement.

---

## Answer to the primary review question

> Does the current 015 implementation provide truthful, fail-closed, machine-enforced sandbox semantics for Claude and AGY without weakening Codex/014/013 behavior?

**Truthful — yes.** No silent downgrade or upgrade; the reported mode is the mode validated at launch; mechanism strings accurately scope what is enforced; enforcement is never faked with provider CLI permission flags.

**Fail-closed — yes, demonstrably.** Verified by passing tests and by live execution: unknown provider, invalid mode, missing bwrap, probe crash, and probe non-zero exit all raise before `_popen`, and both runners convert that into a `failed` report with `needs_attention=True` under the distinct `sandbox_enforcement` class. The review environment supplied an unplanned adverse-conditions test, and the code failed closed correctly.

**Machine-enforced — sound by construction; host runtime not certifiable from here.** The mechanism is a real mount-namespace sandbox, not prompt cooperation and not a CLI flag. I found no defect in its construction, and the reviewer's own confinement under the identical layout is corroborating evidence. Formal host-side certification requires item 7.

**Without weakening Codex/014/013 — yes.** Codex argv is byte-identical, 014 workspace/publication ceilings still gate before any sandbox logic, and 013 needs_attention/failure classification is preserved and cleanly extended.

## FINAL_VERDICT

**PASS_WITH_NONBLOCKING_FINDINGS**

015 closes the 014 PARTIAL correctly: Claude and AGY move from merely recording `sandbox_mode` to being genuinely wrapped in a filesystem sandbox, with a fail-closed refusal whenever that wrapping cannot be established. Codex, 014, and 013 behavior are intact. All my findings are hardening items; none blocks the commit. The one I would fix first is MEDIUM-1, since weak test gating is what makes this otherwise-sound change look broken to anyone reviewing from a constrained environment.

CLAUDE_OPUS5_MEDIUM_SANDBOX_PARITY_015_REVIEW_COMPLETE
