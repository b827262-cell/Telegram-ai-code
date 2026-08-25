# Telegram-ai-code Web

`web/` 是 Telegram-ai-code 的 Web Control Plane 開發區。

目前規格基準：

- `STANDARD.md`：Web UI / UX、API、安全、測試與 Git 規格。
- `sample/`：Codex-style Control Plane 參考樣板。

## 原則

1. `web/sample/` 是視覺與互動的 reference implementation，不直接改動 backend。
2. 不在瀏覽器 bundle 中保存 `CODEX_BRIDGE_API_TOKEN`、Telegram token 或其他 secret。
3. Browser 只呼叫 same-origin `/api/*`；由 server-side BFF / reverse proxy 注入 Bridge Bearer token。
4. UI 必須完整呈現 `queued / running / succeeded / failed / blocked` 狀態，不能只靠顏色區分。
5. 新功能先在 sample 驗證，再移植至 production Web Control Plane。

開發：

```bash
cd web/sample
npm install
npm run dev
```

詳見 [STANDARD.md](STANDARD.md) 與 [sample/README.md](sample/README.md)。
