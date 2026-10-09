import { Alert, AppShell, Box, Group, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { LoginScreen, fetchAuthStatus, postAuthLogout, postAuthRefresh, type AuthStatus } from "./components/LoginScreen";
import {
  api,
  inShell,
  runtimeStatus,
  startRuntime,
  stopRuntime,
  type RuntimeInfo,
  type SessionRow,
} from "./api";
import ModuleNav, { type RuntimeBadgeState } from "./components/ModuleNav";
import HomePage from "./components/HomePage";
import WorkbenchPage from "./components/WorkbenchPage";
import SettingsPage from "./components/SettingsPage";
import {
  currentRoute,
  normalizeLegacySessionUrl,
  onRouteChange,
  type Route,
} from "./router";
import { loadRegistry, type WorkflowSessions } from "./workflowRegistry";

const MODEL_KEY = "skilyst.model";
// The session list is the only thing the shell shows that another process (a CLI run)
// can change behind its back, so it is polled rather than pushed: the runtime has no
// subscription channel, and a 5s `GET /sessions` on loopback is cheaper than adding one.
const SESSION_POLL_MS = 5000;

export default function App() {
  const [route, setRoute] = useState<Route>(() => currentRoute());
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [registry, setRegistry] = useState<WorkflowSessions>(() => loadRegistry());
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [model, setModel] = useState(() => window.localStorage.getItem(MODEL_KEY) ?? "");
  const [dryRun, setDryRun] = useState(true);
  const [balanceUsd, setBalanceUsd] = useState<number | null>(null);

  const refreshSessions = useCallback(async () => {
    const payload = await api<{ sessions: SessionRow[] }>("/sessions");
    setSessions(payload.sessions);
  }, []);

  const connect = useCallback(async () => {
    setConnecting(true);
    try {
      // #31: the runtime starts automatically — start/stop is a Settings
      // concern, not part of the user's main path.
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
      // Loud failure (G3-11): a dead runtime must not vanish silently into a
      // "not connected" state with no explanation.
      console.error(`[runtime-connect] failed: ${exc instanceof Error ? exc.message : exc}`);
      setInfo(null);
    } finally {
      setConnecting(false);
    }
  }, [refreshSessions]);

  useEffect(() => {
    // The deep-link contract keeps `?session=<id>`; re-anchor it into the
    // hash router so both entry shapes land on one route.
    normalizeLegacySessionUrl();
    setRoute(currentRoute());
    void connect();
    return onRouteChange(setRoute);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await postAuthLogout();
    } finally {
      setAuthStatus(await fetchAuthStatus().catch(() => null));
    }
  }, []);

  // #34: renewal in place — try the refresh endpoint first (core#681, no
  // browser round-trip); only when the pair is past the overlap window does
  // it degrade to the full logout + sign-in gate.
  const handleReauthorize = useCallback(async () => {
    try {
      setAuthStatus(await postAuthRefresh());
    } catch {
      await postAuthLogout().catch(() => undefined);
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

  const chooseModel = useCallback((value: string) => {
    setModel(value);
    window.localStorage.setItem(MODEL_KEY, value);
  }, []);

  const disconnect = useCallback(async () => {
    try {
      await stopRuntime();
    } catch (exc) {
      console.error(`[runtime-stop] failed: ${exc instanceof Error ? exc.message : exc}`);
    }
    setInfo(null);
    setSessions([]);
  }, []);

  const runtimeBadge: RuntimeBadgeState = info
    ? { dryRun: info.dry_run, port: info.port }
    : connecting
      ? "connecting"
      : "off";

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
      header={{ height: 0 }}
      navbar={{ width: { base: 216 }, breakpoint: 0 }}
      padding={0}
      styles={{ main: { display: "flex", flexDirection: "column", height: "100vh" } }}
    >
      <AppShell.Navbar p="xs" w="auto">
        <ModuleNav
          route={route}
          authStatus={authStatus}
          runtimeBadge={runtimeBadge}
          onLogout={() => void handleLogout()}
          onReauthorize={() => void handleReauthorize()}
        />
      </AppShell.Navbar>

      <AppShell.Main>
        {info === null && !connecting && !authStatus?.dev_mode ? (
          <Alert color="red" m="sm" title="Local runtime unavailable" data-testid="runtime-error-banner">
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              The local agent runtime did not come up. Retry from Settings → Runtime, or run
              `skilyst serve` by hand in dev mode.
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
        {route.module === "home" ? (
          <HomePage
            sessions={sessions}
            registry={registry}
            runtimeConnected={info !== null}
          />
        ) : route.module === "workbench" ? (
          <WorkbenchPage
            routeSessionId={route.session}
            routeWorkflowId={route.workflow}
            sessions={sessions}
            registry={registry}
            onRegistryChange={setRegistry}
            info={info ? { dry_run: info.dry_run, port: info.port, pid: info.pid } : null}
            dryRun={dryRun}
            onDryRunChange={setDryRun}
            model={model}
            balanceUsd={balanceUsd}
            onBalanceUsd={setBalanceUsd}
          />
        ) : (
          <Box style={{ flex: 1, overflow: "auto" }}>
            <SettingsPage
              info={info}
              model={model}
              onModelChange={chooseModel}
              onStopRuntime={() => void disconnect()}
              onStartRuntime={() => void connect()}
              connecting={connecting}
            />
          </Box>
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
