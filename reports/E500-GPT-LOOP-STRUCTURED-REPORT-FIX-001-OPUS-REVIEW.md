# E500 /gpt Loop — Opus 5 獨立審查與 Live Acceptance

日期：2026-08-27
任務：`E500-GPT-LOOP-STRUCTURED-REPORT-FIX-001`
審查者：Opus 5
來源文件：`E500-GPT-LOOP-LUNA-ISSUES-SUMMARY.md`

## 1. 結論

`SOURCE_FIX_READY / LIVE_E2E_PENDING / OPUS_REVIEW_PENDING`
→ **`/gpt LOOP = PASS`（尚未 commit）**

Opus review 無 blocking finding。Live full loop 已實測成功。

## 2. Root Cause 獨立驗證（實機 probe）

Luna 的 direct CLI probe 先前失敗，本次已補完。

環境：`codex-cli 0.150.1`，獨立 `/tmp` git repo，`stdin=/dev/null`。

### Probe A — `-o` 可寫
- exit code：0
- `-o` artifact：已建立，內容為合法 JSON report
- stdout：**同一份完整 JSON object**（136 bytes，無 prose、無 JSONL）

### Probe B — `-o` 不可寫（重現線上故障）
- exit code：**0**
- artifact：**未建立**
- stderr：`Failed to write last message file ...: No such file or directory`
- stdout：**完整且合法的 JSON report**

**判定：root cause 與 Luna 描述一致且已由實機重現。**
`codex exec` 在無法寫入 `-o` artifact 時仍回傳 `exit 0`，並把 final JSON 送到 stdout。
舊版 Bridge 將 stdout 設為 `DEVNULL`，因此丟棄了唯一有效的 report。

線上兩筆失敗紀錄的特徵完全吻合：
`job-5d3a161078944a74` / `job-e120fa85458d4217` → `exit_code=0`
+ `Codex did not produce a valid structured report`。

## 3. Security Boundary 審查

對 `_parse_stdout_report` 做邊界測試，全部 fail closed：

| 輸入 | 結果 |
|---|---|
| bare valid JSON | accepted（預期） |
| 前後空白 / 換行 | accepted（`json.loads` 語意，安全） |
| prose + JSON | rejected |
| JSON + prose | rejected |
| markdown fenced | rejected |
| JSONL（`--json` events） | rejected |
| sandbox_mode mismatch | rejected |
| 多餘欄位 | rejected |
| 超過 1,000,000 bytes | rejected |
| 空輸出 / 非法 UTF-8 | rejected |
| JSON array / scalar | rejected |

- 未從 prose、markdown、JSONL 抽取 JSON：**確認**
- redaction 在 stdout path 同樣生效：**確認**（secret、Bearer token 皆被 `[REDACTED]`）
- job sandbox 不可被 report override：**確認**
- artifact-first 優先序：**確認**（artifact 有效時不讀 stdout）

## 4. `needs_attention` 語義變更的影響範圍

`RunOutcome` 由 Codex / AGY / Claude 三個 runner 共用，故需確認 blast radius。

檢查結果：AGY 與 Claude runner 的 `needs_attention` 與 status 為結構性綁定
（`status=success` 必然 `needs_attention=False`），不存在
`success + needs_attention=true` 的組合。

**結論：此變更實際只影響 Codex stage，對既有 workflow 無回歸風險。**

## 5. 測試結果

- `tests.test_codex_runner`：12 passed
- full discover：**93 tests，93 passed，0 error**
- 先前 sandbox-blocked 的
  `test_only_queued_workflow_can_be_cancelled_over_authenticated_http`
  在本 runtime **通過** → 確認為 managed sandbox 的 socket 限制，非 bridge logic regression。

## 6. Live `/gpt` Full Loop Evidence

前置：queue 無 running / queued job（`failed 7 / succeeded 65`）。
註：先前觀察到的 `codex-linux-sandbox` 為 2 天前遺留的 orphan process
（PPID=1952，非 worker 子程序），非進行中任務。

`systemctl --user restart gpt-codex-worker.service` — 僅重啟此服務。

| Stage | Job ID | Status | Exit |
|---|---|---|---|
| gpt (codex) | `job-8676c2fe3b144d63` | succeeded | 0 |
| agy | `job-121bc7d09538437d` | succeeded | 0 |
| claude | `job-15057b4ebe9f49b2` | succeeded | 0 |

- Workflow：`flow-5c18a329f5934ea8`
- Terminal status：**`succeeded`**
- Sandbox：`read-only`

Codex structured report（已被 Bridge 正確接受）：
```json
{
  "status": "success",
  "summary": "Repository: e500-codex-smoke; current branch: web/codex-sample-standard.",
  "changed_files": [],
  "tests": [],
  "git_status": "No Git state changes made.",
  "needs_attention": false,
  "sandbox_mode": "read-only"
}
```

## 7. Side Effect 說明

- 無 commit、無 push、無 deploy；Luna 的修補仍為未提交的工作樹變更。
- workspace（`ai-meeting-room`）無任何檔案變更。
- **例外**：workflow 內建 `github` stage 因 `GITHUB_REPORT_ENABLED=true` 自動上傳了
  auto-loop 報告至 `E500-3th-aicode` repo
  （`reports/auto-loop/flow-5c18a329f5934ea8.md`）。
  此為 loop 既有行為，非程式碼 deploy；若後續 smoke test 不希望產生外部寫入，
  應先將該旗標關閉。

## 8. 未處理事項（不阻擋驗收）

- `/api/tg/health`：本 codebase 的路由實為 `/health`，且 HTTP API 未以 service 形式常駐
  （`gpt-codex-api.service` inactive）。本次改由 queue 直接提交，
  與 `/gpt` 走同一 `submit_workflow` 路徑。此 endpoint 命名落差建議另行釐清。
- Bridge child 實際 model / effort 未在此次變更範圍內，仍需另行確認。
- P2：child 對 private runtime report directory 無寫入權限的邊界未改變。
  目前 artifact-first + strict stdout fallback 為合理設計；
  在無獨立安全審查前，不建議放寬 child 寫入權限。

## 9. 建議下一步

Step 9（`git commit` / `push`）需人工批准，尚未執行。
