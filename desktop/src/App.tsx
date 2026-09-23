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
import {
  api,
  inShell,
  runtimeStatus,
  sendMessage,
  startRuntime,
  stopRuntime,
  type RunSummary,
  type RuntimeInfo,
  type SessionDetail,
  type SessionRow,
} from "./api";
import Composer from "./components/Composer";
import ConversationView from "./components/ConversationView";
import SessionList from "./components/SessionList";
import SettingsPage from "./components/SettingsPage";

const MODEL_KEY = "skilyst.model";

export default function App() {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [view, setView] = useState<"chat" | "settings">("chat");
  const [sending, setSending] = useState(false);
  const [streaming, setStreaming] = useState("");
  const [notes, setNotes] = useState<string[]>([]);
  const [dryRun, setDryRun] = useState(true);
  const [model, setModel] = useState(() => window.localStorage.getItem(MODEL_KEY) ?? "");

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
      setStreaming("");
      try {
        const summary: RunSummary = await sendMessage(
          { message: text, session_id: detail?.session_id, model: model || undefined, dry_run: dryRun },
          {
            onDelta: (chunk) => setStreaming((current) => current + chunk),
            onNote: (line) => setNotes((current) => [...current, line]),
          },
        );
        setStreaming("");
        setDetail(await api<SessionDetail>(`/session/${summary.session_id}`));
        await refreshSessions();
        if (!summary.ok) {
          setError(`${summary.stop_reason}: ${summary.error ?? "the run did not complete"}`);
        }
      } catch (exc) {
        setStreaming("");
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
        {view === "settings" ? (
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
            <ConversationView detail={detail} streaming={streaming} notes={notes} busy={sending} />
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
