import { api } from "./api";

/**
 * Unified server-data posture (issue #36): the webview NEVER talks to the
 * beehive API directly. Every remote call goes through the local runtime,
 * which signs with the keychain AK/SK. No web token in localStorage, no
 * vite-proxy dependency, identical behavior dev/prod.
 *
 * Until the runtime proxy routes land (#36) these calls fail loudly —
 * the consuming components surface the message, so the gap stays visible
 * instead of silently showing an empty wall.
 */

export interface WorkflowRow {
  id: string;
  name: string;
  created_via?: string | null;
  settings?: { cover_url?: string } | null;
  updated_at?: string;
  [key: string]: unknown;
}

/** Home works wall + canvas list. */
export async function listWorkflows(limit = 50, offset = 0): Promise<WorkflowRow[]> {
  const data = await api<{ workflows?: WorkflowRow[] } | WorkflowRow[]>(
    `/beehive/workflows?limit=${limit}&offset=${offset}`,
  );
  const rows = Array.isArray(data) ? data : (data.workflows ?? []);
  return rows;
}

/** Workbench header: resolve a workflow's name/board. */
export async function getWorkflow(id: string): Promise<WorkflowRow> {
  return api<WorkflowRow>(`/beehive/workflows/${encodeURIComponent(id)}`);
}

/** Quote-confirm wallet check (S4). Returns null when unavailable. */
export async function getWalletBalance(): Promise<number | null> {
  const data = await api<{ balance_usd?: number; balance?: number } | number>(
    "/beehive/billing/wallet",
  );
  if (typeof data === "number") return data;
  const usd = data?.balance_usd ?? data?.balance;
  return typeof usd === "number" ? usd : null;
}
