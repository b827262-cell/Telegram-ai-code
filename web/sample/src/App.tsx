import { FormEvent, useMemo, useState } from "react";
import { ControlApiError, Provider, RunMode, controlApi } from "./api";

type SemanticStatus = "queued" | "running" | "succeeded" | "failed" | "blocked" | "idle";

type Stage = {
  id: string;
  label: string;
  detail: string;
  status: SemanticStatus;
};

const sampleStages: Stage[] = [
  { id: "01", label: "INTAKE", detail: "Task validated", status: "succeeded" },
  { id: "02", label: "CODEX", detail: "Implementation", status: "running" },
  { id: "03", label: "AGY", detail: "Review pending", status: "queued" },
  { id: "04", label: "CLAUDE", detail: "Finalization", status: "queued" },
  { id: "05", label: "REPORT", detail: "GitHub redacted report", status: "queued" },
  { id: "06", label: "NOTIFY", detail: "Telegram completion", status: "queued" },
];

const statusSymbol: Record<SemanticStatus, string> = {
  queued: "○",
  running: "◩",
  succeeded: "✓",
  failed: "!",
  blocked: "⊘",
  idle: "·",
};

function StatusPill({ status, label }: { status: SemanticStatus; label?: string }) {
  return (
    <span className={`status-pill status-${status}`}>
      <span aria-hidden="true">{statusSymbol[status]}</span>
      {label || status}
    </span>
  );
}

function App() {
  const [provider, setProvider] = useState<Provider>("codex");
  const [mode, setMode] = useState<RunMode>("write");
  const [loop, setLoop] = useState(true);
  const [task, setTask] = useState("檢查 Worker runtime，修正啟動問題並回報驗證結果。" );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("Sample mode · 尚未連線到 production BFF");
  const [workerStatus, setWorkerStatus] = useState<SemanticStatus>("blocked");
  const [counts, setCounts] = useState({ succeeded: 54, failed: 3, queued: 1, running: 0 });

  const total = useMemo(
    () => counts.succeeded + counts.failed + counts.queued + counts.running,
    [counts],
  );

  async function refreshStatus() {
    setBusy(true);
    try {
      const data = await controlApi.status();
      setCounts({
        succeeded: data.counts.succeeded || 0,
        failed: data.counts.failed || 0,
        queued: data.counts.queued || 0,
        running: data.counts.running || 0,
      });
      setWorkerStatus(data.counts.running > 0 ? "running" : "idle");
      setNotice(`Live API · ${data.recent.length} recent jobs · ${data.workflows.length} workflows`);
    } catch (error) {
      const message = error instanceof ControlApiError ? `${error.code}: ${error.message}` : "Unknown status error";
      setWorkerStatus("blocked");
      setNotice(message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const cleanTask = task.trim();
    if (!cleanTask || busy) return;

    if (mode === "full" && !window.confirm("danger-full-access 可能修改系統或工作區外資源。確定送出？")) {
      return;
    }

    setBusy(true);
    try {
      if (loop) {
        const data = await controlApi.workflow(cleanTask, mode);
        setNotice(`Workflow queued: ${data.workflow.id}`);
      } else {
        const data = await controlApi.run(cleanTask, provider, mode);
        setNotice(`Job queued: ${data.job.id}`);
      }
      await refreshStatus();
    } catch (error) {
      const message = error instanceof ControlApiError ? `${error.code}: ${error.message}` : "Unknown dispatch error";
      setNotice(message);
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="left-rail" aria-label="Primary navigation">
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">E5</div>
          <div>
            <strong>E500</strong>
            <span>Control Plane</span>
          </div>
        </div>

        <button className="new-task-button" type="button">＋ New task</button>

        <nav className="nav-list">
          <button className="nav-item active" type="button"><span>⌁</span> Command Console</button>
          <button className="nav-item" type="button"><span>◇</span> Workflows <em>1</em></button>
          <button className="nav-item" type="button"><span>↻</span> Automations</button>
          <button className="nav-item" type="button"><span>✦</span> Skills</button>
        </nav>

        <div className="rail-section">
          <div className="section-label">WORKSPACES</div>
          <button className="workspace-item selected" type="button">
            <span className="workspace-dot" /> Telegram-ai-code
          </button>
          <button className="workspace-item" type="button">
            <span className="workspace-dot muted" /> e500-codex-smoke
          </button>
        </div>

        <div className="rail-footer">
          <span className="mini-avatar">H</span>
          <div><strong>Developer</strong><span>local workspace</span></div>
          <button aria-label="Settings" type="button">•••</button>
        </div>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <div>
            <div className="eyebrow">Telegram-ai-code / web sample</div>
            <h1>Worker runtime recovery</h1>
          </div>
          <div className="topbar-actions">
            <StatusPill status={workerStatus} label={`Worker ${workerStatus}`} />
            <button className="secondary-button" type="button" onClick={refreshStatus} disabled={busy}>
              {busy ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </header>

        <section className="context-strip" aria-label="Execution context">
          <div><span>Workspace</span><strong>Telegram-ai-code</strong></div>
          <div><span>Branch</span><code>main</code></div>
          <div><span>Sandbox</span><strong>{mode}</strong></div>
          <div><span>Queue</span><strong>{counts.queued}</strong></div>
        </section>

        <section className="task-stream">
          <article className="message user-message">
            <div className="message-meta"><span className="avatar">H</span><strong>You</strong><time>08:51</time></div>
            <p>確認 Worker 為何 inactive，恢復 queue 消費後驗證 workflow，再從 Telegram 做完整 /gpt loop。</p>
          </article>

          <article className="message agent-message">
            <div className="message-meta"><span className="agent-avatar">C</span><strong>Codex</strong><StatusPill status="running" /></div>
            <p>正在檢查 systemd unit、WorkingDirectory、EnvironmentFile 與 worker process。現有 backend dirty files 維持不動。</p>
            <div className="command-card">
              <div className="command-head"><span>Runtime check</span><StatusPill status="running" /></div>
              <code>systemctl --user status gpt-codex-worker.service --no-pager -l</code>
              <code>pgrep -af 'python3 -m bridge.worker'</code>
            </div>
          </article>

          <section className="progress-card" aria-labelledby="workflow-title">
            <div className="card-heading">
              <div><span className="eyebrow">WORKFLOW</span><h2 id="workflow-title">flow-sample-runtime</h2></div>
              <StatusPill status="running" />
            </div>
            <div className="stage-list">
              {sampleStages.map((stage) => (
                <div className="stage-row" key={stage.id}>
                  <span className={`stage-marker marker-${stage.status}`} aria-hidden="true">{statusSymbol[stage.status]}</span>
                  <span className="stage-id">{stage.id}</span>
                  <div><strong>{stage.label}</strong><span>{stage.detail}</span></div>
                  <StatusPill status={stage.status} />
                </div>
              ))}
            </div>
          </section>
        </section>

        <form className="composer" onSubmit={submit}>
          <textarea
            value={task}
            onChange={(event) => setTask(event.target.value)}
            aria-label="Task"
            placeholder="Describe a task for the coding agents…"
            rows={3}
          />
          <div className="composer-footer">
            <div className="composer-options">
              <label>
                <span>Provider</span>
                <select value={provider} onChange={(event) => setProvider(event.target.value as Provider)} disabled={loop}>
                  <option value="codex">Codex</option>
                  <option value="agy">AGY</option>
                  <option value="claude">Claude</option>
                </select>
              </label>
              <label>
                <span>Mode</span>
                <select value={mode} onChange={(event) => setMode(event.target.value as RunMode)}>
                  <option value="read">read-only</option>
                  <option value="write">workspace-write</option>
                  <option value="full">danger-full-access</option>
                </select>
              </label>
              <label className="toggle-label">
                <input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} />
                <span>GPT loop</span>
              </label>
            </div>
            <button className="send-button" type="submit" disabled={busy || !task.trim()} aria-label="Dispatch task">
              ↑
            </button>
          </div>
          <div className="composer-notice" role="status">{notice}</div>
        </form>
      </main>

      <aside className="inspector" aria-label="Execution inspector">
        <section className="inspector-section">
          <div className="section-heading"><h2>Agents</h2><span>3 providers</span></div>
          <div className="agent-row"><span className="agent-key">GPT</span><div><strong>Codex</strong><span>primary writer</span></div><StatusPill status="running" /></div>
          <div className="agent-row"><span className="agent-key">AGY</span><div><strong>AGY</strong><span>review gate</span></div><StatusPill status="queued" /></div>
          <div className="agent-row"><span className="agent-key">CL</span><div><strong>Claude</strong><span>finalizer</span></div><StatusPill status="queued" /></div>
        </section>

        <section className="inspector-section">
          <div className="section-heading"><h2>Job lifecycle</h2><span>{total} total</span></div>
          <div className="metric-grid">
            <div><span>Succeeded</span><strong>{counts.succeeded}</strong></div>
            <div><span>Failed</span><strong>{counts.failed}</strong></div>
            <div><span>Queued</span><strong>{counts.queued}</strong></div>
            <div><span>Running</span><strong>{counts.running}</strong></div>
          </div>
        </section>

        <section className="inspector-section">
          <div className="section-heading"><h2>Changed files</h2><span>sample</span></div>
          <div className="file-row"><span>M</span><code>bridge/worker.py</code></div>
          <div className="file-row"><span>T</span><code>tests/test_worker.py</code></div>
          <div className="file-row"><span>R</span><code>reports/runtime.md</code></div>
        </section>

        <section className="inspector-section log-section">
          <div className="section-heading"><h2>Runtime</h2><StatusPill status="blocked" label="worker offline" /></div>
          <pre>{`service  gpt-codex-worker\nstate    inactive / dead\nqueue    1 queued\nrunning  0\n\nNext: verify unit → start → claim`}</pre>
        </section>
      </aside>
    </div>
  );
}

export default App;
