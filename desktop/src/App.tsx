import {
  Alert,
  AppShell,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconPlayerPlay, IconSettings, IconMessage, IconX } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { LoginScreen, AccountChip, fetchAuthStatus, postAuthLogout, type AuthStatus } from "./components/LoginScreen";
import {
  api,
  inShell,
  runtimeStatus,
  resolveConfirm,
  sendMessage,
  startRuntime,
  stopRuntime,
  type RunSummary,
  type RuntimeInfo,
  type SessionDetail,
  type SessionRow,
} from "./api";
import { QuoteConfirmCard, type QuoteView } from "@petaverse/skilyst-studio/session";
import Composer from "./components/Composer";
import ConversationView from "./components/ConversationView";
import SessionList from "./components/SessionList";
import SettingsPage from "./components/SettingsPage";
import { CanvasView } from "./components/CanvasView";
import { appendDelta, emptyStream, freezeTurn, type StreamState } from "./stream";

const MODEL_KEY = "skilyst.model";
// The session list is the only thing the shell shows that another process (a CLI run)
// can change behind its back, so it is polled rather than pushed: the runtime has no
// subscription channel, and a 5s `GET /sessions` on loopback is cheaper than adding one.
const SESSION_POLL_MS = 5000;

export default function App() {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [view, setView] = useState<"chat" | "settings" | "canvas">("chat");
  const [sending, setSending] = useState(false);
  const [stream, setStream] = useState<StreamState>(emptyStream);
  const [notes, setNotes] = useState<string[]>([]);
  const [dryRun, setDryRun] = useState(true);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [model, setModel] = useState(() => window.localStorage.getItem(MODEL_KEY) ?? "");
  // A3 S3: the workflow/node an action card asked to locate on the canvas.
  const [canvasFocus, setCanvasFocus] = useState<{ workflow_id: string; node_key?: string } | null>(null);
  // S4 quote UX: the runtime paused a paid submission at the quote and is
  // waiting on POST /confirm — the card below the transcript answers it.
  const [pendingConfirm, setPendingConfirm] = useState<{ sessionId: string; quote: QuoteView } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  // D3: the runtime never touches the wallet — the desktop fetches the
  // balance itself with the canvas login's beehive JWT, for display only.
  const [balanceUsd, setBalanceUsd] = useState<number | null>(null);

  const refreshSessions = useCallback(async () => {
    const payload = await api<{ sessions: SessionRow[] }>("/sessions");
    setSessions(payload.sessions);
  }, []);

  const connect = useCallback(async () => {
    setConnecting(true);
    setError(null);
    try {
      const status = await runtimeStatus();
      const connected = status ?? (await startRuntime(false));
      setInfo(connected);
      setDryRun(connected.dry_run);
      try {
        setAuthStatus(await fetchAuthStatus());
      } catch {
        // dev profile or older runtime without auth endpoints: treat as dev-mode pass-through
        setAuthStatus({ state: "dev", authenticated: true, dev_mode: true });
      }
      await refreshSessions();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setConnecting(false);
    }
  }, [refreshSessions]);

  useEffect(() => {
    void connect();
  }, [connect]);

  // Deep link: ?session=<id> opens that session once the runtime is connected
  // (openSession needs the runtime's base URL; firing on mount races connect()).
  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get("session");
    if (target && info) void openSession(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  const handleLogout = useCallback(async () => {
    try {
      await postAuthLogout();
    } finally {
      setAuthStatus(await fetchAuthStatus().catch(() => null));
    }
  }, []);

  // A session created by anything other than this window (a CLI run, another shell)
  // shows up within one poll interval instead of needing a restart.
  useEffect(() => {
    if (!info) return;
    const timer = window.setInterval(() => {
      // A failed poll is not worth a banner: the connection state already says the
      // runtime is gone.
      void refreshSessions().catch(() => undefined);
    }, SESSION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [info, refreshSessions]);

  const openSession = useCallback(async (sessionId: string) => {
    setError(null);
    try {
      setDetail(await api<SessionDetail>(`/session/${sessionId}`));
      setView("chat");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  const disconnect = useCallback(async () => {
    try {
      await stopRuntime();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
    setInfo(null);
    setDetail(null);
    setSessions([]);
  }, []);

  const send = useCallback(
    async (text: string) => {
      setSending(true);
      setError(null);
      setNotes([]);
      setStream(emptyStream);
      setPendingConfirm(null);
      try {
        const summary: RunSummary = await sendMessage(
          { message: text, session_id: detail?.session_id, model: model || undefined, dry_run: dryRun, confirm_paid: true },
          {
            onDelta: (chunk) => setStream((state) => appendDelta(state, chunk)),
            // A progress note is the end of the turn that was streaming: freeze what
            // arrived so a tool call does not glue two turns into one bubble.
            onNote: (line) => {
              setStream((state) => freezeTurn(state));
              setNotes((current) => [...current, line]);
            },
            // S4 quote UX: the runtime is holding a paid submission at the
            // quote. Show the card; the user's answer goes to POST /confirm.
            onConfirmRequest: (detail2) => {
              const quote = (detail2.quote ?? {}) as QuoteView;
              setPendingConfirm({ sessionId: detail2.session_id ?? detail?.session_id ?? "", quote });
              // D3: display-only balance, fetched by the desktop with the
              // canvas login's beehive JWT — the runtime never sees the wallet.
              const token = window.localStorage.getItem("skilyst.beehive_token");
              if (token) {
                fetch("/api/v1/billing/wallet", { headers: { Authorization: `Bearer ${token}` } })
                  .then((r) => (r.ok ? r.json() : null))
                  .then((data) => {
                    const payload = data?.payload ?? data;
                    const usd = payload?.balance_usd ?? payload?.balance;
                    if (typeof usd === "number") setBalanceUsd(usd);
                  })
                  .catch(() => setBalanceUsd(null));
              }
            },
          },
        );
        const refreshed = await api<SessionDetail>(`/session/${summary.session_id}`);
        setDetail(refreshed);
        // The transcript now holds everything that streamed; keeping the buffers as
        // well would render every answer twice.
        setStream(emptyStream);
        await refreshSessions();
        if (!summary.ok) {
          setError(`${summary.stop_reason}: ${summary.error ?? "the run did not complete"}`);
        }
      } catch (exc) {
        // Keep whatever the agent had already said: the transcript is not refetched on
        // a failed request, and losing the text would hide the evidence of the failure.
        setStream((state) => freezeTurn(state));
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setSending(false);
      }
    },
    [detail?.session_id, dryRun, model, refreshSessions],
  );

  const chooseModel = useCallback((value: string) => {
    setModel(value);
    window.localStorage.setItem(MODEL_KEY, value);
  }, []);

  // S4 quote UX: answer the runtime's pending gate. Decline leaves the card
  // up until the run ends (the tool error lands as a note), confirm clears it
  // as the submission resumes.
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

  // Login gate: no credentials and not in dev mode -> the whole app is the login screen.
  if (authStatus && !authStatus.authenticated && !authStatus.dev_mode) {
    return (
      <LoginScreen
        onAuthenticated={async () => {
          setAuthStatus(await fetchAuthStatus());
        }}
      />
    );
  }

  return (
    <AppShell
      header={{ height: 52 }}
      navbar={{ width: 300, breakpoint: "sm" }}
      padding={0}
      styles={{ main: { display: "flex", flexDirection: "column", height: "100vh" } }}
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Title order={4}>Skilyst Agent</Title>
            {info ? (
              <Badge size="sm" variant="light" color={info.dry_run ? "yellow" : "red"} data-testid="runtime-mode">
                {info.dry_run ? "dry run" : "live"} · port {info.port}
              </Badge>
            ) : (
              <Badge size="sm" variant="light" color="gray">
                {connecting ? "connecting…" : "not connected"}
              </Badge>
            )}
          </Group>
          <Group gap="xs">
            {authStatus?.authenticated && (
              <AccountChip status={authStatus} onLogout={() => void handleLogout()} />
            )}
            <Button
              size="xs"
              variant={view === "canvas" ? "filled" : "subtle"}
              onClick={() => setView(view === "canvas" ? "chat" : "canvas")}
            >
              画板
            </Button>
            <Button
              size="xs"
              variant="subtle"
              leftSection={view === "settings" ? <IconMessage size={14} /> : <IconSettings size={14} />}
              onClick={() => setView(view === "settings" ? "chat" : "settings")}
            >
              {view === "settings" ? "Chat" : "Settings"}
            </Button>
            {info ? (
              <Button size="xs" variant="default" onClick={() => void disconnect()}>
                Stop runtime
              </Button>
            ) : (
              <Button
                size="xs"
                leftSection={<IconPlayerPlay size={14} />}
                onClick={() => void connect()}
                loading={connecting}
              >
                Start runtime
              </Button>
            )}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <SessionList
          sessions={sessions}
          activeId={detail?.session_id ?? null}
          onOpen={(sessionId) => void openSession(sessionId)}
          onNew={() => {
            setDetail(null);
            setView("chat");
          }}
          busy={sending}
        />
      </AppShell.Navbar>

      <AppShell.Main>
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
        {!inShell() ? (
          <Alert color="blue" m="sm" title="Browser development mode">
            <Text size="sm">
              This window is not the desktop shell, so the runtime has to be started by hand
              (<code>skilyst serve</code>) and passed in through VITE_SKILYST_RUNTIME.
            </Text>
          </Alert>
        ) : null}
        {view === "canvas" ? (
          <CanvasView onExit={() => setView("chat")} focus={canvasFocus} />
        ) : view === "settings" ? (
          <Box style={{ flex: 1, overflow: "auto" }}>
            <SettingsPage info={info} model={model} onModelChange={chooseModel} />
          </Box>
        ) : connecting && !info ? (
          <Stack align="center" justify="center" style={{ flex: 1 }} gap="xs">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              starting the local runtime…
            </Text>
          </Stack>
        ) : (
          <>
            <ConversationView
              detail={detail}
              stream={stream}
              notes={notes}
              busy={sending}
              onLocateBoard={(message) => {
                // Clicking an action card jumps to the canvas and opens the
                // board the action touched. The workflow id is in the action's
                // params (canvas tools carry workflow_id); S4: the node comes
                // from result_ref (added_node / wired edge / pool entry) and
                // the package's focusNode() centers + highlights it.
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
                  setView("canvas");
                }
              }}
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
              onDryRunChange={setDryRun}
              serverDryRun={info?.dry_run ?? true}
              model={model}
            />
          </>
        )}
        {info ? (
          <Group px="md" py={4} gap="sm" bg="var(--mantine-color-dark-8)">
            <Text size="xs" c="dimmed">
              runtime pid {info.pid} · sessions {info.sessions_dir || "—"}
            </Text>
          </Group>
        ) : null}
      </AppShell.Main>
    </AppShell>
  );
}
