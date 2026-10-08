/**
 * Workflow↔session ownership registry (issue #31, post-project revision).
 *
 * The creation domain is the workflow (beehive server entity); chat history
 * is a property of the workflow domain, owned by THIS desktop install. The
 * mapping is a local JSON document:
 *
 *   { "workflow_id": ["session_id", ...] }
 *
 * Storage rules (G3-14): atomic write (temp file + rename), parse failure
 * degrades to an empty registry WITH a visible console error — never a
 * silent catch, never a broken boot.
 */

const STORAGE_KEY = "skilyst.workflow_sessions.v1";

export type WorkflowSessions = Record<string, string[]>;

function logCorrupt(raw: string): void {
  // Loud failure (G4-15): a corrupt registry is a bug, not a case to hide.
  console.error(`[workflow-registry] corrupt local registry, resetting: ${raw.slice(0, 120)}`);
}

export function loadRegistry(): WorkflowSessions {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as WorkflowSessions;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      logCorrupt(raw);
      return {};
    }
    return parsed;
  } catch (exc) {
    console.error(`[workflow-registry] JSON parse failed: ${exc instanceof Error ? exc.message : exc}`);
    return {};
  }
}

function save(registry: WorkflowSessions): void {
  // localStorage is itself atomic per spec (a single key write), so the
  // temp+rename dance of file storage is not needed here; the atomic-write
  // gate applies when this moves to disk-backed storage.
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(registry));
}

export function sessionsFor(registry: WorkflowSessions, workflowId: string): string[] {
  return registry[workflowId] ?? [];
}

export function recordSession(workflowId: string, sessionId: string): WorkflowSessions {
  const registry = loadRegistry();
  const existing = registry[workflowId] ?? [];
  if (!existing.includes(sessionId)) {
    registry[workflowId] = [sessionId, ...existing];
    save(registry);
  }
  return registry;
}

export function removeSession(workflowId: string, sessionId: string): WorkflowSessions {
  const registry = loadRegistry();
  const existing = registry[workflowId] ?? [];
  if (existing.includes(sessionId)) {
    const next = existing.filter((sid) => sid !== sessionId);
    if (next.length === 0) delete registry[workflowId];
    else registry[workflowId] = next;
    save(registry);
  }
  return registry;
}
