# gpt-codex-bridge v2

E500 上的持久化 provider job runner。Telegram 負責驗證、提交任務與發送 outbox 通知；唯一 worker 從 SQLite queue 取 job，依保存的 provider 分派至 Codex、本機 Claude Code CLI 或 agy Google OAuth CLI。

```text
Telegram getUpdates (long polling, no inbound port)
             │
             ▼
       chat_id whitelist
             │
             ▼
       SQLite persistent queue
             │
             ▼
       one worker + file lock
             │
             ├── provider=codex → codex exec --sandbox <job.sandbox_mode>
             ├── provider=claude → claude -p <prompt>
             └── provider=agy → agy -p <prompt> --model gemini-3.7-flash-high --output-format json
             │
             ▼
       structured report.json
             │
             ▼
       SQLite notification outbox
             │
             ▼
Telegram adapter drain
```

AI Meeting commands are proxied from this same E500 Telegram polling process to
the remote TUF A16 Meeting Room at `http://10.0.3.67:8000`. `/hermes` and
`/gemini` are Meeting Room requests; `/gpt` starts the durable GPT → AGY →
Claude workflow; `/agy` and `/claude` are independently scheduled local
provider jobs and never call the Meeting Room. `/all` and `/roundtable` remain
Meeting Room operations and may include the remote GPT agent. There must remain
only one Telegram polling process.

未來的 MCP/HTTP adapter 只需要呼叫同一個 `JobQueue.submit()`；adapter 不得各自啟動 Codex。

## Security boundary

- `TELEGRAM_ALLOWED_CHAT_ID` 在解析 message 前比對；未授權更新不回覆、不 enqueue、不執行 Codex。
- `CODEX_ALLOWED_WORKSPACES` 是明確的絕對路徑 allowlist；Telegram 不提供 `cwd`，所有 Telegram job 都使用 `CODEX_DEFAULT_WORKSPACE`。
- job 會持久化 `provider`/`runner`；`/gpt` 建立 `flow-*`，依序排程 Codex、agy、Claude 三個 job；`/claude` 只會 dispatch 到 ClaudeRunner，不會進 Meeting Room 或 CodexRunner。
- `/agy` 只會 dispatch 到 AgyRunner；child environment 會移除 `GEMINI_API_KEY` 與 `GOOGLE_API_KEY`，保留 agy 已存在的 Google OAuth credential。程式不執行 `agy login`。
- worker 使用 SQLite claim guard 加 Unix file lock，最多一個 `running` job。
- 每個 job 在 SQLite 保存自己的 `sandbox_mode`，只允許 `read-only`、`workspace-write`、`danger-full-access`；未知值直接拒絕。
- `/run-full` 只接受 `TELEGRAM_ALLOWED_CHAT_ID`；Codex 使用 argv list、`shell=False`、job-specific `--sandbox`、timeout，程式碼不使用 `--dangerously-bypass-approvals-and-sandbox` 或 `--yolo`。
- Telegram adapter 沒有 raw shell API；`/run` 是 Codex task，不是 shell command endpoint。
- `/claude` 使用 `claude -p <prompt>`（argv-based，`shell=False`）；不使用 `--dangerously-skip-permissions`，也不啟動第二個 Telegram polling process。
- worker 不呼叫 Telegram API；job terminal update 與 `notifications.pending` 建立在同一個 SQLite transaction。
- Telegram adapter 定期 drain pending notifications；網路、5xx、429 等暫時性失敗會保留 retryable row，永久 Telegram 4xx 則保留原 row 與診斷並標記 `dead_lettered_at`，不再無限重試。
- 功能啟用前已 terminal 的歷史 job 不會被回補通知；notification outbox 只在新的 terminal transition 時建立。
- Bot token 與未來 MCP bearer token 只從 environment/systemd credentials 讀取，不寫入 Git、log 或 report；傳給 Codex 的 child environment 會移除 inbound adapter credentials。
- 不開 inbound TCP port。Telegram 啟動時會呼叫 `deleteWebhook`，之後使用官方 `getUpdates` long polling。

## Files

```text
bridge/
  config.py          validated environment configuration
  models.py          Job model
  sandbox.py         validated per-job sandbox modes
  queue.py           SQLite persistence and atomic claim
  codex_runner.py    fixed sandboxed Codex subprocess + report validation
  claude_runner.py   safe non-interactive Claude Code subprocess + report validation
  agy_runner.py      safe agy Google OAuth subprocess + JSON response parsing
  github_reporter.py report-only GitHub Markdown publisher via gh
  worker.py          single worker loop and process lock
  meeting.py         async HTTP client for the remote TUF A16 Meeting Room
adapters/
  telegram.py        getUpdates + Codex/Meeting commands; never starts Codex or Meeting agents
schemas/
  codex_report.schema.json
scripts/
  run-worker.sh
  run-telegram.sh
  smoke-test.sh
tests/
  test_queue.py
  test_security.py
  test_codex_runner.py
  test_telegram_adapter.py
  test_meeting.py
  test_worker.py
```

## Configure and run

Copy `.env.example` as a reference, but keep real credentials in a mode `0600` systemd `EnvironmentFile` or equivalent credential store. The application does not automatically load `.env`.

At minimum configure:

```bash
export CODEX_ALLOWED_WORKSPACES="$HOME/project/e500"
export CODEX_DEFAULT_WORKSPACE="$HOME/project/e500"
export TELEGRAM_BOT_TOKEN='read-from-your-secret-store'
export TELEGRAM_ALLOWED_CHAT_ID='123456789'
```

Start the two processes separately on E500:

```bash
cd ~/project/gpt-codex-bridge
scripts/run-worker.sh
scripts/run-telegram.sh
```

The worker can be started without Telegram credentials for local queue testing. Telegram requires both the bot token and numeric allowed chat ID.

Supported Telegram commands:

```text
/start
/ping
/status
/run <task>
/gpt <task>
/run-read <task>
/run-full <task>
/result <job_id>
/workflow <flow_id>
/claude <task>
/agy <task>

/hermes <message>
/gemini <message>
/all <message>
/roundtable <message>
/agents
/meeting-status
/meeting-stop
/meeting-reset
```

Meeting requests use an async `httpx.AsyncClient` with a 5-second connect
timeout and a 330-second read timeout. Configure the remote room with:

```env
MEETING_ROOM_URL=http://10.0.3.67:8000
MEETING_API_TOKEN=
```

Keep `MEETING_API_TOKEN` in the mode-0600 runtime environment only. It is not
logged, committed, sent to Telegram, or passed to Codex child processes. The
Meeting Room being offline does not prevent the E500 Bot, `/run*`, `/status`, or
`/result` from starting and working.

The optional authenticated HTTP API uses `CODEX_API_HOST` / `CODEX_API_PORT` and
requires a random `CODEX_BRIDGE_API_TOKEN` of at least 32 characters. It exposes
`GET /health`, `GET /status`, `GET /result/<job_id>`, `GET /workflow/<flow_id>`,
`POST /run`, and `POST /workflow`; all routes
require `Authorization: Bearer <CODEX_BRIDGE_API_TOKEN>`. API-submitted jobs use
the configured Telegram chat ID and are automatically delivered by the same
Telegram notification outbox when the worker finishes.

`/gpt <task>` automatically schedules the bounded sequence

```text
Codex implementation → AGY review → Claude finalization → GitHub Markdown report
```

Each stage produces its normal Telegram completion notification. When
`GITHUB_REPORT_ENABLED=true`, the bridge uses the existing authenticated `gh`
session to upload only a redacted Markdown report under
`GITHUB_REPORT_DIRECTORY`; it does not automatically push source-code changes.

Example:

```text
/run 修復目前 repo 的 pytest 錯誤並執行測試
```

The queue stores the prompt, fixed allowlisted workspace, and per-job sandbox mode in the private state directory (by default `~/.local/state/gpt-codex-bridge`). Reports are stored there as `job-*.json` with mode `0600`; both the SQLite row and report record `sandbox_mode`.

## Codex command contract

Each job uses the equivalent of:

```bash
codex exec \
  --sandbox "$SANDBOX_MODE" \
  -C "$WORKSPACE" \
  --output-schema schemas/codex_report.schema.json \
  -o "$REPORT_JSON" \
  "$PROMPT"
```

`report.json` must contain only:

```json
{
  "status": "success | partial | failed",
  "summary": "string",
  "changed_files": ["string"],
  "tests": [{
    "command": "string",
    "result": "pass | fail",
    "output_summary": "string"
  }],
  "git_status": "string",
  "needs_attention": false,
  "sandbox_mode": "read-only | workspace-write | danger-full-access"
}
```

When a job reaches a terminal state, the Telegram adapter automatically reads
this structured report and sends a safe result summary to the originating chat.
`/result <job_id>` remains available for historical queries; neither path
parses natural-language Codex stdout.

Claude jobs use the same report and notification outbox contract, while the
notification explicitly labels `Provider: claude` and `Runner: claude`. Claude
stdout/stderr is captured by the worker, redacted, and converted to a safe
success or failure summary.

## Tests

The test suite uses Python plus the declared `httpx` dependency and mocks
Codex/Telegram/Meeting Room transport:

```bash
scripts/smoke-test.sh
```

It covers queue persistence/recovery, concurrency `=1`, chat ID whitelist, workspace allowlist, raw-shell rejection, all sandbox commands, invalid-mode rejection, full-access authorization, timeout termination, exact argv/sandbox flags, secret redaction, mock Telegram API, worker lock, Meeting Room URL routing, bearer headers, status/error handling, partial responses, and Telegram message splitting. The script also runs Python compile checks, shell syntax checks, and `git diff --check`.

Live Telegram is intentionally not called by the test suite. A real Codex smoke test should use an isolated temporary Git repository and only be run explicitly when E500 Codex authentication is available; it must verify a file on disk rather than trusting the model's final text.

## Original clipboard bridge

The original local X11 clipboard tools remain available:

```text
ChatGPT Web ── Ctrl+C ──→ X11 Clipboard ──→ gptclip / gpt2codex
Terminal stdout ──→ term2gpt ──→ X11 Clipboard ──→ ChatGPT Web Ctrl+V
```

Install its `bin/` PATH entry with:

```bash
./install.sh
source ~/.bashrc
```
