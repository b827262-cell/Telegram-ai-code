export type Provider = "codex" | "agy" | "claude";
export type RunMode = "read" | "write" | "full";

export interface JobSummary {
  id: string;
  status: string;
  provider: string;
  runner: string;
  sandbox_mode: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  exit_code: number | null;
  workflow_id: string | null;
  workflow_stage: string | null;
  workflow_order: number | null;
}

export interface WorkflowSummary {
  id: string;
  status: string;
  current_stage: string | null;
  created_at: string | null;
  finished_at: string | null;
  github_url: string | null;
  github_status: string | null;
  error: string | null;
  jobs: JobSummary[];
}

export interface StatusResponse {
  ok: boolean;
  counts: Record<string, number>;
  recent: JobSummary[];
  workflows: WorkflowSummary[];
}

export class ControlApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ControlApiError";
  }
}

const base = (import.meta.env.VITE_CONTROL_API_BASE || "/api").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
      credentials: "same-origin",
    });
  } catch {
    throw new ControlApiError("NETWORK_ERROR", "Control API 無法連線。", 0);
  }

  let body: Record<string, unknown> = {};
  try {
    body = (await response.json()) as Record<string, unknown>;
  } catch {
    // Normalize non-JSON upstream errors without exposing raw proxy output.
  }

  if (!response.ok) {
    const code = typeof body.code === "string" ? body.code : "HTTP_ERROR";
    const message = typeof body.message === "string" ? body.message : `Control API HTTP ${response.status}`;
    throw new ControlApiError(code, message, response.status);
  }

  return body as T;
}

export const controlApi = {
  status: () => request<StatusResponse>("/status"),
  run: (task: string, provider: Provider, mode: RunMode) =>
    request<{ ok: boolean; job: JobSummary }>("/run", {
      method: "POST",
      body: JSON.stringify({ task, provider, mode }),
    }),
  workflow: (task: string, mode: RunMode) =>
    request<{ ok: boolean; workflow: WorkflowSummary; job: JobSummary }>("/workflow", {
      method: "POST",
      body: JSON.stringify({ task, mode }),
    }),
  result: (jobId: string) => request(`/result/${encodeURIComponent(jobId)}`),
  workflowById: (flowId: string) => request(`/workflow/${encodeURIComponent(flowId)}`),
};
