<!-- E500-CODEX-REPORT-CONTRACT-HARDEN-012 -->
<!-- Reviewer: Claude Opus 5 (medium), run durably via Bridge POST /run provider=claude -->
<!-- Bridge job id: job-66db479622144bdf | job report: job-66db479622144bdf.json -->
<!-- Post-fix production smoke verified: flow-91ad5bc1e3194892 -->

Operator claim 5 confirmed: `claude_runner.py:105` only records `sandbox_mode` into the report — no CLI enforcement flags. All verification complete.

---

## E500-CODEX-REPORT-CONTRACT-HARDEN-012 — Independent Final Acceptance Review

```
ROOT_CAUSE_A_REMEDIATED:      PASS
PROMPT_REPORT_CONTRACT:       PASS
RAW_OUTPUT_OBSERVABILITY:     PASS
SANDBOX_MODE_HANDLING:        PASS_WITH_FINDING
FOCUSED_TESTS:                PASS
FULL_TESTS:                   PASS
PRODUCTION_BARE_PROMPT_SMOKE: PASS
NO_EXTERNAL_WRITE_CONTAINMENT:PASS
LOCAL_V19_EXACT_PARITY:       PASS
FINAL_VERDICT:                PASS_WITH_NONBLOCKING_FINDINGS
```

### Operator re-verification items — verdicts

**1. TIMELINE GAP — CONFIRMED, closed.** `codex_runner.py` mtime `10:40:20`; original `flow-17ef76e65a9c4e82` ran `02:35:35Z` = 10:35 local, i.e. **pre-fix**. The new `flow-91ad5bc1e3194892` ran `03:17:22Z`→`03:19:53Z` = 11:17 local, **post-restart**. DB confirms: `status=succeeded`, all three stages (codex `job-8feedea885634ac1`, agy `job-5cfb77c7ac7343c6`, claude `job-52dca7ac572843af`) succeeded, `external_publication_enabled=0`, `github_status=skipped_no_external_write`, `github_url` empty. Prompt is bare natural language, no JSON/schema/report instructions. The codex report is schema-perfect with a populated `tests` array — evidence the injected contract worked on a bare prompt. **The AGY report's citation of the pre-fix flow was a material evidentiary gap; the operator's new flow closes it.**

**2. SECOND ROOT CAUSE — CONFIRMED. This is the most important finding.** `job-ea807c8537d8403e` holds a structurally **valid** report: `status=success`, `needs_attention=true`. `RunOutcome.succeeded` (`codex_runner.py:68-73`) returns `False` on `needs_attention`, so the workflow died with "gpt stage failed". By contrast `job-46ca690f21804b1c` is the genuine parse failure with the `"Codex did not produce a valid structured report"` fallback. **The REPORT_CONTRACT remediation addresses only class (b) — malformed output. Class (a) — valid report, self-flagged `needs_attention=true` on a read-only inspection task — remains fully open.** Note the contract text tells the model when to *set* the flag ("true only if human intervention is required") but nothing tells it that setting it **fails the entire workflow**. A model doing a read-only inspection that notices 19 dirty files may reasonably flag attention and unknowingly kill the flow. Non-blocking for this task's stated scope, but it is a real second failure mode and the harden-012 title implies coverage it does not have.

**3. ZERO PRODUCTION SIDECAR INSTANCES — CONFIRMED.** `find ... -name "*.raw.txt"` returns empty. Fix B rests entirely on unit tests. **I judge this sufficient for acceptance.** Fix B is a diagnostic-only path that cannot alter success/failure outcomes — the fallback report is byte-identical to pre-fix behavior. The unit tests cover redaction, the exact `MAX_RAW_DIAGNOSTIC_BYTES` bound, `0o600`, and stale-sidecar clearing on a valid re-run. Demanding a production instance would require deliberately inducing a failure. Absence of instances is also positive evidence the contract is working.

**4. HARDCODED `"workspace-write"` EXAMPLE — CONFIRMED, low materiality.** `command_for` never injects the job's actual mode; the example object always shows `workspace-write`. Harmless because `_report_for_job` now **overwrites** the reported value with the argv-authoritative job mode, so a read-only job whose report parrots `workspace-write` is normalized correctly and merely logs a warning. The residual cost: read-only and danger-full-access jobs will emit a spurious discrepancy WARNING per run, which dilutes the signal that warning exists to carry. Cosmetic/observability, not correctness.

**5. CLAUDE SANDBOX NON-ENFORCEMENT — CONFIRMED.** `ClaudeRunner.command_for` (`claude_runner.py:42-50`) emits exactly `[claude, -p, prompt, --model, --effort]`. No `--permission-mode`, no `--allowedTools`, no sandbox translation; `sandbox_mode` appears only at line 105, written into the report. **This does weaken any claim that a Claude stage runs under a technically enforced read-only boundary — the boundary is prompt-instructed, not sandbox-enforced.** For the review just performed: the read-only discipline was honored behaviorally (I ran only reads, tests in temp dirs, and GET/invalid-POST HTTP probes), but it was not machine-guaranteed. This is a pre-existing architectural gap, out of scope for harden-012, and worth its own task.

### Findings by severity

**MEDIUM — `needs_attention=true` fails workflows with no operator-visible explanation.** (`bridge/codex_runner.py:68-73`) Class (a) failure mode is untouched by this change and will recur. The `.raw.txt` sidecar does **not** fire for it either — the report parsed fine — so the new observability gives zero diagnostic help for this class. Recommend a follow-up: either surface a distinct error string (`"agent flagged needs_attention"` rather than the generic `"Codex job failed"`) or add contract language stating the operational consequence of the flag.

**LOW — hardcoded `sandbox_mode` example produces spurious warnings.** (`bridge/codex_runner.py`, REPORT_CONTRACT) Interpolate the job's actual mode into the example to remove predictable false-positive discrepancy logs on non-`workspace-write` jobs.

**LOW — duplicated sidecar-unlink logic.** `run()` inlines `raw_path = ... / f"{job.id}.raw.txt"` + `unlink(missing_ok=True)` while `_clear_raw_diagnostic()`/`_raw_diagnostic_path()` exist for exactly that. Third literal spelling of the same path. Cosmetic.

**INFO — AGY report cited a pre-fix flow as post-fix evidence.** Corrected by the operator's `flow-91ad5bc1e3194892`. Worth noting as a process finding: the AGY narrative asserted end-to-end verification against code that had not yet been deployed at run time.

### Verified independently
- Diff matches AGY narrative; no undisclosed changes in `codex_runner.py`.
- REPORT_CONTRACT field/enum set is an **exact** match to `validate_report` (`codex_runner.py:76-115`), including the strict `set(report) != required` equality and the `tests` sub-object shape.
- Raw path: collects report-artifact + stdout evidence **before** fallback overwrite, redacts via `settings.secret_values`, bounds at 16384 bytes, best-effort `0600`, atomic `os.replace`, warning logs only the redacted reason and exception class name — no raw secret.
- Focused tests: **17 passed, 11 subtests**. Full suite: **133 passed, 17 subtests**, 4.76s. No source files modified.
- Production health `HTTP=200` (`bridgeConnected:true`, `BRIDGE_READY`); invalid `noExternalWrite` → `HTTP=400 NO_EXTERNAL_WRITE_INVALID`. Both read-only.
- `git write-tree` in `web/e500-control-plane` = `04545a202f9a7acdfbd5a52054550f6ddf502fca` — exact V19 match, `git diff` empty (no unstaged drift), no untracked files.

### Closure recommendation

**Accept and close harden-012.** Root cause A is remediated and now proven against genuinely post-fix production code. Fixes A and B are correct, tested, fail-closed, and containment plus V19 parity are intact. No blocking defect.

Open two follow-up tasks rather than reopening this one: (1) the `needs_attention` workflow-failure class, which is the higher-value remaining gap; (2) Claude/AGY sandbox enforcement parity with Codex. Scope this task's claim precisely: it hardens the **malformed-report** failure class, not all Codex stage failures.

CLAUDE_OPUS5_MEDIUM_CODEX_REPORT_HARDEN_FINAL_READY
