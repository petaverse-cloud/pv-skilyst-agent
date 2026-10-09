/**
 * The shell's whole view of the runtime: start/stop the child process (Tauri
 * commands) and speak its loopback HTTP contract.
 *
 * Nothing here invents data. Every payload is the JSON the runtime returned --
 * if the runtime is not connected, the calls fail loudly instead of rendering an
 * empty screen that looks like an answer.
 *
 * #42: inside the desktop shell, requests go through the Rust command
 * `runtime_request` (invoke) — the bearer token stays in Rust state and never
 * enters JS. The fetch path remains ONLY for browser dev
 * (VITE_SKILYST_RUNTIME), where there is no shell to hold the token.
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
  // A3 S3 (R3 message model): `action` rows (canvas tool executions) and
  // `note` rows (lock hints / manual changes / errors). Roles stay in one
  // transcript; the chat API never receives these rows.
  type?: string;
  tool?: string;
  params?: Record<string, unknown>;
  result_ref?: Record<string, unknown> | null;
  board_delta?: Record<string, unknown> | null;
  origin?: string;
  duration_s?: number;
  cost?: { estimate_usd?: number; hold_micro_usd?: number };
  note_kind?: string;
  text?: string;
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
  /** S4 quote UX: gate paid submissions behind a confirm_request / POST /confirm exchange. */
  confirm_paid?: boolean;
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
  // #42: the shell proxies for us so the token stays out of JS. The browser
  // dev fallback keeps the direct fetch (there is no shell to hold anything).
  if (inShell()) {
    const relay = await invoke<{ status: number; payload: {
      ok?: boolean; data?: T; error?: { kind?: string; message?: string };
    } }>("runtime_request", {
      path,
      method: options.method ?? "GET",
      body: options.body === undefined ? null : options.body,
    });
    const payload = relay.payload;
    if (relay.status >= 400 || payload.ok === false) {
      const error = payload.error;
      throw new Error(error ? `${error.kind ?? "Error"}: ${error.message ?? "request failed"}` : `HTTP ${relay.status}`);
    }
    return payload.data as T;
  }
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
  /** S4 quote UX: the runtime paused a paid submission at the quote and is
   *  waiting on POST /confirm/<session_id>. */
  onConfirmRequest?: (detail: {
    session_id?: string;
    quote?: { total_hold?: number; total_hold_display?: number; nodes?: unknown[] };
  }) => void;
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
/** S4 quote UX: answer the runtime's pending paid-confirmation gate. */
export async function resolveConfirm(sessionId: string, approve: boolean): Promise<void> {
  if (!current) throw new Error("the local runtime is not connected");
  await api(`/confirm/${sessionId}`, { method: "POST", body: { approve } });
}

export async function sendMessage(request: MessageRequest, handlers: StreamHandlers = {}): Promise<RunSummary> {
  if (!current) throw new Error("the local runtime is not connected");
  // #42: inside the shell the SSE bytes come through the Rust bridge as
  // Tauri events — the fetch path below is the browser-dev fallback only.
  if (inShell()) {
    return sendMessageViaBridge(request, handlers);
  }
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
        else if (event.name === "confirm_request") handlers.onConfirmRequest?.(
          event.data as { session_id?: string; quote?: { total_hold?: number; total_hold_display?: number; nodes?: unknown[] } });
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

/**
 * #42: the shell's SSE bridge. The Rust command holds the bearer token and
 * relays raw stream bytes as "runtime-stream-*" events; the same parser and
 * handler semantics as the fetch path apply (one code path for both).
 */
async function sendMessageViaBridge(request: MessageRequest, handlers: StreamHandlers): Promise<RunSummary> {
  const { listen } = await import("@tauri-apps/api/event");
  const streamId = `runtime-stream-${Math.random().toString(36).slice(2)}`;
  let summary: RunSummary | null = null;
  let buffer = "";
  const unlisten = await listen<string>(streamId, (event) => {
    buffer += event.payload;
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const parsed = parseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (parsed) {
        if (parsed.name === "delta") handlers.onDelta?.(String(parsed.data.text ?? ""));
        else if (parsed.name === "note") handlers.onNote?.(String(parsed.data.text ?? ""));
        else if (parsed.name === "confirm_request") handlers.onConfirmRequest?.(
          parsed.data as { session_id?: string; quote?: { total_hold?: number; total_hold_display?: number; nodes?: unknown[] } });
        else if (parsed.name === "done") {
          summary = (parsed.data as { data?: RunSummary }).data ?? null;
        } else if (parsed.name === "error") {
          const error = parsed.data.error as { kind?: string; message?: string } | undefined;
          summary = null; // the throw below surfaces it
          throw new Error(`${error?.kind ?? "Error"}: ${error?.message ?? "the run was refused"}`);
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  });
  try {
    await invoke("runtime_request_stream", { streamId, body: { ...request, stream: true } });
  } finally {
    unlisten();
  }
  if (!summary) throw new Error("the runtime closed the stream without a result");
  return summary;
}
