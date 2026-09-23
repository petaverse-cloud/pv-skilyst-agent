/**
 * The shell's whole view of the runtime: start/stop the child process (Tauri
 * commands) and speak its loopback HTTP contract.
 *
 * Nothing here invents data. The token and the port come from the runtime's own
 * ready line via Rust state, and every payload is the JSON the runtime returned --
 * if the runtime is not connected, the calls fail loudly instead of rendering an
 * empty screen that looks like an answer.
 */
import { invoke } from "@tauri-apps/api/core";

export type RuntimeInfo = {
  port: number;
  token: string;
  base_url: string;
  pid: number;
  dry_run: boolean;
  sessions_dir: string;
  store_dir: string;
  workspace_dir: string;
};

export type SessionRow = {
  session_id: string;
  title: string;
  status: string;
  model: string;
  messages: number;
  artifacts: number;
  usage?: Record<string, number>;
  updated_at?: number;
};

export type TranscriptMessage = {
  seq?: number;
  ts?: number;
  role: string;
  content: string;
  name?: string;
  tool_calls?: unknown[];
};

export type SessionDetail = {
  session_id: string;
  meta: Record<string, unknown> & { title?: string; model?: string; message_count?: number };
  messages: TranscriptMessage[];
  artifacts: { artifact_url?: string; job_id?: string; ts?: number }[];
  trace: Record<string, unknown>[];
  path: string;
};

export type ToolCall = {
  tool: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: string | null;
  duration_s?: number;
};

export type RunSummary = {
  session_id: string;
  ok: boolean;
  stop_reason: string;
  answer: string;
  model: string;
  turns: number;
  usage: Record<string, unknown>;
  artifacts: string[];
  tool_calls: ToolCall[];
  notes: string[];
  error?: string | null;
  dry_run: boolean;
  skill?: string | null;
};

export type RedactedConfig = {
  beehive: Record<string, string>;
  llm: { base_url: string; model: string; fallbacks: string[]; api_key: string };
  paths: Record<string, string>;
  env_file: string | null;
  sources?: Record<string, unknown>;
};

export type DoctorReport = {
  store: string;
  env_file: string | null;
  integrity: { skill_id: string; ok: boolean; error?: string }[];
  config: RedactedConfig;
  skills?: { skill_id: string; version: string; description: string; degraded?: boolean }[];
  runnable: boolean;
  preflight?: {
    registry_available: boolean;
    runnable: boolean;
    problems: { node_id: string; severity: string; detail: string }[];
  } | null;
};

export type MessageRequest = {
  message: string;
  session_id?: string;
  skill?: string | null;
  model?: string;
  dry_run?: boolean;
};

let current: RuntimeInfo | null = null;

export function inShell(): boolean {
  return typeof (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ !== "undefined";
}

export function runtime(): RuntimeInfo | null {
  return current;
}

/** Browser-only development: point the UI at a runtime you started yourself. */
function browserFallback(): RuntimeInfo | null {
  const spec = import.meta.env.VITE_SKILYST_RUNTIME as string | undefined;
  if (!spec) return null;
  const [base, token] = spec.split("#");
  return {
    base_url: base.replace(/\/$/, ""),
    token: token ?? "",
    port: Number(base.split(":").pop() ?? 0),
    pid: 0,
    dry_run: true,
    sessions_dir: "",
    store_dir: "",
    workspace_dir: "",
  };
}

export async function startRuntime(live = false): Promise<RuntimeInfo> {
  if (inShell()) {
    const info = await invoke<RuntimeInfo>("runtime_start", { live });
    current = info;
    return info;
  }
  const fallback = browserFallback();
  if (fallback) {
    current = fallback;
    return fallback;
  }
  throw new Error(
    "This build is not inside the desktop shell, so it cannot start the runtime. " +
      "Start one yourself (`skilyst serve --port 8765 --token <token>`) and load the dev " +
      "server with VITE_SKILYST_RUNTIME=http://127.0.0.1:8765#<token>.",
  );
}

export async function stopRuntime(): Promise<void> {
  if (!inShell()) {
    current = null;
    return;
  }
  if (current) {
    // Graceful first (the runtime closes its sessions cleanly); runtime_stop is the
    // backstop and always runs, so a wedged runtime still goes away.
    try {
      await api("/shutdown", { method: "POST", body: {} });
    } catch {
      /* already gone */
    }
  }
  await invoke("runtime_stop");
  current = null;
}

export async function runtimeStatus(): Promise<RuntimeInfo | null> {
  if (!inShell()) return current;
  current = await invoke<RuntimeInfo | null>("runtime_status");
  return current;
}

type ApiOptions = { method?: string; body?: unknown; timeoutMs?: number };

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  if (!current) throw new Error("the local runtime is not connected");
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), options.timeoutMs ?? 15 * 60 * 1000);
  try {
    const response = await fetch(`${current.base_url}${path}`, {
      method: options.method ?? "GET",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${current.token}` },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const payload = (await response.json().catch(() => ({}))) as {
      ok?: boolean;
      data?: T;
      error?: { kind?: string; message?: string };
    };
    if (!response.ok || payload.ok === false) {
      const error = payload.error;
      throw new Error(error ? `${error.kind ?? "Error"}: ${error.message ?? "request failed"}` : `HTTP ${response.status}`);
    }
    return payload.data as T;
  } finally {
    window.clearTimeout(timer);
  }
}

export type StreamHandlers = {
  onDelta?: (text: string) => void;
  onNote?: (text: string) => void;
};

type SseEvent = { name: string; data: Record<string, unknown> };

function parseEvent(block: string): SseEvent | null {
  let name = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data = line.slice(6);
  }
  if (!name || !data) return null;
  try {
    return { name, data: JSON.parse(data) as Record<string, unknown> };
  } catch {
    return null;
  }
}

/**
 * Send one turn and stream the run: `delta` is model text as it arrives, `note` is a
 * progress line from the loop (tool calls, job polling), and the resolved value is the
 * same summary a non-streaming call returns.
 */
export async function sendMessage(request: MessageRequest, handlers: StreamHandlers = {}): Promise<RunSummary> {
  if (!current) throw new Error("the local runtime is not connected");
  const response = await fetch(`${current.base_url}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${current.token}` },
    body: JSON.stringify({ ...request, stream: true }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { error?: { kind?: string; message?: string } };
    throw new Error(payload.error ? `${payload.error.kind}: ${payload.error.message}` : `HTTP ${response.status}`);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("the runtime returned no stream body");

  const decoder = new TextDecoder();
  let buffer = "";
  let summary: RunSummary | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const event = parseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event) {
        if (event.name === "delta") handlers.onDelta?.(String(event.data.text ?? ""));
        else if (event.name === "note") handlers.onNote?.(String(event.data.text ?? ""));
        else if (event.name === "done") {
          summary = (event.data as { data?: RunSummary }).data ?? null;
        } else if (event.name === "error") {
          const error = event.data.error as { kind?: string; message?: string } | undefined;
          throw new Error(`${error?.kind ?? "Error"}: ${error?.message ?? "the run was refused"}`);
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
  if (!summary) throw new Error("the runtime closed the stream without a result");
  return summary;
}
