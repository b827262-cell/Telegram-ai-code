# E500-GPT-LOOP-STRUCTURED-REPORT-FIX-001 — LUNA

## CODEX EXECUTION REPORT

### WORKSPACE

`/home/b827262/project/e500-codex-smoke/gpt-codex-bridge`

Task: `E500-GPT-LOOP-STRUCTURED-REPORT-FIX-001`

### ROOT_CAUSE

The blocker was a report transport mismatch, not an invalid Codex task result.

The bridge invoked the current CLI with `codex exec`, `--output-schema`, and
`-o <runtime-report-path>`, then discarded stdout and treated the `-o` file as
the only report source. Codex CLI `0.150.1` documents stdout as the final agent
message and `-o/--output-last-message` as a file containing that message; the
schema option constrains the final response. See the [CLI source for the
0.150.1 release](https://raw.githubusercontent.com/openai/codex/rust-v0.150.1/codex-rs/exec/src/lib.rs)
and [non-interactive Codex documentation](https://github.com/ai-native-engineer/openai-mirror/blob/main/developers.openai.com/codex/noninteractive.md).

For both observed jobs, the read-only SQLite evidence showed `exit_code=0`,
failure at the `gpt` stage, and the fallback bridge report:

- `flow-00200f1d5b544dfc` / `job-5d3a161078944a74`
- `flow-4c68012a97214f33` / `job-e120fa85458d4217`

The matching Codex rollout records show a completed `task_complete` event whose
`last_agent_message` was a valid JSON object with exactly the report fields
required by `schemas/codex_report.schema.json`. The child’s enclosing filesystem
policy allowed the selected workspace and `/tmp`, but not the private runtime
report directory. Consequently, the CLI could complete the task and exit 0
without leaving the `-o` artifact. Because the bridge had set stdout to
`DEVNULL`, it threw away the valid structured result, generated
`Codex did not produce a valid structured report`, and the worker correctly
stopped workflow advancement at `gpt`.

### ACTIONS

- Kept the existing fixed argv, explicit per-job sandbox, shell-free launch,
  secret-filtered environment, strict schema validation, redaction, and
  fail-closed fallback.
- Made the `-o` artifact authoritative when present and valid.
- Captured stdout and added a narrowly scoped compatibility path: only one
  complete UTF-8 JSON object is considered; it must pass the same report schema,
  redaction, and job `sandbox_mode` normalization. Prose, JSONL, markdown,
  oversized output, malformed JSON, and sandbox mismatches still fail closed.
- Did not change the schema, queue, worker advancement rules, credentials,
  API tokens, SQLite state, or historical runtime reports.

### CHANGED_FILES

- `gpt-codex-bridge/bridge/codex_runner.py` — capture and strictly validate
  stdout when the CLI output artifact is unavailable.
- `gpt-codex-bridge/tests/test_codex_runner.py` — regression coverage for the
  current file path, strict stdout compatibility, malformed/missing output,
  schema shape, sandbox normalization/mismatch, redaction, and attention state.
- `gpt-codex-bridge/README.md` — documents the artifact-first/strict-stdout
  report contract. Existing unrelated README changes were preserved.

`gpt-codex-bridge/schemas/codex_report.schema.json` was inspected and remains
unchanged because it already expresses the required strict contract.

### TESTS

- `python3 -m unittest tests.test_codex_runner -v` — **12 passed**.
- Explicit broader bridge suite excluding the socket-binding API test —
  **92 passed**.
- `python3 -m unittest discover -s tests -p 'test_*.py' -v` — **93 tests;
  92 passed, 1 environment error**. The only error is
  `tests/test_api.py::test_only_queued_workflow_can_be_cancelled_over_authenticated_http`,
  where this managed environment rejects binding `127.0.0.1:0` with
  `PermissionError: [Errno 1] Operation not permitted`.
- `python3 -m compileall -q bridge adapters tests` — passed.
- `bash -n` over executable files in `bin/` and `scripts/` — passed.
- `git diff --check` — passed.
- Safe direct current-CLI probe with secrets removed, `--ephemeral`,
  `--sandbox read-only`, and a harmless read-only prompt — exited before model
  execution with `failed to initialize in-process app-server client:
  Read-only file system`; no probe report was created.

### GIT_DIFF

No reset, clean, checkout, rebase, commit, push, deploy, or runtime-state move
was performed. The task diff is limited to the three source/test/documentation
files above. Pre-existing modified and untracked work in the enclosing
workspace was preserved. The two historical runtime reports were read-only
inspected and not changed or copied into Git.

### ERRORS

- Full-suite API socket test: blocked by the execution sandbox’s network/socket
  policy, as described above; this is not a bridge assertion failure.
- Live Codex/HTTP workflow E2E: unavailable in this environment. The user
  systemd bus reported `Failed to connect to bus: No medium found`, no worker
  process was running, and the direct CLI probe failed during read-only app
  server initialization. Therefore no claim is made that a live submission
  advanced through AGY, Claude, and the final report stage.

### REMAINING_ISSUES

- The live service still needs verification in an environment where the user
  service and Codex CLI can start. The safe source-change restart target is
  **only** `gpt-codex-worker.service`; no restart was performed here. After
  confirming the queue is safe to resume, use:
  `systemctl --user restart gpt-codex-worker.service`.
- The observed local Codex configuration selected `gpt-5.6-sol` with medium
  reasoning effort. This fix intentionally does not alter model selection or
  user configuration; the task’s LUNA/xhigh intent remains an execution
  configuration concern.
- Strict failure is intentional when neither a valid artifact nor one complete
  schema-valid stdout JSON object is available.

### RESULT

The reproducible exit-0 structured-report blocker is fixed at the bridge report
transport layer with strict compatibility handling and regression coverage.
Focused and non-socket broader tests pass. Live terminal workflow success
remains unverified because of the current service, filesystem, and socket
constraints.
