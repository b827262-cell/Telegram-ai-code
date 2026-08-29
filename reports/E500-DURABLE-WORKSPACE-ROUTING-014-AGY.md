# E500-DURABLE-WORKSPACE-ROUTING-014 — 驗收報告

Date: 2026-08-29 (Asia/Taipei)
Code baseline for all routing regression: `17cf2c4` (`bridge: complete self-contained baseline for routing work`)
Status: **實作完成、全鏈路驗收通過；尚未 commit、尚未 push。**

## 1. 錨點鏈

| SHA | 用途 |
|---|---|
| `3a99346` | 011–013 已審查行為錨點（不可執行） |
| `17cf2c4` | 014 的 code baseline：相依閉包完整、可 checkout、可測、可 bisect |
| `ef32b18` | docs-only：記錄 green baseline（不混入 014 implementation） |
| (pending) | 014 implementation commit |

`git archive HEAD` 隔離驗證在 `17cf2c4` 取得 `SELF_CONTAINED_BASELINE = PASS`（116/116、0 collection errors、25/25 imports）。詳細過程見 `reports/E500-BRIDGE-GREEN-BASELINE-PRE-014.md`。

## 2. 架構（已實作）

```
caller ── workspace="bridge" | "meeting-room" ─┐
                                               ├─ API (bridge/api.py)  ─┐
                                               │  Telegram (adapters/…) ─┼─ Settings.resolve_workspace_route()
  ❌ workspace="/home/.../gpt-codex-bridge"     │  （同一支 resolver）    ─┘        │
  ❌ workspace="../gpt-codex-bridge"     ← 在字元集驗證階段即 REJECT                 ▼
  ✅ workspace="bridge"                                                          resolve_route()
                                                  ├─ alias 表（操作者設定）→ 絕對路徑
                                                  ├─ 重新檢查 CODEX_ALLOWED_WORKSPACES 包含關係
                                                  └─ WorkspacePolicy（provider 白名單、sandbox 上限、發佈許可）
                                                          ▼
                                              queue/job ── runner
```

設計要點：
- 呼叫端只能提供 opaque slug；`WORKSPACE_ALIAS_PATTERN = [a-z0-9][a-z0-9-]{0,31}` 以 `fullmatch` 驗證，字元集排除 `/`、`.`、`~`、空白，因此絕對路徑、穿越、編碼穿越都無法抵達 resolver。
- `resolve_workspace_path()` 在 `Path.resolve()` **之前**檢查絕對性。`Path.resolve()` 恆回傳絕對路徑，若先 resolve 再檢查，相對 target 會靜默綁定到服務啟動時的 cwd — 這是本輪 self-review 抓出並修復的真實隱性缺陷（`test_relative_alias_target_refuses_to_load`）。
- 符號連結：load 階段以 `resolve()` 後的 realpath 檢查是否仍在 allowlist 內。
- 政策只封頂、不抬高：`SANDBOX_MODE_RANK` read-only(0) < workspace-write(1) < danger-full-access(2)。上限在**派送邊界**強制（決定 job 能否被建立），不約束已執行中的 job。
- Fail-closed 下限：已設定但未經審核的 alias → `DEFAULT_WORKSPACE_POLICY`（read-only + 禁止發佈）。新增 alias 到設定檔本身不授予任何寫入或發佈能力。此下限刻意只收 sandbox 與發佈兩個維度；`allowed_providers` 維持全集，因為 provider 選擇本身不擴權，且 durable 鏈需要 codex+agy+claude 三者。
- `WORKSPACE_POLICIES` 以 **alias 名稱**為鍵，故操作者的 alias 表本身是信任輸入：把高上限政策的名字指到任何目錄，該目錄即取得該政策。014 的保證是「呼叫端無法擴權」，不是「路徑身分綁定政策」。
- 未帶 alias 的請求走 `LEGACY_WORKSPACE_POLICY`，保留 014 之前的能力，因此既派送無迴歸。
- HTTP 與 Telegram 共用 `Settings.resolve_workspace_route()`，兩邊語意無法漂移。
- 拒絕碼為封閉詞彙：`WORKSPACE_ALIAS_INVALID` / `WORKSPACE_ALIAS_UNKNOWN` / `WORKSPACE_NOT_ALLOWED` / `WORKSPACE_SANDBOX_DENIED` / `WORKSPACE_PUBLICATION_DENIED` / `WORKSPACE_PROVIDER_DENIED`。`WorkspaceRoutingError` 是 `ValueError` 子類別，其 `except` 必須排在通用子句之前，否則 code 會被 `REQUEST_INVALID` 吞掉。
- 拒絕訊息與所有回應一律不外洩絕對路徑。

初始政策：
- `meeting-room` = 014 前的上限（danger-full-access + 可發佈）。
- `bridge` = **上限 workspace-write、發佈一律禁止**（自修改 control-plane 的 durable task 沒必要順帶取得發佈能力）。

## 3. 12 行驗收矩陣

E=實測證據來源。U=單元/整合測試，L=對執行中 API 的即時探針，C=完成之 durable 鏈。

| # | 情境 | 期望 | 結果 | E |
|---|---|---|---|---|
| 1 | `workspace="meeting-room"` |  routed → meeting-room | PASS | U, L（`job-0be77ebdf7824360`，見 §3.1） |
| 2 | `workspace="bridge"` | routed → bridge checkout | PASS | U, C |
| 3 | 未知 alias | 400 `WORKSPACE_ALIAS_UNKNOWN`，不建立 job | PASS | U, L |
| 4 | 絕對路徑 | REJECT `WORKSPACE_ALIAS_INVALID` | PASS | U, L |
| 5 | `../gpt-codex-bridge` | REJECT `WORKSPACE_ALIAS_INVALID` | PASS | U, L |
| 6 | symlink 逃逸 allowlist | 設定載入階段即拒絕 | PASS | U |
| 7 | bridge + read-only | 允許派送；read-only 由 runner 強制 | **PARTIAL**（見 §6） | U, L；codex 有 `--sandbox`，claude/agy 無 |
| 8 | bridge + workspace-write | 可修改受控標本 | PASS | C |
| 9 | 要求 mode 高於上限（bridge + full） | REJECT `WORKSPACE_SANDBOX_DENIED` | PASS | U, L |
| 10 | meeting-room 無迴歸（+ full / + 發佈） | 仍准許 | PASS | U |
| 11 | `noExternalWrite=true` 封裝保留 | persists `external_publication_enabled=0`、github 階段 skip | PASS | U, C |
| 12 | bridge 要求對外發佈 | BLOCKED `WORKSPACE_PUBLICATION_DENIED` | PASS | U, L |

附加即時探針（非矩陣內，一併 fail-closed）：`~/x`、`"   "`、`a/b`、無效 provider、未驗證 token — 共 **10/10 PASS**，且 `jobs 113 → 113`、`workflows 25 → 25`（零副作用）。

### 3.1 正向即時路由探針

`POST /run {workspace:"meeting-room", mode:"read", provider:"codex"}` → `job-0be77ebdf7824360`

| 欄位 | 值 |
|---|---|
| status / exit_code | `succeeded` / `0` |
| 解析後 workspace | `/home/b827262/project/e500-codex-smoke/ai-meeting-room` |
| sandbox_mode（job 與報告一致） | `read-only` |
| changed_files | `[]` |

`meeting-room` 在 014 之前從來只能以 default route 抵達；這次是它**第一次被 alias 明確點到**並由 worker 實際執行，故矩陣第 1 列的即時證據由此補齊（`external_publication` 未涉及，`/run` 不要求發佈）。

## 4. 即時 durable 鏈（首次 bridge-routed 全鏈）

`POST /workflow {workspace:"bridge", mode:"write", noExternalWrite:true}`

| 階段 | job | provider | sandbox | exit | 結果 |
|---|---|---|---|---|---|
| gpt | `job-12d08a547d594205` | codex | workspace-write | 0 | succeeded |
| agy | `job-8993a015ecd5478a` | agy | workspace-write | 0 | succeeded |
| claude | `job-efa795cec64d4a04` | claude | workspace-write | 0 | succeeded |

Workflow `flow-163c7d3c14694430`：`status=succeeded`、`current_stage=github`、`external_publication_enabled=0`、`github_status=skipped_no_external_write`、`github_url=''`、`error=''`。
三個 job 的 `workspace` 皆為 `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge`（路由前不可能出現）。

受控標本 `tests/fixtures/durable-routing-014.txt`：
- `token` 由 `pre-014-unrouted` → `routed-014-074614`
- `git diff --numstat` = **1 行增 / 1 行刪**，12 行標頭 byte-identical，總行數維持 13。
- 這是「workspace-write 確實能寫入 bridge checkout」的直接證據 — 路由前所有 job 都被釘在 meeting-room，做不到。

發佈零副作用（獨立交叉驗證）：`reports/auto-loop/` 不存在；近 25 分鐘內 `github_status`/`github_url` 非空的 workflow = **0 筆**。注意：worker 的 `.env` 中 `GITHUB_REPORT_ENABLED=true`，因此 bridge 的發佈禁止是**實質**封鎖，不是因為發佈功能本來就關閉。

## 5. 測試

| 指令 | 結果 |
|---|---|
| `python3 -m unittest discover -s gpt-codex-bridge/tests -t gpt-codex-bridge` | **Ran 178 tests — OK** |
| `python3 -m pytest -q tests`（於 gpt-codex-bridge） | **201 passed, 69 subtests passed** |
| 014 三模組 | 52 passed, 43 subtests passed |

新增測試檔：`tests/test_workspaces.py`(372)、`tests/test_api_workspace_routing.py`(211，真實 `BridgeAPIServer` 綁定臨時 port)、`tests/test_telegram_workspace_routing.py`(146)。

## 6. Sandbox 執行力 findings（015 輸入）

### 6.1 workspace-write 的 Codex job 無法跑綠本 repo 測試
`codex sandbox -c sandbox_mode="workspace-write"` 下建立 AF_INET socket 即 `PermissionError: [Errno 1] Operation not permitted`（實測重現）。

`pytest -q tests` 於該 sandbox：**19 failed, 182 passed, 63 subtests**。分佈：
- `tests/test_api_workspace_routing.py` 15（014 新增）
- `tests/test_api.py` 3、`tests/test_api_logging.py` 1 — **014 之前就存在的檔案**

即「測試需要 loopback bind、因此無法在受 sandbox 的 job 內跑綠」是本 repo 既有性質，014 只是讓它多暴露 15 個案例。建議（015 處理，不在本輪範圍）：在 bind 遭拒時 `skipTest`，避免未來每個 sandboxed bridge job 都回報 19 failed，讓訊號退化成雜訊。

### 6.2 gpt 階段回報 `partial` 的原因是操作失誤，非 014 缺陷
codex 階段報告寫著 `ERROR tests/test_workspaces.py; 1 error during collection` 與 `unittest ... errors=18`。時間戳比對：

- gpt job 視窗：`15:45:55 – 15:47:23`
- `bridge/workspaces.py` mtime：`15:47:22` ← 我**在該 job 執行期間**編輯它

因此該 collection error 是我同步編輯 import 目標所造成的暫態。證據：檔案穩定後，`tests/test_workspaces.py` 在 sandbox 內單獨執行為 **31 passed, 37 subtests**。
教訓：**不該把有寫入能力的 job 派進我同時正在編輯的工作區**。claude 階段（15:50:27–15:52:45，完全在我的最後編輯 15:48:14 之後）独立複測 `201 passed, 69 subtests` 與 `178 OK`，與我本輪結果一致 — 穩定後的綠是由未被 sandbox、也無共同編輯衝突的階段獨立確認。

### 6.3 014 不聲稱 provider sandbox 對等（明確邊界）
僅 `bridge/codex_runner.py` 傳遞 `--sandbox`。`claude_runner.py` 與 `agy_runner.py` 只對 `sandbox_mode` 做 `validate_sandbox_mode()` 並寫進報告中繼資料 — **沒有 OS/CLI 層強制力**。

因此 bridge 工作區上 agy / claude 階段的 `workspace-write` 只是**派送時的政策上限**，不是執行時的能力封鎖；本輪他們未越權修改標本（claude 自述「未做任何修改」），但那是**合作，不是強制**。這正是保留給 **015 = Claude / AGY sandbox enforcement parity** 的工作。

## 7. 014-only 差異統計

因 014 必須編輯「已帶著 997 行無關未提交工作」的檔案（四個混雜檔案自身即含既有 266 行新增／84 行移除；全部追蹤檔既有工作為 402 行新增／87 行移除，另有 `observability.py` 170、`test_api.py` 147、`test_api_logging.py` 278 三個未追蹤檔），逐檔以 pre-014 快照 (`/tmp/pre014-true`) 重新計算，避免把別人的工作算進 014：

| 檔案 | 014 delta |
|---|---|
| `bridge/workspaces.py` | 新增 219 行 |
| `bridge/config.py` | +64 / −0（對 `17cf2c4` 純新增；014 前為 clean） |
| `bridge/api.py` | +41 / −11 |
| `adapters/telegram.py` | +20 / −3 |
| `.env.example` | +7 / −0 |
| `README.md` | +2 / −0 |
| `tests/test_workspaces.py` | 新增 372 行 |
| `tests/test_api_workspace_routing.py` | 新增 211 行 |
| `tests/test_telegram_workspace_routing.py` | 新增 146 行 |
| `tests/fixtures/durable-routing-014.txt` | 新增 13 行 |

 production 程式碼：新增 353 行、移除 14 行。測試：新增 729 行（另標本 13 行）。
 `reports/E500-PRE-014-BRIDGE-BLOB-MANIFEST.txt` 記錄 014 開始前既已 dirty 的 12 個檔案 blob hash，作為歸屬邊界。

刪除的自我審查項目（屬投機或死碼）：`describe_alias_catalog`（014 刻意不改 `/health` 契約）、不可達的 `mode not in SANDBOX_MODE_RANK` 分支、未使用的 `SANDBOX_MODES` import、`__all__`、多餘的 `isinstance` 分支，以及一個冗餘測試。

## 8. 部署狀態

- `CODEX_WORKSPACE_ALIASES` 在兩個 EnvironmentFile（`~/.config/gpt-codex-bridge/api.env` 與 repo `gpt-codex-bridge/.env`）內容一致；allowlist / default 亦一致。原始檔備份於 `/tmp/*.bak-014`，0600 權限保留，僅改動 3 行非機密設定。
- `gpt-codex-api` / `-worker` / `-telegram` 已於 **15:57** 重啟到含 self-review 修正的最終程式碼；§3 的即時探針是在重啟**之後**執行（重啟前的程序載的是 15:43 的碼，早於我最後編輯，故不作數）。
- 未 push。`HEAD = ef32b18`，與 `origin/main` 的關係為 9 ahead / 12 behind。

## 9. Opus 5 終局審查（durable final review）

派送：`POST /run {provider:"claude", mode:"read", workspace:"bridge"}` → `job-f5cca594af824237`，`status=succeeded`、`exit_code=0`、報告 `sandbox_mode=read-only`、路由至 bridge 工作區（同時也是「read-only 在 bridge 上限內准許」的即時證據）。

**裁決：`ACCEPTANCE = PASS`；「必須在 014 結案前修復的缺陷：無。」** 九項審查重點**全數 PASS**。第 3 項（`expanduser()` → `is_absolute()` → `resolve()` 的順序）被評為「本次實作最正確的一處」；第 5 項的 default route 繼承其實際落地 workspace 的上限，亦被單獨肯定為正確做法。

審查者提出四項「被高估或證據不足」的措辭問題，逐項處置：

| # | finding | 處置 |
|---|---|---|
| 一 | 模組 docstring 的「re-resolved / re-checked」可被讀成執行期也會重新 resolve；實際上執行期只做 allowlist 字典比對，屬 TOCTOU 殘餘風險 | **已修改** `workspaces.py` docstring：明載「target 在載入時解析一次、每次派送以 allowlist 複查路徑」。殘餘風險記錄於本報告（利用前提需攻擊者已能寫入 allowlist 的父層，屆時 bridge 早已失守） |
| 二 | policy 以 **alias 名稱**為鍵而非路徑身分：operator 若把 `meeting-room=` 指向任意目錄，該目錄即取得 danger-full-access + 發佈權。非呼叫端可達提權（alias 表為伺服器端設定），但保證強度成立於名稱層面 | **僅記錄、不改碼**：改以路徑身分為鍵屬設計變更、超出 014 範圍；此為 operator 信任屬性 |
| 三 | 「per-workspace 能力上限」是派送期准入控制，非執行期強制；結案敘述應寫「上限在派送邊界強制」 | **已修改**：docstring 與 README 皆明載此區別，README 並指向「runtime 隔離取決於各 provider CLI 是否強制 sandbox」 |
| 四 | `DEFAULT_WORKSPACE_POLICY.allowed_providers` 為全體 provider，故「下限」在 sandbox 與發佈兩維度成立、在 provider 維度不成立 | **僅記錄、不改碼**：provider 選擇本身不擴權；收窄會破壞 durable 鏈對 codex+agy+claude 的需要。本報告 §2 措辭已對齊 |

另兩項審查者註記（**皆為既有行為、非 014 引入**）：
- `adapters/telegram.py:592` 的 job 完成通知仍輸出 `Workspace: <絕對路徑>`；僅送達已驗證的 `TELEGRAM_ALLOWED_CHAT_ID`，不在 014 的拒絕路徑上。列為後續可精進項。
- Telegram 無選 alias 語法、一律走 default，因此 014 的 alias 選取能力**實際上只有 HTTP 具備**。刻意的保守設計，已補進 README 以免 operator 誤判能力面。

### 9.1 必須更正的一項審查結論（歸屬錯誤）

審查者第 9 項指控 diff 含兩處「明顯超出 014 範圍」的改動：`bridge/claude_runner.py`（`--model` / `--effort`）與 `bridge/github_reporter.py`（`external_publication_enabled` 攔截）。

**此項不成立。** 以 `reports/E500-PRE-014-BRIDGE-BLOB-MANIFEST.txt` 的 blob 逐檔比對：

```
UNCHANGED-SINCE-PRE-014  gpt-codex-bridge/bridge/claude_runner.py
UNCHANGED-SINCE-PRE-014  gpt-codex-bridge/bridge/github_reporter.py
```

兩檔與 014 開始前的快照**逐 byte 相同** — 014 從未編輯它們；它們屬於 012/013 時期既有的未提交工作。誤判原因是審查者讀的是混雜的工作區、而非 014 的差異。這正是「未提交工作與任務變更混在同一棵樹」會系統性造成審查錯誤歸因的實證，也是 §11 主張切割 commit 的直接理由。

真正被 014 改動的既有檔案只有四個：`.env.example`、`README.md`、`adapters/telegram.py`、`bridge/api.py`。

審查全文留存於 `~/.local/state/gpt-codex-bridge/reports/job-f5cca594af824237.json`。

## 10. 審查後複測（docstring／README 修正後）

| 指令 | 結果 |
|---|---|
| `python3 -m unittest discover -s gpt-codex-bridge/tests -t gpt-codex-bridge` | **Ran 178 tests — OK** |
| `python3 -m pytest -q tests`（於 `gpt-codex-bridge`） | **201 passed, 69 subtests passed** |
| `git diff --check` | clean |

## 11. 待辦

1. **014 implementation commit — 尚未執行。** 切割方案已**實測可行**（非僅提議）：於 `/tmp/split014/cand1` 組出「既有工作、完全不含 014」的候選樹（四個混雜檔案還原為 pre-014 快照、`config.py` 退回 HEAD、移除 `workspaces.py`、三個 014 測試檔與標本），結果 **149 passed, 26 subtests / unittest 126 OK，零失敗**。且 201 − 52 = 149、178 − 52 = 126 正好等於 014 新增的 52 個測試，交叉證明切割無遺漏、無隱藏耦合。
   - Commit 1 = 既有未提交的 bridge 控制面工作（含 `observability.py`、`test_api.py`、`test_api_logging.py`）
   - Commit 2 = 014（`workspaces.py`、`config.py` alias 支援、resolver 接點、三個 routing 測試檔、標本、`.env.example`／README 文件）
   - 順序不可反（實測證明，非推斷）：014 對 `api.py` 的 7 個 hunk 以 `pre014-true/api.py` 為基準抽出後，對 `HEAD:bridge/api.py` 以 `patch --fuzz=0` dry-run 為 **7/7 全數 FAILED** — 014 的變動是表達在既有工作之後的內容之上，既有工作必須先落地。
2. 切割後需對**兩個** commit 分別驗證 green（`git archive` 等價隔離方式），並確認 `git status` 對 bridge 路徑全 clean，以保證 commit1 ∪ commit2 恰好等於本輪已測的工作樹——未遺漏、也未悄悄改寫任何檔案。
3. **未 push**；`3a99346` / `17cf2c4` / `ef32b18` 未 amend、未重寫。
4. 015 輸入：Claude／AGY sandbox enforcement parity（§6.3），以及 sandbox 內 bind 遭拒時讓 socket 測試 `skipTest`（§6.1）。
