/**
 * WorkbenchPage (issue #38) — the Figma-form workbench: canvas-first.
 *
 * The board is the primary surface (fullscreen); the input bar docked at the
 * bottom is the control point. History and the full transcript live in an
 * overlay (Figma's file-list pattern), NOT a resident pane. Agent action
 * cards annotate the run inside the conversation overlay; quote-confirm docks
 * above the input bar.
 *
 * Session mechanics (sendMessage, workflowRegistry ownership, quote confirm)
 * are unchanged from the M1 three-pane layout — this is a layout convergence
 * only (#38 ruling: three panes → one board + one input).
 */

import { Alert, Badge, Box, Button, Drawer, Group, Loader, Stack, Text, Tooltip } from "@mantine/core";
import { IconHistory, IconMessage } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type RunSummary, type SessionDetail, type SessionRow, type TranscriptMessage } from "../api";
import { resolveConfirm, sendMessage } from "../api";
import { getWalletBalance, getWorkflow, type WorkflowRow } from "../beehiveClient";
import { appendDelta, emptyStream, freezeTurn, type StreamState } from "../stream";
import { recordSession, sessionsFor, type WorkflowSessions } from "../workflowRegistry";
import Composer, { type SkillOption as ComposerSkillOption } from "./Composer";
import ConversationView from "./ConversationView";
import SessionList from "./SessionList";
import { CanvasView, type CanvasFocus } from "./CanvasView";
import { QuoteConfirmCard, type QuoteView } from "@petaverse/skilyst-studio/session";

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
  const [canvasFocus, setCanvasFocus] = useState<CanvasFocus>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [workflow, setWorkflow] = useState<WorkflowRow | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The workflow this workbench instance is bound to: the route's workflow,
  // or null (fresh start). The canvas IS the page — no toggle, no open/close.
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
      setHistoryOpen(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  // Route deep link: `#/workbench?session=<id>` opens that session once.
  useEffect(() => {
    if (routeSessionId) void openSession(routeSessionId);
  }, [routeSessionId, openSession]);

  // Resolve the route's workflow (board data for the primary surface).
  useEffect(() => {
    if (!activeWorkflowId) {
      setWorkflow(null);
      setWorkflowError(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setWorkflowError(null);
      try {
        // Unified posture (#36): runtime proxy, keychain-signed.
        const row = await getWorkflow(activeWorkflowId);
        if (!cancelled) setWorkflow(row);
      } catch (exc) {
        if (!cancelled) setWorkflowError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeWorkflowId]);

  // M3a (#52): the skill picker feed — GET /skills via the runtime (unified
  // posture; the cloud registry joins at core P1 freeze, core#708).
  const [skills, setSkills] = useState<ComposerSkillOption[]>([]);
  const [skill, setSkill] = useState<string>("");
  useEffect(() => {
    if (!info) return;
    let cancelled = false;
    api<{ skills: ComposerSkillOption[] }>("/skills")
      .then((payload) => { if (!cancelled) setSkills(payload.skills ?? []); })
      .catch((exc) => { if (!cancelled) setError(`skills unavailable: ${exc instanceof Error ? exc.message : String(exc)}`); });
    return () => { cancelled = true; };
    // keyed on info like the rest of the mount-fetch audit (#53)
  }, [info]);

  const send = useCallback(
    async (text: string) => {
      if (!info) return;
      setSending(true);
      setError(null);
      setNotes([]);
      setStream(emptyStream);
      setPendingConfirm(null);
      // The reply streams into the conversation overlay: open it so the
      // run's feedback is visible by default (Figma opens the comment list
      // when you comment) — the input bar must not fire into a void.
      setHistoryOpen(true);
      try {
        const summary: RunSummary = await sendMessage(
          {
            message: text,
            session_id: detail?.session_id,
            skill: skill || undefined,
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
              setHistoryOpen(true);
              // Wallet context for the quote (S4) — unified posture (#36).
              getWalletBalance()
                .then((usd) => onBalanceUsd(usd))
                .catch(() => onBalanceUsd(null));
            },
          },
        );
        const refreshed = await api<SessionDetail>(`/session/${summary.session_id}`);
        setDetail(refreshed);
        setStream(emptyStream);
        // Ownership: a new session belongs to this workflow (when one is
        // active) — the registry is what the history overlay filters on.
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
    [info, detail, model, dryRun, activeWorkflowId, onRegistryChange, onBalanceUsd],
  );

  const answerConfirm = useCallback(
    async (approve: boolean) => {
      if (!pendingConfirm) return;
      setConfirmBusy(true);
      try {
        await resolveConfirm(pendingConfirm.sessionId, approve);
        setPendingConfirm(null);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setConfirmBusy(false);
      }
    },
    [pendingConfirm],
  );

  // A3 S3 action-card locate: hop the canvas focus (same surface now — the
  // board centers on the node the action touched).
  const locate = useCallback((message: TranscriptMessage) => {
    const focus = extractCanvasFocus(message);
    if (focus) setCanvasFocus(focus);
  }, []);

  return (
    <Stack gap={0} style={{ flex: 1, minHeight: 0 }} align="stretch">
      {/* ── Board header: identity + the only two overlays' handles ── */}
      <Group
        px="md"
        py={4}
        justify="space-between"
        wrap="nowrap"
        bg="var(--mantine-color-dark-8)"
        style={{ borderBottom: "1px solid var(--mantine-color-dark-4)" }}
      >
        <Group gap="sm" wrap="nowrap">
          <Text size="sm" fw={500} truncate data-testid="workbench-title">
            {activeWorkflowId ? (workflow?.name ?? workflowIdLabel(activeWorkflowId)) : "Workbench"}
          </Text>
          {workflow?.created_via === "agent" && (
            <Badge size="xs" variant="light" color="violet" data-testid="workbench-agent-badge">
              agent-built
            </Badge>
          )}
          {activeWorkflowId && ((workflow?.nodes as unknown[] | undefined)?.length != null) && (
            <Badge size="xs" variant="light" color="teal">
              {(workflow?.nodes as unknown[]).length} nodes
            </Badge>
          )}
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Tooltip label="Conversation & history" position="bottom" withArrow>
            <Button
              size="xs"
              variant={detail || sending ? "light" : "subtle"}
              leftSection={<IconMessage size={14} />}
              onClick={() => setHistoryOpen(true)}
              data-testid="toggle-history"
            >
              {detail ? `Session ${workflowIdLabel(detail.session_id)}` : "History"}
            </Button>
          </Tooltip>
        </Group>
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

      {/* ── The board: the primary surface, always on ── */}
      {info === null ? (
        <Stack align="center" justify="center" style={{ flex: 1 }} gap="xs">
          <Loader size="sm" />
          <Text size="sm" c="dimmed">
            starting the local runtime…
          </Text>
        </Stack>
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }} data-testid="workbench-canvas">
          <CanvasView
            focus={canvasFocus}
            initialWorkflowId={activeWorkflowId}
            runtimeReady={info !== null}
          />
        </div>
      )}

      {/* ── Quote confirm docks above the input bar ── */}
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

      {/* ── Input bar: the control surface, always present ── */}
      {info !== null && (
        <Composer
          onSend={(text) => void send(text)}
          busy={sending || !info}
          dryRun={dryRun}
          onDryRunChange={onDryRunChange}
          serverDryRun={info.dry_run}
          model={model}
          skills={skills}
          skill={skill}
          onSkillChange={setSkill}
        />
      )}

      {/* ── History + transcript overlay (Figma file-list pattern) ── */}
      <Drawer
        opened={historyOpen}
        onClose={() => setHistoryOpen(false)}
        position="left"
        size={340}
        title={
          <Group gap="xs">
            <IconHistory size={14} />
            <Text size="sm" fw={600}>
              {activeWorkflowId ? (workflow?.name ?? "History") : "All sessions"}
            </Text>
          </Group>
        }
        styles={{ body: { padding: 0 } }}
        data-testid="workbench-history"
      >
        <Stack gap={0} style={{ height: "100%" }}>
          <div style={{ flex: 0, minHeight: 0 }}>
            <SessionList
              sessions={visibleSessions}
              activeId={detail?.session_id ?? null}
              onOpen={(sessionId) => void openSession(sessionId)}
              onNew={() => {
                setDetail(null);
                setStream(emptyStream);
                setNotes([]);
                setHistoryOpen(false);
              }}
              busy={sending}
            />
          </div>
          <div style={{ flex: 1, minHeight: 0, borderTop: "1px solid var(--mantine-color-dark-4)" }}>
            <ConversationView
              detail={detail}
              stream={stream}
              notes={notes}
              busy={sending}
              onLocateBoard={locate}
            />
          </div>
        </Stack>
      </Drawer>
    </Stack>
  );
}

/**
 * Action row → canvas focus. Contract fields only: the runtime writes
 * "params" (src/agent/tools.py), session store keeps board_delta/result_ref
 * verbatim, and TranscriptMessage.params is the frontend shape. Any other
 * field name silently degrades locate to a no-op (#15 pattern) — hence the
 * dedicated data-path test with real-shaped rows.
 */
export function extractCanvasFocus(message: TranscriptMessage): {
  workflow_id: string;
  node_key?: string;
} | null {
  const params = (message.params ?? {}) as Record<string, unknown>;
  const delta = (message.board_delta ?? {}) as Record<string, unknown>;
  const ref = (message.result_ref ?? {}) as Record<string, unknown>;
  const workflowId = typeof params.workflow_id === "string" ? params.workflow_id : undefined;
  if (!workflowId) return null;
  const nodeKey =
    typeof ref.node === "string" ? ref.node
    : Array.isArray(ref.wired) && typeof ref.wired[0] === "string" ? (ref.wired[0] as string)
    : typeof delta.added_node === "string" ? delta.added_node
    : undefined;
  return { workflow_id: workflowId, node_key: nodeKey };
}

function workflowIdLabel(id: string): string {
  return id.length > 18 ? `${id.slice(0, 16)}…` : id;
}
