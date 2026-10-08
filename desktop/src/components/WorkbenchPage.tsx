/**
 * WorkbenchPage (issue #31) — the creation domain: one workflow at a time.
 * Three-pane layout — History (this workflow's sessions, from the local
 * registry) | Conversation + Composer | the embedded canvas (the former
 * global CanvasView, absorbed per the 2026-10-08 ruling).
 *
 * The canvas pane opens when this workflow exists server-side; action-card
 * locate hops between panes inside this page, never a module switch.
 */

import { Alert, Badge, Box, Button, Divider, Group, Loader, Stack, Text } from "@mantine/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type RunSummary, type SessionDetail, type SessionRow } from "../api";
import { resolveConfirm, sendMessage } from "../api";
import { BEEHIVE_TOKEN_KEY } from "../hostAdapter";
import { appendDelta, emptyStream, freezeTurn, type StreamState } from "../stream";
import { recordSession, sessionsFor, type WorkflowSessions } from "../workflowRegistry";
import Composer from "./Composer";
import ConversationView from "./ConversationView";
import SessionList from "./SessionList";
import { CanvasView, type CanvasFocus } from "./CanvasView";
import { QuoteConfirmCard, type QuoteView } from "@petaverse/skilyst-studio/session";

type WorkflowRow = {
  id: string;
  name: string;
  nodes?: unknown[];
  /** BR-A: workflow-level settings container (explicit cover). */
  settings?: { cover_url?: string | null } | null;
  /** BR-B: provenance — "agent" workflows are agent-built. */
  created_via?: string | null;
};

export default function WorkbenchPage({
  routeSessionId,
  routeWorkflowId,
  sessions,
  registry,
  onRegistryChange,
  info,
  dryRun,
  onDryRunChange,
  model,
  balanceUsd,
  onBalanceUsd,
}: {
  routeSessionId?: string;
  routeWorkflowId?: string;
  sessions: SessionRow[];
  registry: WorkflowSessions;
  onRegistryChange: (registry: WorkflowSessions) => void;
  info: { dry_run: boolean; port: number; pid: number } | null;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  model: string;
  balanceUsd: number | null;
  onBalanceUsd: (value: number | null) => void;
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [sending, setSending] = useState(false);
  const [stream, setStream] = useState<StreamState>(emptyStream);
  const [notes, setNotes] = useState<string[]>([]);
  const [pendingConfirm, setPendingConfirm] = useState<{ sessionId: string; quote: QuoteView } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasFocus, setCanvasFocus] = useState<CanvasFocus>(null);
  const [workflow, setWorkflow] = useState<WorkflowRow | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The workflow this workbench instance is bound to: the route's workflow,
  // or the most recent one in the registry, or null (fresh start).
  const activeWorkflowId = routeWorkflowId ?? null;
  const visibleSessions = useMemo(() => {
    if (activeWorkflowId) {
      const owned = new Set(sessionsFor(registry, activeWorkflowId));
      return sessions.filter((s) => owned.has(s.session_id));
    }
    return sessions;
  }, [sessions, registry, activeWorkflowId]);

  const openSession = useCallback(async (sessionId: string) => {
    setError(null);
    try {
      setDetail(await api<SessionDetail>(`/session/${sessionId}`));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  // Route deep link: `#/workbench?session=<id>` opens that session once.
  useEffect(() => {
    if (routeSessionId) void openSession(routeSessionId);
  }, [routeSessionId, openSession]);

  // Resolve the route's workflow (name for the header, canvas availability).
  useEffect(() => {
    if (!activeWorkflowId) {
      setWorkflow(null);
      setWorkflowError(null);
      return;
    }
    const token = window.localStorage.getItem(BEEHIVE_TOKEN_KEY);
    if (!token) {
      setWorkflowError("Sign in to the canvas data source to open this workflow's board.");
      setWorkflow(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setWorkflowError(null);
      try {
        const res = await fetch(`/api/v1/workflows/${activeWorkflowId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error(`workflow fetch failed (HTTP ${res.status})`);
        const data = await res.json();
        if (!cancelled) setWorkflow((data.payload ?? data) as WorkflowRow);
      } catch (exc) {
        if (!cancelled) setWorkflowError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeWorkflowId]);

  const send = useCallback(
    async (text: string) => {
      if (!info) return;
      setSending(true);
      setError(null);
      setNotes([]);
      setStream(emptyStream);
      setPendingConfirm(null);
      try {
        const summary: RunSummary = await sendMessage(
          {
            message: text,
            session_id: detail?.session_id,
            model: model || undefined,
            dry_run: dryRun,
            confirm_paid: true,
          },
          {
            onDelta: (chunk) => setStream((state) => appendDelta(state, chunk)),
            onNote: (line) => {
              setStream((state) => freezeTurn(state));
              setNotes((current) => [...current, line]);
            },
            onConfirmRequest: (detail2) => {
              const quote = (detail2.quote ?? {}) as QuoteView;
              setPendingConfirm({ sessionId: detail2.session_id ?? detail?.session_id ?? "", quote });
              const token = window.localStorage.getItem("skilyst.beehive_token");
              if (token) {
                fetch("/api/v1/billing/wallet", { headers: { Authorization: `Bearer ${token}` } })
                  .then((r) => (r.ok ? r.json() : null))
                  .then((data) => {
                    const payload = data?.payload ?? data;
                    const usd = payload?.balance_usd ?? payload?.balance;
                    if (typeof usd === "number") onBalanceUsd(usd);
                  })
                  .catch(() => onBalanceUsd(null));
              }
            },
          },
        );
        const refreshed = await api<SessionDetail>(`/session/${summary.session_id}`);
        setDetail(refreshed);
        setStream(emptyStream);
        // Ownership: a new session belongs to this workflow (when one is
        // active) — the registry is what the history pane filters on.
        if (activeWorkflowId && summary.session_id) {
          onRegistryChange(recordSession(activeWorkflowId, summary.session_id));
        }
        if (!summary.ok) {
          setError(`${summary.stop_reason}: ${summary.error ?? "the run did not complete"}`);
        }
      } catch (exc) {
        setStream((state) => freezeTurn(state));
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setSending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [detail?.session_id, dryRun, model, info, activeWorkflowId],
  );

  const answerConfirm = useCallback(
    async (approve: boolean) => {
      if (!pendingConfirm) return;
      setConfirmBusy(true);
      try {
        await resolveConfirm(pendingConfirm.sessionId, approve);
        if (approve) setPendingConfirm(null);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setConfirmBusy(false);
      }
    },
    [pendingConfirm],
  );

  const locate = useCallback((message: import("../api").TranscriptMessage) => {
    const params = (message.params ?? {}) as Record<string, unknown>;
    const delta = (message.board_delta ?? {}) as Record<string, unknown>;
    const ref = (message.result_ref ?? {}) as Record<string, unknown>;
    const workflowId = typeof params.workflow_id === "string" ? params.workflow_id : undefined;
    const nodeKey =
      typeof ref.node === "string" ? ref.node
      : Array.isArray(ref.wired) && typeof ref.wired[0] === "string" ? (ref.wired[0] as string)
      : typeof delta.added_node === "string" ? delta.added_node
      : undefined;
    if (workflowId) {
      setCanvasFocus({ workflow_id: workflowId, node_key: nodeKey });
      setCanvasOpen(true);
    }
  }, []);

  const historyPaneWidth = 240;

  return (
    <Group wrap="nowrap" gap={0} style={{ flex: 1, minHeight: 0 }} align="stretch">
      {/* ── History: this workflow's sessions (workbench-local) ── */}
      <Stack
        gap={0}
        w={historyPaneWidth}
        style={{ borderRight: "1px solid var(--mantine-color-dark-4)" }}
        data-testid="workbench-history"
      >
        <Group px="sm" py={6} justify="space-between" wrap="nowrap">
          <Text size="xs" fw={600} c="dimmed" truncate>
            {activeWorkflowId ? (workflow?.name ?? "History") : "All sessions"}
          </Text>
        </Group>
        <div style={{ flex: 1, minHeight: 0 }}>
          <SessionList
            sessions={visibleSessions}
            activeId={detail?.session_id ?? null}
            onOpen={(sessionId) => void openSession(sessionId)}
            onNew={() => {
              setDetail(null);
              setStream(emptyStream);
              setNotes([]);
            }}
            busy={sending}
          />
        </div>
      </Stack>

      {/* ── Conversation ── */}
      <Stack gap={0} style={{ flex: 1, minWidth: 0, minHeight: 0 }} align="stretch">
        <Group px="md" py={4} justify="space-between" wrap="nowrap" bg="var(--mantine-color-dark-8)">
          <Group gap="sm" wrap="nowrap">
            <Text size="sm" fw={500} truncate data-testid="workbench-title">
              {activeWorkflowId ? (workflow?.name ?? workflowIdLabel(activeWorkflowId)) : "Workbench"}
            </Text>
            {workflow?.created_via === "agent" && (
              <Badge size="xs" variant="light" color="violet" data-testid="workbench-agent-badge">
                agent-built
              </Badge>
            )}
            {activeWorkflowId && (workflow?.nodes?.length != null) && (
              <Badge size="xs" variant="light" color="teal">
                {(workflow.nodes as unknown[]).length} nodes
              </Badge>
            )}
          </Group>
          <Button
            size="xs"
            variant={canvasOpen ? "filled" : "subtle"}
            onClick={() => setCanvasOpen(!canvasOpen)}
            disabled={Boolean(workflowError)}
            data-testid="toggle-canvas"
          >
            Canvas
          </Button>
        </Group>
        {workflowError ? (
          <Alert color="orange" m="sm" title="Workflow board unavailable">
            {workflowError}
          </Alert>
        ) : null}
        {error ? (
          <Alert
            color="red"
            title="Runtime error"
            m="sm"
            withCloseButton
            onClose={() => setError(null)}
            data-testid="error-banner"
          >
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              {error}
            </Text>
          </Alert>
        ) : null}
        {info === null ? (
          <Stack align="center" justify="center" style={{ flex: 1 }} gap="xs">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              starting the local runtime…
            </Text>
          </Stack>
        ) : (
          <>
            <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <ConversationView
                detail={detail}
                stream={stream}
                notes={notes}
                busy={sending}
                onLocateBoard={locate}
              />
              {pendingConfirm ? (
                <Box px="md" pb="sm" data-testid="quote-confirm-wrap">
                  <QuoteConfirmCard
                    quote={pendingConfirm.quote}
                    balanceUsd={balanceUsd}
                    busy={confirmBusy || sending}
                    onConfirm={() => void answerConfirm(true)}
                    onCancel={() => void answerConfirm(false)}
                  />
                </Box>
              ) : null}
              <Composer
                onSend={(text) => void send(text)}
                busy={sending || !info}
                dryRun={dryRun}
                onDryRunChange={onDryRunChange}
                serverDryRun={info.dry_run}
                model={model}
              />
            </div>
          </>
        )}
      </Stack>

      {/* ── Canvas: embedded, per-workflow ── */}
      {canvasOpen && (
        <>
          <Divider orientation="vertical" />
          <div
            style={{ width: "48%", minWidth: 380, flex: "0 0 48%", minHeight: 0, display: "flex", flexDirection: "column" }}
            data-testid="workbench-canvas"
          >
            <CanvasView
              onExit={() => setCanvasOpen(false)}
              focus={canvasFocus}
              initialWorkflowId={activeWorkflowId}
            />
          </div>
        </>
      )}
    </Group>
  );
}

function workflowIdLabel(id: string): string {
  return id.length > 18 ? `${id.slice(0, 16)}…` : id;
}
