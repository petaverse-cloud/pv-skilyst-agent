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
/** #91: a creation on the public wall (core#756 publicWorkView — the shared
 * card contract web#386 pins; the desktop is the MVP subset consumer). */
export type CreationWork = {
  id: string;
  name: string;
  asset_type: string;
  mime_type: string;
  url: string;
  thumbnail_url?: string;
  duration?: number;
  created_at: string;
};

/** #91: the Creations feed — media-first, newest first. */
export async function listPublicWorks(limit = 24, offset = 0): Promise<CreationWork[]> {
  const payload = await api<Record<string, unknown>>(
    `/beehive/public/works?limit=${limit}&offset=${offset}`);
  const rows = Array.isArray(payload?.works) ? (payload?.works as Record<string, unknown>[]) : [];
  // raw rows are the passthrough floor; pinned fields land last so upstream
  // garbage never overrides the normalized shape (#70's spread-order lesson)
  return rows.map((row) => ({
    ...row,
    id: String(row.id ?? ""),
    name: String(row.name ?? "untitled"),
    asset_type: String(row.asset_type ?? "image"),
    mime_type: String(row.mime_type ?? ""),
    url: String(row.url ?? ""),
    thumbnail_url: row.thumbnail_url ? String(row.thumbnail_url) : undefined,
    duration: typeof row.duration === "number" ? row.duration : undefined,
    created_at: String(row.created_at ?? ""),
  }));
}

/** #91: like = the favorite toggle (core#632/#633 — the existing contract;
 * a creation-card "like" lands on the asset's favorite surface). */
export async function setCreationFavorite(id: string, liked: boolean): Promise<void> {
  await api(liked ? "/beehive/assets/favorite" : "/beehive/assets/unfavorite", {
    method: "POST",
    body: { id },
  });
}

export async function getWalletBalance(): Promise<number | null> {
  const data = await api<{ balance_usd?: number; balance?: number } | number>(
    "/beehive/billing/wallet",
  );
  if (typeof data === "number") return data;
  const usd = data?.balance_usd ?? data?.balance;
  return typeof usd === "number" ? usd : null;
}

// -- Billing ledger (#99, web-console parity) --------------------------------
// The Settings billing card: history (paged) + per-job cost detail.
// Amounts are micro-USD integers (core contract) — formatted at the edge.

export interface BillingEvent {
  event_id: number;
  event_type: string;
  amount: number; // micro-USD
  job_id?: string;
  node_id?: string;
  created_at: string;
}

export interface BillingHistory {
  events: BillingEvent[];
  total: number;
}

export async function getBillingHistory(
  limit = 20,
  offset = 0,
): Promise<BillingHistory> {
  const data = await api<{ events?: BillingEvent[]; total?: number }>(
    `/beehive/billing/history?limit=${limit}&offset=${offset}`,
  );
  return { events: data?.events ?? [], total: data?.total ?? 0 };
}

export interface JobBilling {
  job_id: string;
  cost_estimated: number;
  cost_actual: number;
  sum_quoted: number;
  sum_reserved: number;
  sum_refunded: number;
  events: BillingEvent[];
}

export async function getJobBilling(jobId: string): Promise<JobBilling> {
  return api<JobBilling>(`/beehive/billing/jobs/${encodeURIComponent(jobId)}`);
}

// -- Profile: account self-service (#99 face 4) ------------------------------
// Password change + OAuth connection management. Bind (the browser relay)
// stays a web-console action; the desktop shows what is bound and unbinds.

export interface OAuthConnection {
  provider: string;
  email?: string;
  created_at?: string;
}

export async function getOAuthConnections(): Promise<OAuthConnection[]> {
  const data = await api<{ connections?: OAuthConnection[] }>(
    "/beehive/auth/oauth/connections",
  );
  return data?.connections ?? [];
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await api<void>("/beehive/auth/password", {
    method: "PUT",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}

export async function unbindOAuth(provider: string): Promise<void> {
  await api<void>(`/beehive/auth/oauth/${encodeURIComponent(provider)}/unbind`, {
    method: "POST",
    body: "{}",
  });
}

// -- Skill-preview wall (#102, core#833/#834) ---------------------------------
// The wall's subject is the SKILL (Wesley's ruling): generated content is
// preview material attached to the card, not a first-class wall citizen.
// Field mapping isolated here (API churn touches one layer only).

export interface SkillPreviewCard {
  skill_id: string;
  display_name: string;
  slug: string; // never rendered — display_name everywhere (#818)
  tier: string; // official | community_verified (anonymous wall gate)
  fork_count: number;
  previews: string[]; // media URIs, media-first (video > image > audio), <=3
  created_at: string;
}

export async function listSkillPreviews(
  limit = 24,
  offset = 0,
): Promise<{ cards: SkillPreviewCard[]; total: number }> {
  const data = await api<{ skills?: SkillPreviewCard[]; total?: number }>(
    `/beehive/public/skill-previews?limit=${limit}&offset=${offset}`,
  );
  return { cards: data?.skills ?? [], total: data?.total ?? 0 };
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
    ...row,
    // Normalized fields LAST: the raw row is the passthrough floor, not the
    // ceiling — a raw-typed field (e.g. pricing.price_usd as a string, or a
    // number display_name) must never punch through to the card renderer.
    // review round-2: spread order fixed (raw-first, coerced-last).
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
  };
}
