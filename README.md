# Telegram-ai-code

Telegram-ai-code 是一個在 Ubuntu / E500 工作站上運行的 **Telegram 控制平面與多 Agent AI 協作編程專案**。

GitHub：<https://github.com/b827262-cell/Telegram-ai-code>

預設分支：`main`

本專案把 Telegram 當成任務入口，將 job 排入持久化 queue，再由單一 worker 安全地執行 Codex、AGY 與 Claude。程式修改、測試、結果摘要與 Git safety 都保留可追蹤的生命週期。

目前以 Telegram 作為互動入口，透過 Meeting Room API 將任務路由至 GPT / Codex、Hermes、Gemini，讓不同 Agent 分工進行程式實作、Linux / Runtime 審查與 API / Logic 審查，再由測試與 Live E2E 驗收確認結果。

## 專案目標

建立一套可實際運作的多方 AI 編程流程：

```text
使用者 / Telegram
        │
        ▼
  gpt-codex-bridge
        │
        ▼
   Meeting Room API
        │
        ├── GPT / Codex  → 主實作 / 程式設計
        ├── Hermes       → Linux / Runtime / systemd 審查
        └── Gemini       → API / Schema / Logic 審查
        │
        ▼
     測試與整合
        │
        ▼
 Telegram Live Acceptance
```

核心不是讓三個 AI 同時修改同一份程式，而是：

> **一個負責寫、兩個負責挑錯、測試與 Live E2E 負責證明結果。**

## Agent 分工

| Agent | 主要角色 | 適合工作 |
|---|---|---|
| GPT / Codex | 主程式設計師 / 執行者 | Python、Bash、API、重構、測試、deployment |
| Hermes | 系統工程師 / Runtime Reviewer | Linux、systemd、process、journal、權限、環境變數 |
| Gemini | API / Logic Reviewer | OpenAPI、payload schema、edge cases、反證、regression review |

詳細流程請見：[AI_MULTI_AGENT_PROGRAMMING.md](AI_MULTI_AGENT_PROGRAMMING.md)

## Telegram 指令

| 指令 | 用途 |
|---|---|
| `/ping` | Bot / worker 健康檢查 |
| `/run <task>` | 建立單一 Codex job |
| `/run-read <task>` | 以 read-only sandbox 執行 Codex |
| `/run-full <task>` | 以完整權限模式執行；只允許 allowlist chat 使用 |
| `/gpt <task>` | Codex → AGY → Claude → GitHub redacted report |
| `/agy <task>` | 建立 AGY review job |
| `/claude <task>` | 建立 Claude final job |
| `/status` | 查詢 queue 與 running job |
| `/result <job_id>` | 查詢單一 job 結果 |
| `/workflow <flow_id>` | 查詢完整 loop 流程 |

### GPT loop

`/gpt` 會建立一個可追蹤的 workflow，依序執行：

```text
GPT / Codex implementation
        ↓
AGY review
        ↓
Claude finalization
        ↓
redacted GitHub Markdown report
```

每一階段都會寫入 SQLite job 狀態，並透過 Telegram notification outbox 自動回報。若已有 Codex exec 執行中，新的 `/gpt` 會先被擋下並回報目前 running job，避免兩個 Codex 任務同時修改 workspace。

## 快速開始

建立 Python 環境並安裝 bridge：

```bash
cd gpt-codex-bridge
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

複製設定範例並填入私密環境：

```bash
cp .env.example .env
chmod 600 .env
```

至少需要設定：

```env
TELEGRAM_BOT_TOKEN=<from-secret-store>
TELEGRAM_ALLOWED_CHAT_ID=<numeric-chat-id>
CODEX_ALLOWED_WORKSPACES=/absolute/path/to/allowed/workspace
CODEX_DEFAULT_WORKSPACE=/absolute/path/to/default/workspace
```

實際部署時，請使用 systemd `EnvironmentFile` 或其他 secret store；不要把真實 token 寫入 Git。

分別啟動 worker 與 Telegram adapter：

```bash
cd gpt-codex-bridge
scripts/run-worker.sh
scripts/run-telegram.sh
```

## 測試與驗收

```bash
cd gpt-codex-bridge
pytest -q
```

執行中的服務應維持單一 Telegram polling process：

```bash
pgrep -af 'python3 -m adapters.telegram'
systemctl --user status gpt-codex-telegram.service --no-pager -l
```

健康檢查只能確認服務可達；完整驗收仍應從 Telegram 實際執行 `/ping`、`/run` 或 `/gpt`，再用 `/status`、`/result`、`/workflow` 查詢結果。

## 已完成的 Live E2E 驗收

2026-08-23 已完成 Telegram → Meeting Room → Agent → Telegram 的三路實際驗收：

```text
/gpt Reply exactly GPT_OK
→ 🟢 GPTGPT_OK

/hermes Reply exactly HERMES_OK
→ ☤ HermesHERMES_OK

/gemini Reply exactly GEMINI_OK
→ 🔵 GeminiGEMINI_OK
```

目前三個 Agent 的核心 E2E 路徑均可正常運作。

## 已排除的主要問題

### 1. Empty Bearer Token

症狀：

```text
httpx.LocalProtocolError: Illegal header value b'Bearer '
```

根因：`MEETING_API_TOKEN` 存在但值為空，導致 request header 變成：

```text
Authorization: Bearer
```

修正後 runtime token 可正常載入，且不再出現 illegal header。

### 2. Meeting API HTTP 422

症狀：`POST /api/ask/gpt` 回傳 HTTP 422。

Meeting API schema 要求：

```text
chat_id: string
user_id: string
message: string
```

Telegram 原始 `chat.id` 與 `from.id` 為 integer，因此 adapter 需在 payload builder 中正規化：

```python
payload = {
    "chat_id": str(chat_id),
    "user_id": str(user_id),
    "message": message,
}
```

Meeting API schema 本身不需放寬。

## Runtime 原則

目前 Telegram bridge 的 live runtime 以 smoke workspace 為基準：

```text
~/project/e500-codex-smoke/gpt-codex-bridge
```

systemd user service：

```text
gpt-codex-telegram.service
```

基本驗證：

```bash
systemctl --user show gpt-codex-telegram.service \
  -p ActiveState \
  -p SubState \
  -p MainPID \
  -p NRestarts \
  -p WorkingDirectory \
  -p EnvironmentFiles
```

確認 Telegram polling process：

```bash
pgrep -af 'python3 -m adapters.telegram'
```

正常應維持單一 polling process。

## 多方編程流程

建議每個任務使用以下生命週期：

```text
1. Task Definition
2. GPT / Codex 主責分析
3. Hermes Runtime Review
4. Gemini API / Logic Review
5. Decision / Conflict Resolution
6. Codex 單一寫入與整合
7. Unit / Integration Tests
8. Service Validation
9. Telegram Live E2E
```

### 單一寫入者原則

同一時間只允許一個 Agent 修改主程式碼。

建議：

```text
Writer:     GPT / Codex
Reviewer 1: Hermes
Reviewer 2: Gemini
```

避免三個 Agent 同時修改同一檔案造成：

- patch conflict
- overwrite
- regression 無法歸因
- API contract 被不同方向同時修改

## Telegram 驗收範例

```text
/gpt Reply exactly GPT_OK
/hermes Reply exactly HERMES_OK
/gemini Reply exactly GEMINI_OK
```

API `/health` 或 `/api/agents` HTTP 200 只能證明 backend 可達，**不能取代 Telegram inbound/outbound Live E2E**。

## 測試

Python 專案建議：

```bash
pytest -q
```

Service 層：

```bash
systemctl --user status gpt-codex-telegram.service --no-pager -l
```

驗收時至少確認：

```text
ActiveState=active
SubState=running
NRestarts=0
polling process=1
```

## Secret 與 `.env` 管理

**禁止將 `.env`、API key、Telegram token、Meeting API token commit 到 Git。**

建議：

```bash
chmod 600 .env
```

Agent 或診斷輸出只應回報：

```text
MEETING_API_TOKEN: present=yes, length=<N>
```

不得顯示實際 secret。

Provider credential 應由各自 runtime/service 管理，不要全部集中到 Telegram bridge：

```text
Telegram bridge
└── Meeting API credential

GPT / Codex worker
└── GPT / Codex provider auth

Hermes gateway
└── Hermes provider auth

Gemini provider
└── Gemini provider auth
```

## 建議 Git Workflow

每個任務使用獨立 branch：

```bash
git switch -c task/MR-001
```

修改完成：

```bash
git status
git diff
pytest -q
```

commit：

```bash
git add <changed-files>
git commit -m "fix: <summary>"
```

大型修改建議使用 Pull Request，讓多 Agent review 結果可被保留與追蹤。

## 專案狀態

目前完成：

- [x] Telegram bridge live deployment
- [x] Meeting API authentication
- [x] GPT route Live E2E
- [x] Hermes route Live E2E
- [x] Gemini route Live E2E
- [x] Telegram integer ID → Meeting API string schema normalization
- [x] 單一 Telegram polling process
- [x] 多 Agent 編程流程定義

下一階段可加入：

- [ ] 任務 ID / Meeting trace ID
- [ ] Reviewer structured output
- [ ] 自動化 decision table
- [ ] Git branch / PR 自動建立
- [ ] CI 測試與 regression gate
- [ ] Agent usage / latency / failure metrics

## 文件

- [多 Agent 編程流程](AI_MULTI_AGENT_PROGRAMMING.md)

## License

目前尚未指定 License。若要公開供其他人使用，建議後續加入適合的開源授權。
