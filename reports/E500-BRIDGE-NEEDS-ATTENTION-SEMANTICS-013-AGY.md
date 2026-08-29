# E500-BRIDGE-NEEDS-ATTENTION-SEMANTICS-013 — Implementation Report

Route: **direct implementation** in `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge`.
The durable `/workflow` chain was used only to *verify*, never to implement — see
"Structural constraint" below. No workspace or sandbox boundary was widened.

Predecessor: `E500-CODEX-REPORT-CONTRACT-HARDEN-012` (closed
`PASS_WITH_NONBLOCKING_FINDINGS`, see
`reports/E500-CODEX-REPORT-CONTRACT-HARDEN-012-CLAUDE-REVIEW.md`).

---

## Problem

012 hardened only the **malformed-report** class. A second class stayed indistinguishable:

```text
valid structured report + status in {success, partial} + needs_attention=true
    -> RunOutcome.succeeded == False          (bridge/codex_runner.py)
    -> job.error       == "Codex job failed"  (bridge/worker.py)
    -> workflow.error  == "gpt stage failed"  (bridge/queue.py advance_workflow)
```

Timeout, CLI crash, unparseable report and "agent finished but asked for a human" all
collapsed into one string.

Secondary defect fixed in the same task: two workflow-failure transitions produced
different error shapes for the same event. `reconcile_failed_workflow` appended the
terminal job error (`"gpt stage failed: <reason>"`), `advance_workflow` dropped it.

---

## Structural constraint discovered (not fixed here, by design)

`CODEX_ALLOWED_WORKSPACES` contains only `ai-meeting-room`, and both entry points pin the
workspace to the configured default with no per-request override:

- `bridge/api.py:314` — `workspace = settings.validate_workspace(settings.default_workspace)`
- `adapters/telegram.py:399` — `workspace=self.settings.default_workspace`

Therefore a `workspace-write` job dispatched through the chain **cannot** modify the bridge
repository. Confirmed live: specimen `job-0fd43861826e434b` (pre-fix) exited 0 with a valid
report and the summary "apply_patch was rejected … outside the writable sandbox", and
`changed_files: []` was honest — no bridge source mtime moved.

This is the sandbox working as designed. Escalating it into a routing change would convert
an error-semantics task into a remote-code-execution surface change, so it is deferred to a
separate task (**014 — durable workspace routing**, server-side alias → allowlist, never an
arbitrary caller-supplied path).

---

## Changes

### 1. `bridge/codex_runner.py` — closed failure classification

```python
FAILURE_NONE = "none"
FAILURE_TIMEOUT = "timeout"
FAILURE_EXIT_NONZERO = "exit_nonzero"
FAILURE_INVALID_REPORT = "invalid_report"
FAILURE_NEEDS_ATTENTION = "needs_attention"
FAILURE_AGENT_REPORTED = "agent_reported_failure"
FAILURE_CLASSES = frozenset({...six above...})
MAX_FAILURE_DETAIL_CHARS = 300
```

`RunOutcome` gained one field, `explicit_failure: str | None = None`, for the one cause only
a runner can know (output that failed both the artifact and the stdout compatibility parse).

`RunOutcome.failure_reason` precedence:

```text
succeeded -> none
timed_out -> timeout
exit != 0 -> exit_nonzero      # a crash can never be relabelled needs_attention
explicit  -> invalid_report
status in {success, partial} and needs_attention -> needs_attention
otherwise -> agent_reported_failure
```

**`RunOutcome.succeeded` was not modified.** It remains `exit 0 AND status in
{success, partial} AND not needs_attention`. Pinned by a new 9-row truth-table regression
test.

`describe_failure(provider, outcome)` builds the operator message from a single shared
implementation used by all providers (Codex, AGY and Claude all import the same
`RunOutcome`), appending a single-line, 300-char-bounded excerpt of the report summary. The
summary is already redacted before it reaches the outcome (`_parse_report_payload` /
`_report` apply `_redact` + `Settings.redact_text`), so no raw stdout or secret material can
enter an error string.

### 2. `bridge/worker.py`

```python
- error=None if outcome.succeeded else f"{job.provider.title()} job failed",
+ error=None if outcome.succeeded else describe_failure(job.provider, outcome),
```

### 3. `bridge/queue.py` — `advance_workflow`

Now appends the terminal job error in exactly the reconciliation shape:

```python
stage_error = f"{completed_job.workflow_stage or 'unknown'} stage failed"
if completed_job.error:
    stage_error += f": {completed_job.error}"
```

### 4. `bridge/agy_runner.py`

The unparsable-JSON branch sets `explicit_failure=FAILURE_INVALID_REPORT`, so AGY output
failures are no longer reported as if the agent itself had declared failure. AGY's
`BLOCKED_AUTH` summary now surfaces in the job error via the shared detail excerpt.

### 5. `REPORT_CONTRACT` language + sandbox interpolation

`needs_attention` requirement replaced with the operational consequence:

> Set true only when a human must intervene before this task can be considered done.
> IMPORTANT: needs_attention=true makes this stage non-successful and stops the automated
> workflow from advancing, even when "status" is "success" or "partial". Do not set it
> merely because unrelated dirty files, pre-existing unrelated failures, or other ambient
> environment conditions exist; describe those in "summary" or "git_status" instead.

The frozen 7-field schema, field names, types and enums are unchanged.

The example value is now rendered per job via `report_contract(sandbox_mode)`, closing the
012 leftover where a hardcoded `"workspace-write"` pushed read-only jobs into self-reporting
a mode they were not launched with and then logging a discrepancy warning.

---

## Tests

| Suite | Result (as reviewed) | Result (post-remediation) |
| --- | --- | --- |
| `python3 -m unittest discover -s tests` | **124 / 124 OK** (baseline 110) | **126 / 126 OK** |
| `python3 -m pytest -q` | **147 passed, 26 subtests** (baseline 133 + 17) | **149 passed, 26 subtests** |

Post-review remediation, the Opus 5 MEDIUM and two test LOWs, is recorded in
`reports/E500-BRIDGE-NEEDS-ATTENTION-SEMANTICS-013-CLAUDE-REVIEW.md`.

New coverage: per-class `failure_reason`, needs_attention requires exit 0 and a valid report,
crash classes outrank the flag, timeout outranks explicit invalid_report, all five failing
classes yield pairwise-distinct messages and none equals the old generic string, detail
excerpt bounded to exactly 300 chars, `succeeded` truth table unchanged, prose output
classified end-to-end through `CodexRunner.run`, valid+needs_attention end-to-end classified
`needs_attention` and produces **no** `.raw.txt` sidecar, contract renders the job's real
mode and never leaks the sentinel token, and `advance_workflow` error equals
`reconcile_failed_workflow` error for an identical job error.

Two 012 contract tests were updated to assert against `report_contract(mode)` rather than the
raw template — expected churn from the interpolation, not a behaviour change.

---

## Runtime acceptance

Services reloaded to keep all three processes on one revision: `gpt-codex-worker`,
`gpt-codex-api`, `gpt-codex-telegram` — all `active`; `/health` → 200
`service=codex-bridge api=v1`.

### Before/after on identical input

The pre-fix run and the post-fix run received the **byte-identical** 013 prompt, so this is a
true A/B. Both hit the same real sandbox wall described above.

| | pre-fix | post-fix |
| --- | --- | --- |
| workflow | `flow-3de5535c3f4946d8` | `flow-d47d7f6bb3614992` |
| codex job | `job-0fd43861826e434b` | `job-ed4bbff7dba445ee` |
| exit code | 0 | 0 |
| report | valid, `status=partial`, `needs_attention=true` | valid, `status=partial`, `needs_attention=true` |
| `job.error` | `Codex job failed` | `Codex completed but flagged needs_attention: Blocked: the required bridge checkout and reports directory are outside this job's workspace-write sandbox, so no patch or requested report could be written. …` |
| `workflow.error` | `gpt stage failed` | `gpt stage failed: Codex completed but flagged needs_attention: …` |

Observed `job.error` length 345 chars = message prefix + 300-char detail bound.
`.raw.txt` sidecar correctly **absent** — the report parsed cleanly, so the 012
observability path must not fire.

### Containment

`flow-d47d7f6bb3614992`: `external_publication_enabled = 0`, `github_status` empty, no
publication attempted (chain stopped at the gpt stage).

### No regression

`job-c2fa2482d303490f` — `POST /run`, `mode=read` → `read-only`, status `succeeded`,
exit 0, `error = (none)`.

### Canonical source untouched

Changed files limited to `bridge/{codex_runner,worker,queue,agy_runner}.py` and
`tests/{test_codex_runner,test_queue,test_worker}.py`. `web/e500-control-plane`
`git write-tree` still `04545a202f9a7acdfbd5a52054550f6ddf502fca` — V19 parity EXACT.

No commit, push, deploy, version save, reset, rebase or clean performed.

---

## Verdict

`RunOutcome.succeeded` semantics are unchanged, so no job or workflow that previously passed
or failed now changes outcome. What changed is only *why* the operator is told it failed.

013 acceptance criteria:

```text
needs_attention distinguished from crash/invalid   MET
job.error actionable and bounded and redacted      MET
workflow.error retains the job reason              MET
both workflow failure transitions agree            MET
REPORT_CONTRACT states the operational consequence MET
sandbox_mode example interpolated per job          MET
focused + full suites green                        MET 124 / 147+26
production specimen reproduces the AFTER state     MET
no workspace or sandbox boundary widened           MET
```

Still open, deliberately out of 013 scope:

- **014 — durable workspace routing** (chain cannot modify the bridge itself; needs
  server-side alias mapping plus per-workspace policy, not an arbitrary path parameter).
- Claude / AGY sandbox enforcement parity with Codex (012 finding, still open).
- LOW: `CodexRunner.run` still inlines its own `f"{job.id}.raw.txt"` unlink instead of
  reusing `_clear_raw_diagnostic`. No behavioural impact.

End marker: AGY_NEEDS_ATTENTION_SEMANTICS_READY
