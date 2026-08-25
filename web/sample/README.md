# Codex-style Control Plane Sample

此資料夾是 Telegram-ai-code Web Control Plane 的 reference implementation。

設計方向：

- 左側 workspace / workflow navigation。
- 中央 task thread + command composer。
- 右側 Agent / lifecycle / queue inspector。
- Codex、AGY、Claude 三 Agent 狀態。
- 01–06 workflow stage。
- queued / running / succeeded / failed / blocked 狀態。
- 預設使用 sample data；可切換到 same-origin `/api`。

> 這是 Telegram-ai-code 自有 UI 樣板，只參考 Codex 的工作模型與資訊層級，不複製 OpenAI 品牌素材。

## Run

```bash
npm install
npm run dev
```

Build gate：

```bash
npm run typecheck
npm run build
```

## API

預設：

```env
VITE_CONTROL_API_BASE=/api
```

前端**不接受** Bridge bearer token。Production 應由 BFF / reverse proxy 在 server-side 注入 `Authorization: Bearer ...`。

參考 API：

```text
GET  /api/health
GET  /api/status
GET  /api/result/:job_id
GET  /api/workflow/:flow_id
POST /api/run
POST /api/workflow
```

完整規格請讀 `../STANDARD.md`。
