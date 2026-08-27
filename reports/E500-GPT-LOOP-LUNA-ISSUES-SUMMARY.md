# E500 /gpt Loop — Luna 問題整理與未處理項目

日期：2026-08-27
來源：`E500-GPT-LOOP-STRUCTURED-REPORT-FIX-001-LUNA.md`
任務：`E500-GPT-LOOP-STRUCTURED-REPORT-FIX-001`

## 1. 執行摘要

Luna 已定位並修正 `/gpt` workflow 第一階段的 structured-report transport blocker。

先前兩次實測：
- `flow-00200f1d5b544dfc` / `job-5d3a161078944a74`
- `flow-4c68012a97214f33` / `job-e120fa85458d4217`

兩次 Codex child process 都是 `exit_code=0`，但 Bridge 最後回報：
`Codex did not produce a valid structured report`。

目前判定：
- Root cause：已確認
- Source fix：已完成
- Regression tests：通過
- Live `/gpt` full loop：尚未重新驗證成功
- Opus 5 independent review：尚未完成
## 2. 已確認 Root Cause

問題不是 Codex 沒有完成任務，而是 Bridge 的 report transport 假設不完整。

原流程：
`codex exec` → `--output-schema` → `-o <runtime-report-path>` → Bridge 只讀 `-o` file。

實際狀況：
1. Codex CLI 已產生符合 schema 的 final JSON。
2. Codex child filesystem policy 允許 workspace 與 `/tmp`。
3. private runtime report directory 不在 child 可寫範圍。
4. 因此 `-o` artifact 可能沒有建立，但 Codex 仍可正常 `exit 0`。
5. 舊 Bridge 把 stdout 設成 `DEVNULL`，合法 final JSON 因而被直接丟棄。
6. Bridge 最後只能產生 fallback failed report，workflow 被停在 GPT/Codex stage。
7. AGY、Claude、final report 因前一階段失敗而全部 blocked。

這是「report transport mismatch」，不是模型本身執行失敗。

## 3. Luna 已完成的修正

### 3.1 `bridge/codex_runner.py`
- `stdout=subprocess.DEVNULL` 改成 `stdout=subprocess.PIPE`。
- `-o` artifact 仍為 authoritative / 第一優先來源。
- artifact 缺失或無效時，才啟用 strict stdout compatibility fallback。
- stdout 只接受「完整單一 UTF-8 JSON object」。
- 不從 prose、Markdown 或 JSONL 中抽取 JSON。
- stdout 上限設為 1,000,000 bytes。
- stdout payload 必須走同一套 schema validation、secret redaction 與 `sandbox_mode` normalization。
- malformed JSON、oversized output、sandbox mismatch 仍 fail closed。
- `needs_attention=true` 時，即使 status 是 `success` 或 `partial`，也不再算 succeeded。

### 3.2 `tests/test_codex_runner.py`
已新增/擴充以下回歸測試：
- current Codex report file 可正常接受
- output file 缺失時 strict JSON stdout fallback
- exit 0 但 missing/natural-language report 必須 fail closed
- invalid artifact + invalid stdout 必須 fail closed
- schema shape validation
- schema file 與 strict validator contract 一致
- `sandbox_mode` 缺失時 normalization
- `sandbox_mode` mismatch 必須失敗
- job sandbox 不可被 report override
- `needs_attention` 必須壓過 success/partial
- timeout / process termination

### 3.3 README
補充 artifact-first / strict-stdout-fallback structured-report contract。

`schemas/codex_report.schema.json` 已檢查但未修改。
## 4. 測試結果

Luna 回報：
- `python3 -m unittest tests.test_codex_runner -v`：12 passed
- broader bridge suite（排除 socket-binding API test）：92 passed
- full discover：93 tests，92 passed，1 environment error
- `python3 -m compileall -q bridge adapters tests`：PASS
- executable `bin/` / `scripts/` 的 `bash -n`：PASS
- `git diff --check`：PASS

唯一 full-suite error：
`tests/test_api.py::test_only_queued_workflow_can_be_cancelled_over_authenticated_http`

原因是 managed execution environment 禁止 bind `127.0.0.1:0`：
`PermissionError: [Errno 1] Operation not permitted`

目前沒有證據顯示這是 bridge logic regression；但此測試仍應在正常 E500 runtime 再執行一次。

## 5. 尚未處理 / 尚未驗證事項

### P0 — Live `/gpt` full loop 尚未驗收
目前還沒有修補後的 terminal evidence 證明：
`/gpt → Codex → AGY → Claude → final report → workflow=succeeded`。

因此目前只能宣稱 structured-report source blocker 已修，不能宣稱整個 `/gpt` loop 已正式 PASS。
### P0 — Worker 尚未套用修補後版本驗證
Luna 沒有執行 service restart。
建議只處理：`gpt-codex-worker.service`。

在確認 queue 可安全恢復後，再執行：
`systemctl --user restart gpt-codex-worker.service`

不可為了這次修補重啟無關服務。

### P1 — API socket test 尚未在正常環境重跑
managed sandbox 無法 bind loopback ephemeral port，因此 cancellation HTTP test 尚缺正常 runtime 驗證。

### P1 — Direct Codex CLI probe 未完成
Luna 的 read-only direct probe 在 model execution 前即因：
`failed to initialize in-process app-server client: Read-only file system`
而停止。

所以目前缺少「修補後、current Codex CLI、真實 process」的直接 transport 驗證。

### P1 — Opus 5 / medium 獨立審查尚未完成
仍需獨立確認：
- root cause 是否與實際 CLI 行為一致
- stdout fallback 是否存在邊界繞過
- schema / redaction / sandbox_mode 是否保持 fail closed
- `needs_attention` 語義改動是否會影響既有 workflow
- 測試是否覆蓋主要 regression surface
### P1 — 實際 Bridge child model 設定差異
Luna 觀察到 local Codex configuration 選到 `gpt-5.6-sol` / medium。
本次修補沒有修改 model selection 或 user config。

這不影響 structured-report transport fix 本身，但未來 `/gpt` 實際使用哪個 model / effort 必須另外確認，不能從外層 Luna/xhigh 任務推定 Bridge child 也使用 Luna/xhigh。

### P2 — Private runtime report path 的根本權限邊界仍存在
本次採取的是相容性修補：artifact-first，artifact 不可用時改讀 strict stdout。

因此 child 對 private runtime report directory 無寫入權限這個事實沒有改變。
這可能是刻意的 security boundary，也可能表示 `-o <runtime-report-path>` 長期而言不是理想 transport。

後續 Opus review 應判斷：
- 保持現有 artifact-first + stdout fallback 是否為最佳安全設計；或
- 是否應改成 child 可寫的 temporary report，再由 trusted Bridge 搬移/持久化。

未經獨立安全審查前，不建議直接放寬 child 對 private runtime state 的寫入權限。

### P2 — 尚未 commit / push / deploy
Luna 明確沒有執行 reset、clean、checkout、rebase、commit、push 或 deploy。
修補目前仍屬工作樹變更，正式納入 Git 前應先通過 Opus review 與 live loop acceptance。
## 6. 建議後續處理順序

1. **Opus 5 / medium code review**：先審查 Luna diff 與 security contract。
2. **確認 queue 狀態**：必須沒有不應中斷的 running job。
3. **只重啟 `gpt-codex-worker.service`**：讓 live worker 載入修補後程式。
4. **重新確認 `/api/tg/health`**：bridge ready、queue 正常。
5. **執行 harmless live `/gpt` smoke loop**：禁止 commit/push/deploy。
6. **保存完整 workflow evidence**：flow id、各 stage job id、status、exit code、report summary。
7. **驗收條件**：Codex、AGY、Claude、final report 全部 succeeded，workflow 最終為 `succeeded`。
8. **重跑 socket/API test**：在正常 E500 runtime 驗證先前 sandbox-blocked test。
9. **通過後才進 Git finalize**：review diff → commit → push；deploy 仍需另行人工批准。

## 7. Acceptance Gate

目前狀態：`SOURCE_FIX_READY / LIVE_E2E_PENDING / OPUS_REVIEW_PENDING`

只有下列全部成立，才能改標 `/gpt LOOP = PASS`：
- Opus 5 review 無 blocking finding
- worker 已載入新 runner
- `/api/tg/health` = ready
- 新 workflow HTTP submission 成功
- Codex structured report 被 Bridge 正確接受
- AGY stage succeeded
- Claude stage succeeded
- final report stage succeeded
- workflow terminal status = `succeeded`
- 無 commit / push / deploy side effect
