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

// -- Skills registry (#54, core#714) ---------------------------------------
// The works-wall skill dimension: list the published/owned skills with
// their materialization snapshots. Field mapping is isolated HERE (the
// design doc's invariant: API churn touches one layer only).

export interface SkillCardData {
  skill_id: string;
  display_name: string;
  version: string;
  author_name: string;
  author_verified: boolean;
  fork_depth: number;
  usage_count: number;
  /** v0.3 §1: the reference snapshot the skill carries. */
  reference_workflow: { id: string; content_hash?: string; cover_url?: string } | null;
  pricing: { price_usd: number; free: boolean } | null;
  /** Raw row for anything the card spec has not pinned yet. */
  [key: string]: unknown;
}

export async function listSkills(limit = 50, offset = 0): Promise<SkillCardData[]> {
  const data = await api<{ skills?: SkillCardData[] } | SkillCardData[]>(
    `/beehive/skills?limit=${limit}&offset=${offset}`,
  );
  const rows = Array.isArray(data) ? data : (data.skills ?? []);
  return rows.map(normalizeSkillCard);
}

/** Tolerant normalizer (design doc §1): core P1 field names may still
 * drift before the API freezes — a missing field degrades the card, it
 * never breaks the wall. */
function normalizeSkillCard(row: Record<string, unknown>): SkillCardData {
  const author = (row.author ?? {}) as Record<string, unknown>;
  const ref = (row.reference_workflow ?? row.referenceWorkflow ?? null) as Record<string, unknown> | null;
  const pricing = (row.pricing ?? null) as Record<string, unknown> | null;
  const price = pricing ? Number(pricing.price_usd ?? pricing.priceUsd ?? 0) : null;
  return {
    skill_id: String(row.skill_id ?? row.id ?? ""),
    display_name: String(row.display_name ?? row.name ?? row.skill_id ?? ""),
    version: String(row.version ?? ""),
    author_name: String(author.name ?? author.display_name ?? ""),
    author_verified: Boolean(author.verified),
    fork_depth: Number(row.fork_depth ?? row.forkDepth ?? 0),
    usage_count: Number(row.usage_count ?? row.usageCount ?? 0),
    reference_workflow: ref ? {
      id: String(ref.id ?? ref.workflow_id ?? ""),
      content_hash: ref.content_hash ? String(ref.content_hash) : undefined,
      cover_url: ref.cover_url ? String(ref.cover_url) : undefined,
    } : null,
    pricing: price !== null ? { price_usd: price, free: price === 0 } : null,
    ...row,
  };
}
