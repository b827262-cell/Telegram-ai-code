# Telegram-ai-code Web Control Plane Standard v1

## 1. 目的

本規格定義 Telegram-ai-code Web Control Plane 的長期前端基準。介面語言參考 Codex「agent command center」的工作方式：工作區導覽、長任務狀態、多 Agent、執行進度、變更檔案與輸入 composer；不做 OpenAI 品牌、商標或私有 UI 的像素級複製。

## 2. 資訊架構

桌面版採三區：

```text
┌──────────────┬──────────────────────────────────┬────────────────────┐
│ Left rail    │ Main task / workflow             │ Inspector          │
│ 220–260 px   │ flexible                         │ 320–380 px         │
│              │                                  │                    │
│ New task     │ Workspace / branch / runtime     │ Agent status       │
│ Workflows    │ Task thread / progress           │ Lifecycle 01–06    │
│ Automations  │ Result / changed files summary   │ Queue / logs       │
│ Skills       │ Composer                         │ Error detail       │
└──────────────┴──────────────────────────────────┴────────────────────┘
```

小於 1180px：Inspector 變為抽屜。

小於 760px：Left rail 與 Inspector 都變為抽屜，Main 佔滿寬度。

## 3. 核心畫面

### 3.1 Command Console

必須支援：

- 單一 Codex job：`/run` 對應。
- sandbox：`read-only / workspace-write / danger-full-access`。
- provider：Codex / AGY / Claude。
- GPT loop：Codex → AGY → Claude → GitHub report。
- task 長度與錯誤訊息。
- 防重複 Codex exec 的 `CODEX_EXEC_RUNNING` 狀態。

### 3.2 Workflow 01–06

標準六段：

1. `INTAKE` — 任務接收與驗證。
2. `CODEX` — 主實作或執行。
3. `AGY` — review / 反證。
4. `CLAUDE` — finalization。
5. `REPORT` — redacted GitHub Markdown report。
6. `NOTIFY` — Telegram / Control Plane 完成回報。

每段最少顯示：名稱、狀態、開始/結束時間、錯誤摘要。進行中可動畫，但必須遵守 `prefers-reduced-motion`。

### 3.3 Job Lifecycle

狀態只允許下列 UI 語意：

- `queued`：等待 worker claim。
- `running`：已由 worker 執行。
- `succeeded`：成功完成。
- `failed`：失敗，可查看 error / result。
- `blocked`：因安全、重複執行或人工 gate 阻擋。

顏色只能作為輔助；同時必須有文字、圖示或 shape。

## 4. 視覺規格

### 4.1 Design tokens

所有尺寸與顏色集中在 CSS custom properties，不在 component 內散落 magic values。

```css
:root {
  --bg: #f7f7f5;
  --panel: #ffffff;
  --panel-muted: #f1f1ee;
  --text: #1f1f1d;
  --text-muted: #6f6f69;
  --border: #deded9;
  --accent: #11110f;
  --success: #1f7a52;
  --warning: #9a6700;
  --danger: #b42318;
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 18px;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
}
```

### 4.2 排版

- UI font：system sans-serif。
- log / job id / branch / command：system monospace。
- body 基準：14px–15px。
- 主要標題：20px–24px，避免 dashboard 過度放大。
- 卡片陰影保持極輕；主要依靠 border、spacing、surface 層級。

### 4.3 動畫

- running：最多使用低頻 pulse / blink。
- 預設 transition 120–180ms。
- 禁止持續旋轉、快速閃爍或大量位移。
- `prefers-reduced-motion: reduce` 時停用非必要動畫。

## 5. 前端架構

Reference stack：

- React 18
- TypeScript strict
- Vite
- 原生 CSS / design tokens
- fetch-based API client

推薦 production 結構：

```text
web/
├── STANDARD.md
├── sample/
└── app/
    ├── src/
    │   ├── components/
    │   ├── features/
    │   │   ├── console/
    │   │   ├── jobs/
    │   │   ├── workflows/
    │   │   └── runtime/
    │   ├── lib/
    │   ├── types/
    │   └── styles/
    └── ...
```

`sample/` 不作為 production bundle 的 implicit dependency。

## 6. API 邊界

Bridge 現有 API：

```text
GET  /health
GET  /status
GET  /result/:job_id
GET  /workflow/:flow_id
POST /run
POST /workflow
```

Web 標準使用 same-origin BFF：

```text
Browser
  ↓
/api/health
/api/status
/api/result/:job_id
/api/workflow/:flow_id
/api/run
/api/workflow
  ↓ server-side only
Bridge API :4300 + Authorization: Bearer <secret>
```

### 禁止

- 禁止 `VITE_CODEX_BRIDGE_API_TOKEN`。
- 禁止 localStorage / sessionStorage 保存 Bridge bearer token。
- 禁止把 Telegram bot token 放在 HTML / JS / source map。
- 禁止讓 Browser 直接連 `127.0.0.1:4300` 並攜帶 secret。

### Request 標準

前端只傳業務欄位：

```json
{
  "task": "...",
  "provider": "codex",
  "mode": "write"
}
```

BFF 負責：authentication、allowed user / session、Bearer injection、timeout、error normalization、audit metadata。

## 7. Error contract

UI 至少識別：

- `BRIDGE_UNAUTHORIZED`
- `USER_NOT_ALLOWED`
- `REQUEST_INVALID`
- `CODEX_EXEC_RUNNING`
- `JOB_NOT_FOUND`
- `WORKFLOW_NOT_FOUND`
- `BRIDGE_ERROR`
- `NETWORK_ERROR`

錯誤訊息顯示人類可讀摘要；原始敏感 payload 不直接 dump 到 DOM。

## 8. 安全規格

- Content Security Policy 優先採 same-origin。
- 禁止 `dangerouslySetInnerHTML` 顯示模型輸出或 logs。
- job / flow id 放入 URL 前必須 `encodeURIComponent`。
- 顯示 report 時沿用 backend redaction，不在 client 嘗試還原資料。
- `danger-full-access` 必須有二次確認與清楚的風險標示。
- Web 不得繞過 Telegram chat allowlist / workspace allowlist / sandbox policy。
- Production 建議 BFF 綁定登入 session，再由 BFF 連本機 Bridge。

## 9. Accessibility

最低要求：

- keyboard 可完成主要流程。
- 所有 icon-only button 有 `aria-label`。
- focus 狀態可見。
- status 不只靠顏色。
- form element 有 label。
- modal / drawer 正確管理 focus。
- 文字與背景達 WCAG AA 對比。

## 10. Responsive

```text
>= 1180px  三欄完整顯示
760–1179px Left + Main；Inspector drawer
< 760px    Main；Left / Inspector drawer
```

Composer 在 mobile 不可覆蓋 workflow 內容；textarea 最少 44px 高的可操作區。

## 11. Testing Gate

每個 production Web change 最少通過：

```bash
npm run typecheck
npm run build
```

正式 app 建議再加入：

```text
unit: Vitest + Testing Library
E2E: Playwright
accessibility: axe
```

關鍵 E2E：

1. status 載入成功 / 失敗。
2. run job 提交。
3. workflow 提交。
4. queued → running → terminal state。
5. `CODEX_EXEC_RUNNING`。
6. job / workflow lookup 404。
7. BFF 未授權時前端不洩漏 token。

## 12. Git / 變更規格

- Web 工作不得 reset / clean backend dirty files。
- 新 UI 先在 feature branch 開發。
- `web/sample/` 只作 reference；production 修改進 `web/app/`。
- 不直接重建或 force-update `main`。
- commit scope 建議：`web:` / `web-ui:` / `web-api:`。

## 13. Definition of Done

Web feature 完成需同時滿足：

- 視覺符合本規格。
- desktop / tablet / mobile 可用。
- keyboard 基本操作正常。
- `typecheck`、`build` 通過。
- secret 不進 browser bundle。
- API error 有明確 UI。
- backend 安全 gate 不被繞過。
- 有 changed files 與驗收摘要。
