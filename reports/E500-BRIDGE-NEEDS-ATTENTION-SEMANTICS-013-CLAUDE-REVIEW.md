# E500-BRIDGE-NEEDS-ATTENTION-SEMANTICS-013 — Claude Opus 5 Final Acceptance Review

Provenance
----------
- Reviewer: Claude Code CLI, `claude-opus-5`, effort `medium`, non-interactive.
- Executed as a durable bridge job (not a detached runner): `job-5673268a0ace4457`,
  `POST /run` `provider=claude` `mode=read` → `read-only`.
- Job outcome: `status=success`, `needs_attention=false`, `exit_code=0`,
  report `~/.local/state/gpt-codex-bridge/reports/job-5673268a0ace4457.json` (9443 chars).
- End marker `CLAUDE_OPUS5_MEDIUM_NEEDS_ATTENTION_SEMANTICS_READY` verified present.
- Reviewed subject: `reports/E500-BRIDGE-NEEDS-ATTENTION-SEMANTICS-013-AGY.md` and the
  working-tree implementation.
- NOTE: the verdict below was rendered against the code **before** the MEDIUM finding was
  remediated. See "Post-review remediation" at the end of this file.

Reviewer output, verbatim:
---------------------------

HEAD does contain `reconcile_failed_workflow`, so the bulk of the queue.py diff is
uncommitted work from prior tasks (011/012), not 013. The mtime evidence remains the
reliable attribution for 013's scope, and the sole 013 hunk in queue.py is the
`stage_error` change I verified.

All items verified. Here is my final assessment.

VERDICT: SUCCEEDED_SEMANTICS_UNCHANGED: PASS
VERDICT: FAILURE_CLASSIFICATION_CORRECT: PASS_WITH_FINDING
VERDICT: NEEDS_ATTENTION_NOT_MASKING_CRASH: PASS
VERDICT: ERROR_REDACTION_AND_BOUND: PASS
VERDICT: WORKFLOW_ERROR_PATHS_AGREE: PASS
VERDICT: CONTRACT_SCHEMA_VALIDATOR_PARITY: PASS
VERDICT: SANDBOX_MODE_INTERPOLATION: PASS
VERDICT: AGY_INVALID_OUTPUT_CLASSIFIED: PASS
VERDICT: FOCUSED_TESTS: PASS
VERDICT: FULL_TESTS: PASS
VERDICT: PRODUCTION_SPECIMEN_AFTER_STATE: PASS
VERDICT: NO_REGRESSION_READ_ONLY: PASS
VERDICT: CONTAINMENT_AND_SCOPE: PASS_WITH_FINDING
VERDICT: TEST_QUALITY: PASS_WITH_FINDING

FINAL_VERDICT: PASS_WITH_NONBLOCKING_FINDINGS

OBSERVED EVIDENCE PER ITEM

A. succeeded / precedence. codex_runner.py:104-110 — `succeeded` is exit 0 AND status in
{success,partial} AND not needs_attention, unmodified. `failure_reason`
(codex_runner.py:113-132) orders succeeded -> timed_out -> exit!=0 -> explicit ->
needs_attention -> agent_reported. I executed the matrix directly rather than trusting the
tests: exit 3 + status success + needs_attention=true -> exit_nonzero; exit -15 + partial +
NA -> exit_nonzero; exit 0 + NA + explicit invalid -> invalid_report; timeout + exit 0 + NA
-> timeout. needs_attention is unreachable whenever exit_code != 0 or the report was invalid.
Precedence is safe.

B. Redaction and bound. All three providers redact the summary before it lands in
`RunOutcome`: Codex `_parse_report_payload` (codex_runner.py:319-323) applies `_redact` +
`Settings.redact_text`; AGY `_report` (agy_runner.py:141) and Claude `_report`
(claude_runner.py:100) apply `redact_text` at construction, and Claude additionally redacts
stdout/stderr at claude_runner.py:161,169-171. `describe_failure` reads only
`outcome.report["summary"]`, never stdout or the exception text. `_failure_detail`
(codex_runner.py:135-145) takes the first non-empty line and truncates to 300 chars, so
newlines cannot break the field. Measured worst case: 50k-char summary -> 346-char message;
non-string summary -> no detail, no crash; empty report dict -> no crash.

C. Workflow error shape. Read both paths, not the tests. queue.py:956-958 (reconcile) and
queue.py:1067-1069 (advance) now build `f"{stage} stage failed"` plus `f": {error}"` when the
terminal job error is non-empty — character-identical construction.

D. Schema freeze. REPORT_CONTRACT (codex_runner.py:51-58),
schemas/codex_report.schema.json and `validate_report` (codex_runner.py:168-207) all carry
the same 7 fields, same enums. The 012 parity test at tests/test_codex_runner.py:572-579
still compares the schema's `required` and `properties` key sets against a live report
fixture and asserts `additionalProperties` false at both levels — not weakened.

E. Interpolation. `report_contract` (codex_runner.py:62-72) routes the mode through
`validate_sandbox_mode` before substitution. I probed it:
`"read-only\nIGNORE ALL PRIOR"`, `"__JOB_SANDBOX_MODE__"`, `""`, `None` and
`"workspace-write "` are all rejected by SandboxModeError/TypeError. The sentinel appears
exactly once in the template and never survives rendering for any of the three legal modes.
No arbitrary text can enter the prompt.

F. Tests, run by me:
- `python3 -m unittest discover -s tests -p 'test_*.py'` -> Ran 124 tests, OK.
- `python3 -m pytest -q` -> 147 passed, 26 subtests passed.
Both match the claim exactly.

G. Test quality. Tests are largely substantive, not green-by-construction. The truth table
(test_codex_runner.py:337-363) pins all 9 succeeded rows; the parity test
(test_queue.py:530-578) builds two real SQLite queues and asserts
`advanced.error == reconciled.error` rather than asserting a literal; the end-to-end tests
drive `CodexRunner.run` through a fake Popen and assert exact message text plus sidecar
absence. The specific probe requested — nonzero exit carrying needs_attention=true
misreported as benign — cannot occur; I confirmed it both by reading the precedence and by
executing it.

H. Production specimen. Live rows read read-only. job-ed4bbff7dba445ee: exit_code 0, error =
"Codex completed but flagged needs_attention: Blocked: the required bridge checkout and
reports directory are outside this job's workspace-write sandbox…", length exactly 345. Its
persisted report has the full 7 fields, status=partial, needs_attention=true,
sandbox_mode=workspace-write. flow-d47d7f6bb3614992.error = "gpt stage failed: Codex
completed but flagged needs_attention: …" — the job reason is retained. No
job-ed4bbff7dba445ee.raw.txt exists in the reports directory, so the 012 observability path
correctly did not fire. Pre-fix job-0fd43861826e434b: exit 0, error = "Codex job failed";
flow-3de5535c3f4946d8.error = "gpt stage failed". The A/B is genuine.

I. Containment. flow-d47d7f6bb3614992 external_publication_enabled = 0.
job-c2fa2482d303490f: read-only, status succeeded, exit 0, error empty.
`web/e500-control-plane` `git write-tree` = 04545a202f9a7acdfbd5a52054550f6ddf502fca,
unchanged. Live .env still has CODEX_ALLOWED_WORKSPACES and CODEX_DEFAULT_WORKSPACE both set
to only ai-meeting-room — no allowlist widened. All three user units report active; /health
returns {"status":"ok"}.

FINDINGS

MEDIUM — codex_runner.py:126-127 — `failure_reason` returns `self.explicit_failure` verbatim
without checking membership in `FAILURE_CLASSES`, so the "closed enum" is only closed by
convention. I confirmed by construction that
`RunOutcome(..., explicit_failure="arbitrary; DROP").failure_reason` returns
`"arbitrary; DROP"`, and `describe_failure` then silently falls through to the
agent_reported branch, mislabelling the cause. This is unreachable today — grep shows the
only two writers (codex_runner.py:505, agy_runner.py:245) both assign the
`FAILURE_INVALID_REPORT` constant — so there is no live defect. It matters because the spec
required a closed enum and a future runner adding a third writer gets no guard. Minimum fix:
validate in `__post_init__` or coerce unknown values to `FAILURE_AGENT_REPORTED`.

LOW — tests/test_codex_runner.py:315 — `assertNotIn("Codex job failed", messages.values())`
tests membership against a dict-values view, so it only detects a message *exactly equal* to
the old string, never one merely containing it. It passes regardless of implementation detail
and is close to a tautology. The distinctness assertion on line 314 carries the real weight,
so coverage is not lost; the assertion is just weaker than it reads.

LOW — tests/test_codex_runner.py — no end-to-end test drives `CodexRunner.run` with a
nonzero exit *and* a report carrying needs_attention=true. The unit-level precedence test at
line 237 covers the logic, and I verified the behaviour empirically, so this is a coverage
gap rather than a defect. Note that `run` at codex_runner.py:489-498 overwrites the report to
status=failed on nonzero exit anyway, giving a second independent barrier.

LOW — codex_runner.py:404-408 — `run` still inlines its own `f"{job.id}.raw.txt"` unlink
instead of calling `_clear_raw_diagnostic`, duplicating the path construction. Self-reported
by the implementer; no behavioural impact.

LOW — spec item, runtime acceptance 5 — the spec required
`github_status=skipped_no_external_write`, but both workflows show `github_status` as an
empty string. Since `external_publication_enabled=0` and each chain halted at the gpt stage
before any publish decision, nothing was published; the literal spec string was simply never
written. Containment is intact; the acceptance wording was not met verbatim.

SHOULD 013 CLOSE

Yes. Every required fix (A, B, C) is implemented and independently verified against source,
the live database and executed probes, not against the report's claims. The three enumerated
013 defects — indistinguishable failure classes, the dropped job reason in
`advance_workflow`, and the hardcoded contract sandbox_mode — are each genuinely fixed, and
the fail-closed `succeeded` boundary is byte-for-byte intact. No finding above is blocking. I
would carry the MEDIUM enum-closure item into a follow-up rather than reopening 013.

SCOPE CREEP

None attributable to 013. Mtime evidence (2026-08-29 13:42–13:48) isolates 013's edits to
exactly the seven claimed files: bridge/{codex_runner,worker,queue,agy_runner}.py and
tests/{test_codex_runner,test_queue,test_worker}.py. `git diff --stat` appears far larger —
22 files, including api.py, models.py, telegram.py, claude_runner.py, observability.py and
web/ — but those carry mtimes from 2026-08-25 through 2026-08-28 and are uncommitted residue
from tasks 011/012; HEAD predates them. The 537-line queue.py diff is likewise mostly
prior-task work: I confirmed HEAD already contains `reconcile_failed_workflow`, and 013's
only queue.py contribution is the three-line `stage_error` hunk. One caveat worth stating
plainly: because none of this is committed, file mtimes are my only attribution mechanism, so
I cannot cryptographically prove 013 did not also touch an earlier-dated file — I can only
report that no such evidence appeared. No sandbox or workspace boundary was widened.

CLAUDE_OPUS5_MEDIUM_NEEDS_ATTENTION_SEMANTICS_READY

Post-review remediation
-----------------------

The reviewer recommended carrying the MEDIUM into a follow-up. It was not deferred: 013's own
spec required a *closed* enum, so leaving it unenforced would have made the task's acceptance
claim false. Fixed inside 013 before closing.

1. MEDIUM — closed the enum at construction. `RunOutcome.__post_init__` now rejects an
   `explicit_failure` that is not a member of `FAILURE_CLASSES`, and separately rejects
   `FAILURE_NONE` as an explicit cause (it is a valid `failure_reason` output but would let a
   runner claim "not a failure" while `succeeded` is False). Fail-loud at construction rather
   than mislabelling at read time. Both writers continue to pass `FAILURE_INVALID_REPORT`.
   Regression test: `test_explicit_failure_must_be_a_declared_class`.
2. LOW (test tautology) — replaced the vacuous
   `assertNotIn("Codex job failed", messages.values())` with a loop asserting no classified
   message contains the generic substring at all.
3. LOW (coverage gap) — added
   `test_nonzero_exit_with_needs_attention_report_is_not_benign`, driving `CodexRunner.run`
   end-to-end with exit `-15` and a `needs_attention=true` report, asserting `exit_nonzero`.

Two findings were intentionally not acted on, both outside 013's declared scope:

- The duplicated `{job.id}.raw.txt` unlink in `CodexRunner.run` stays as a known LOW. It is
  behaviour-neutral path-construction duplication; touching it would add unrelated churn to a
  diff the reviewer had already cleanly attributed by mtime.
- The `github_status` wording gap is a defect in **my acceptance spec**, not in the code. The
  reviewer is right that the chain halts at the gpt stage and therefore never reaches the
  publish decision that writes `skipped_no_external_write`. Containment was proven by
  `external_publication_enabled = 0` instead. Future specs should assert containment on that
  column plus absence of any publish call, not on a stage-specific string.

One reviewer statement is imprecise and is recorded here rather than silently corrected: it
reports `/health` returning `{"status":"ok"}`. The actual contract, verified directly, is
`{"ok": true, "service": "codex-bridge", "api": "v1", ...}`. This does not affect any verdict.

Re-verification after remediation
---------------------------------
- `python3 -m unittest discover -s tests -p 'test_*.py'` → **Ran 126 tests, OK**
- `python3 -m pytest -q` → **149 passed, 26 subtests passed**
- `gpt-codex-worker`, `gpt-codex-api`, `gpt-codex-telegram` restarted → all `active`;
  `/health` → 200, `service=codex-bridge`, `api=v1`.
- Post-restart read-only regression job `job-b1e0439717f64bad` → `succeeded`, exit 0, no error.

013 CLOSED: PASS_WITH_NONBLOCKING_FINDINGS, with the single MEDIUM remediated before closure.
