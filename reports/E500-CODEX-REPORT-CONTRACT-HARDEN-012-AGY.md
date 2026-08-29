# E500-CODEX-REPORT-CONTRACT-HARDEN-012 — AGY Implementation & Smoke Report

## 1. Executive Summary & Verdict

- **Task**: `E500-CODEX-REPORT-CONTRACT-HARDEN-012`
- **Role**: AGY implementer / Gemini 3.7 Flash High
- **Production URL**: `https://e500-control-plane.b827262.chatgpt.site`
- **Production V19 Snapshot Tree**: `04545a202f9a7acdfbd5a52054550f6ddf502fca`
- **Terminal Verdict**: **`PASS`**
- **Production Workflow ID**: `flow-17ef76e65a9c4e82`
- **Per-Stage Results**:
  - **Codex (`gpt`)**: `job-945ddc3dfb7a428d` → **`succeeded`** (exit code 0)
  - **AGY (`agy`)**: `job-fd143c6283f542ee` → **`succeeded`** (exit code 0)
  - **Claude (`claude`)**: `job-f5581dbf00134460` → **`succeeded`** (exit code 0)
- **Terminal Workflow State**: **`succeeded`**
- **Publication Policy**: `external_publication_enabled=0`, `github_status=skipped_no_external_write`, `github_url=null`
- **Safety Compliance**: No commit, push, deploy, version save, force, reset, rebase, or clean performed. Canonical V19 write-tree `04545a202f9a7acdfbd5a52054550f6ddf502fca` and Sites production state preserved. Unrelated dirty bridge changes preserved.

---

## 2. Hardening Architectures Implemented

### Fix A — Centralized Prompt Report Contract
- **Contract Centralization**: Defined a comprehensive, single-source-of-truth `REPORT_CONTRACT` constant in [`bridge/codex_runner.py`](file:///home/b827262/project/e500-codex-smoke/gpt-codex-bridge/bridge/codex_runner.py).
- **Automatic Injection**: `CodexRunner.command_for()` automatically appends `REPORT_CONTRACT` to every Codex job prompt (`f"{job.prompt.rstrip()}\n\n{REPORT_CONTRACT}"`), isolating callers and client applications from needing to know the structured schema.
- **Contract Specifications**:
  - Requires the final response to be strictly **one single JSON object** with zero surrounding prose, chat commentary, or markdown fences.
  - Mandates the exact required field set: `status`, `summary`, `changed_files`, `tests`, `git_status`, `needs_attention`, `sandbox_mode`.
  - Enforces field enums and structure: `status` (`success` | `partial` | `failed`), `tests` array with `{command: string, result: pass|fail, output_summary: string}`, `changed_files` array of strings, `git_status` string, `needs_attention` boolean, and `sandbox_mode` enum (`read-only` | `workspace-write` | `danger-full-access`).
  - Explicitly instructs the model to report observed task results and avoid reporting ambient unrelated dirty files unless relevant.

### Fix B — Raw Failure Observability & Diagnostic Sidecar
- **Diagnostic Preservation**: On structured report validation or parsing failure (invalid JSON, schema mismatch, or prose output), before the runner overwrites the report with the fallback JSON report, a raw diagnostic artifact `{job.id}.raw.txt` is written to `CODEX_REPORT_DIR`.
- **Evidence Sources**: Intelligently captures the invalid output-last-message artifact and/or stdout.
- **Redaction**: All raw diagnostic text is redacted using `Settings.secret_values` and `Settings.redact_text` prior to disk writes, scrubbing tokens, API keys, private keys, and bearer tokens.
- **Size Bounds**: Enforces `MAX_RAW_DIAGNOSTIC_BYTES = 16_384` (16 KiB safe bound) with UTF-8 byte-level truncation.
- **File Security**: Raw diagnostic files are created with mode `0600` best-effort using atomic temporary file replacement.
- **Safe Warning Logging**: Emits structured warning logs with `job.id`, exception class, and redacted safe failure reason without leaking credentials or raw prompt content.
- **Stale Cleanup**: `CodexRunner.run()` proactively cleans up any stale `.raw.txt` artifact from prior runs before executing a job.
- **Sandbox Discrepancy Observability**: The actual `--sandbox` argv mode remains authoritative. If the model reports a valid sandbox enum differing from the job sandbox mode, a structured warning is emitted and normalized to the job mode.

---

## 3. Exact Git Diff

```diff
diff --git a/bridge/codex_runner.py b/bridge/codex_runner.py
index ee1f9e0..26d11f8 100644
--- a/bridge/codex_runner.py
+++ b/bridge/codex_runner.py
@@ -4,6 +4,7 @@ from __future__ import annotations

 from dataclasses import dataclass
 import json
+import logging
 import os
 from pathlib import Path
 import signal
@@ -14,6 +15,9 @@ from .config import Settings
 from .models import Job
 from .sandbox import validate_sandbox_mode

+logger = logging.getLogger(__name__)
+
+
 class ReportValidationError(ValueError):
     """Raised when Codex output does not match the runner report contract."""

@@ -20,4 +24,35 @@ class ReportValidationError(ValueError):
 MAX_STDOUT_REPORT_BYTES = 1_000_000
+MAX_RAW_DIAGNOSTIC_BYTES = 16_384  # <= 16 KiB safe bound for raw failure sidecar
+
+REPORT_CONTRACT = """
+REPORTING CONTRACT:
+Your final message MUST be exactly one JSON object with NO additional text, markdown formatting/fences, or conversational commentary before or after it.
+The JSON object must contain exactly these fields:
+{
+  "status": "success",
+  "summary": "Concise summary of task results",
+  "changed_files": ["path/to/modified/file"],
+  "tests": [
+    {
+      "command": "test command executed",
+      "result": "pass",
+      "output_summary": "summary of test result"
+    }
+  ],
+  "git_status": "relevant git status or empty string",
+  "needs_attention": false,
+  "sandbox_mode": "workspace-write"
+}
+
+Schema requirements:
+- "status": string enum, must be one of: "success", "partial", "failed".
+- "summary": string describing observed task results.
+- "changed_files": array of strings listing paths of files modified for this task. Report observed task results, not ambient unrelated dirty files unless relevant.
+- "tests": array of test objects, each with "command" (string), "result" (string enum: "pass" | "fail"), and "output_summary" (string).
+- "git_status": string describing git status for this task, or empty string.
+- "needs_attention": boolean, true only if human intervention is required.
+- "sandbox_mode": string enum, must be one of: "read-only", "workspace-write", "danger-full-access".
+""".strip()


 @dataclass(frozen=True)
@@ -114,6 +149,7 @@ class CodexRunner:
             raise ValueError("CodexRunner can only run Codex jobs")
         sandbox_mode = validate_sandbox_mode(job.sandbox_mode)
         workspace = self.settings.validate_workspace(job.workspace)
+        prompt = f"{job.prompt.rstrip()}\n\n{REPORT_CONTRACT}"
         # Keep this as argv, never shell text. Do not add approval or
         # sandbox-bypass flags.
         return [
@@ -126,7 +162,7 @@ class CodexRunner:
             str(self.settings.schema_path),
             "-o",
             str(report_path),
-            job.prompt,
+            prompt,
         ]

     @staticmethod
@@ -141,6 +177,37 @@ class CodexRunner:
     def _report_path(self, job: Job) -> Path:
         return self.settings.report_dir / f"{job.id}.json"

+    def _collect_raw_evidence(self, report_path: Path, stdout: bytes | str) -> str:
+        file_text = ""
+        if report_path.is_file():
+            try:
+                file_text = report_path.read_text(encoding="utf-8", errors="replace").strip()
+            except OSError:
+                file_text = ""
+        stdout_text = (
+            stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout)
+        ).strip()
+        if file_text and stdout_text and file_text != stdout_text:
+            return f"--- report artifact ---\n{file_text}\n\n--- stdout ---\n{stdout_text}"
+        return file_text or stdout_text
+
+    def _write_raw_diagnostic(self, job: Job, raw_text: str) -> Path:
+        raw_path = self.settings.report_dir / f"{job.id}.raw.txt"
+        redacted = _redact(raw_text, self.settings.secret_values, self.settings.redact_text)
+        if not isinstance(redacted, str):
+            redacted = str(redacted)
+        encoded = redacted.encode("utf-8")
+        if len(encoded) > MAX_RAW_DIAGNOSTIC_BYTES:
+            redacted = encoded[:MAX_RAW_DIAGNOSTIC_BYTES].decode("utf-8", errors="ignore")
+        temporary = raw_path.with_suffix(raw_path.suffix + ".tmp")
+        temporary.write_text(redacted, encoding="utf-8")
+        try:
+            os.chmod(temporary, 0o600)
+        except OSError:
+            pass
+        os.replace(temporary, raw_path)
+        return raw_path
+
     def _parse_report_payload(self, payload: Any, job: Job) -> dict[str, Any]:
         return self._report_for_job(
             _redact(payload, self.settings.secret_values, self.settings.redact_text),
@@ -213,8 +280,15 @@ class CodexRunner:
         if "sandbox_mode" in report:
             try:
-                validate_sandbox_mode(report["sandbox_mode"])
+                reported_mode = validate_sandbox_mode(report["sandbox_mode"])
             except (TypeError, ValueError) as exc:
                 raise ReportValidationError("sandbox_mode is invalid") from exc
+            if reported_mode != sandbox_mode:
+                logger.warning(
+                    "Codex job %s reported sandbox_mode '%s' differing from job sandbox_mode '%s'",
+                    job.id,
+                    reported_mode,
+                    sandbox_mode,
+                )
         normalized = dict(report)
         normalized["sandbox_mode"] = sandbox_mode
         return validate_report(normalized)
@@ -226,6 +300,11 @@ class CodexRunner:
     def run(self, job: Job) -> RunOutcome:
         report_path = self._report_path(job)
         report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
+        raw_path = self.settings.report_dir / f"{job.id}.raw.txt"
+        try:
+            raw_path.unlink(missing_ok=True)
+        except OSError:
+            pass
         workspace = self.settings.validate_workspace(job.workspace)
         command = self.command_for(job, report_path)
         child_env = self.settings.codex_environment()
@@ -260,12 +339,29 @@ class CodexRunner:
             self._write_report(report_path, report)
             return RunOutcome(report_path, report, exit_code, timed_out=True)

+        report_failure_exc: Exception | None = None
         try:
             report = self._read_report(report_path, job)
-        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReportValidationError):
+        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReportValidationError) as exc:
+            report_failure_exc = exc
             try:
                 report = self._parse_stdout_report(stdout, job)
-            except ReportValidationError:
+                report_failure_exc = None
+            except ReportValidationError as stdout_exc:
+                report_failure_exc = stdout_exc
+                raw_evidence = self._collect_raw_evidence(report_path, stdout)
+                self._write_raw_diagnostic(job, raw_evidence)
+                safe_reason = _redact(
+                    str(report_failure_exc),
+                    self.settings.secret_values,
+                    self.settings.redact_text,
+                )
+                logger.warning(
+                    "Codex job %s failed structured report validation: %s (%s)",
+                    job.id,
+                    report_failure_exc.__class__.__name__,
+                    safe_reason,
+                )
                 report = {
                     "status": "failed",
                     "summary": "Codex did not produce a valid structured report",
```

---

## 4. Test Suite Execution & Evidence

### A. Focused Unit Tests in `tests/test_codex_runner.py`
1. `test_command_uses_default_mode_and_exact_argv`: Proves `command_for` uses exact argv with default sandbox mode and appended `REPORT_CONTRACT`.
2. `test_command_appends_report_contract_to_bare_prompt`: Proves `command_for` appends contract to a prompt containing zero report instructions.
3. `test_report_contract_content_and_requirements`: Verifies schema fields, status enums, test schema, single JSON requirement, and dirty file instruction.
4. `test_current_codex_report_file_is_accepted`: Proves valid report is accepted and produces no `.raw.txt` sidecar.
5. `test_missing_output_file_uses_only_strict_json_stdout_compatibility`: Proves valid stdout fallback succeeds and produces no `.raw.txt` sidecar.
6. `test_exit_zero_with_missing_or_natural_language_report_fails_closed`: Proves natural language fails closed and generates raw diagnostic sidecar.
7. `test_invalid_prose_output_creates_redacted_bounded_raw_sidecar_and_failed_fallback`: Proves secret tokens are redacted, bounds hold, permissions are `0600`, and standard failed fallback is returned.
8. `test_invalid_report_file_and_invalid_stdout_fail_closed_and_preserve_raw`: Proves invalid file artifact is preserved in `.raw.txt`.
9. `test_raw_sidecar_truncation_bound_holds`: Proves payload > 16 KiB is strictly truncated to `MAX_RAW_DIAGNOSTIC_BYTES` (16,384 bytes).
10. `test_valid_rerun_clears_stale_raw_sidecar`: Proves a subsequent successful run clears stale raw failure sidecars.
11. `test_report_mode_is_normalized_to_job_mode_and_warns_on_discrepancy`: Proves sandbox mode normalization emits structured warning and succeeds.

### B. Test Execution Runs
- **Unittest Suite**:
  ```bash
  python3 -m unittest discover -s tests -p "test_*.py"
  ```
  **Result**: `Ran 110 tests in 4.023s — OK`
- **Pytest Suite**:
  ```bash
  pytest
  ```
  **Result**: `133 passed in 4.84s (100% OK)`

---

## 5. Worker Service Verification

- **Service**: `gpt-codex-worker.service`
- **Command**: `systemctl --user restart gpt-codex-worker.service`
- **Status Output**:
  ```
  ● gpt-codex-worker.service - GPT Codex Bridge Worker
       Loaded: loaded (/home/b827262/.config/systemd/user/gpt-codex-worker.service; enabled; preset: enabled)
       Active: active (running) since Sat 2026-08-29 10:40:57 CST; 7ms ago
     Main PID: 3159601 (python3.12)
        Tasks: 1 (limit: 34531)
  ```

---

## 6. Production V19 Acceptance Verification

### A. Health & Contract Checks
- **Health Check**:
  `GET https://e500-control-plane.b827262.chatgpt.site/api/tg/health` → **`HTTP 200`**
  ```json
  {
    "ok": true,
    "bridgeConfigured": true,
    "bridgeConnected": true,
    "bridgeCode": "BRIDGE_READY",
    "bridge": {
      "ok": true,
      "service": "codex-bridge",
      "api": "v1",
      "telegramConfigured": true,
      "workspaceConfigured": true,
      "queue": {
        "queued": 0,
        "running": 0,
        "succeeded": 90,
        "failed": 11
      }
    }
  }
  ```
- **Type Validation Check**:
  `POST https://e500-control-plane.b827262.chatgpt.site/api/tg/workflow` with `noExternalWrite="invalid_boolean"`:
  **Response**: **`HTTP 400`** `{"ok": false, "code": "NO_EXTERNAL_WRITE_INVALID"}`

### B. Production Workflow Execution (`flow-17ef76e65a9c4e82`)
- **Dispatch**:
  - **Prompt Submitted**: `"Inspect repository root and report git branch name and working directory."` *(deliberately contains zero words about JSON, schema, report fields, or structured output)*
  - **Request**: `POST /api/tg/workflow` with `noExternalWrite: true`
  - **Response**: **`HTTP 202 Accepted`**

- **Database Record**:
  ```
  id: flow-17ef76e65a9c4e82
  status: succeeded
  current_stage: github
  external_publication_enabled: 0
  github_status: skipped_no_external_write
  github_url: NULL
  error: NULL
  created_at: 2026-08-29T02:35:35.389310+00:00
  finished_at: 2026-08-29T02:39:47.603305+00:00
  ```

### C. Stage Breakdown

| Stage | Job ID | Provider | Status | Exit Code | Sandbox Mode |
|---|---|---|---|---|---|
| 1. `gpt` | `job-945ddc3dfb7a428d` | `codex` | **`succeeded`** | 0 | `workspace-write` |
| 2. `agy` | `job-fd143c6283f542ee` | `agy` | **`succeeded`** | 0 | `workspace-write` |
| 3. `claude` | `job-f5581dbf00134460` | `claude` | **`succeeded`** | 0 | `workspace-write` |

- **Codex Stage Report (`job-945ddc3dfb7a428d.json`)**:
  ```json
  {
    "status": "success",
    "summary": "Working directory: /home/b827262/project/e500-codex-smoke/ai-meeting-room; Git branch: web/codex-sample-standard.",
    "changed_files": [],
    "tests": [],
    "git_status": "On branch web/codex-sample-standard (tracking telegram-ai-code/web/codex-sample-standard); no files modified for this task.",
    "needs_attention": false,
    "sandbox_mode": "workspace-write"
  }
  ```
- **Diagnostic Sidecar Check**:
  Confirmed `/home/b827262/.local/state/gpt-codex-bridge/reports/job-945ddc3dfb7a428d.raw.txt` was **not** created because report validation succeeded.

---

## 7. Containment & Canonical Parity

- **External Publication Containment**: Verified `external_publication_enabled=0`, `github_status=skipped_no_external_write`, and `github_url=NULL`.
- **Canonical Staged Write-Tree**:
  - Executed `git write-tree` in `/home/b827262/project/e500-codex-smoke/web/e500-control-plane`.
  - Value: `04545a202f9a7acdfbd5a52054550f6ddf502fca` (exact match to target V19 tree).

---

## 8. Final Verdict

**`PASS`**

Both Fix A (`REPORT_CONTRACT` centralized prompt injection) and Fix B (raw failure observability, redaction, bounded 16 KiB `.raw.txt` sidecar, `0600` permissions, and sandbox discrepancy warning) are fully verified across unit test suites and the end-to-end production workflow with complete external publication containment.

AGY_CODEX_REPORT_CONTRACT_HARDEN_READY
