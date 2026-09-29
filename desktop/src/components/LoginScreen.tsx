import { useEffect, useRef, useState } from "react";
import { Button, Card, Stack, Text, Title, Loader, Center, Group, Badge } from "@mantine/core";

export interface AuthStatus {
  state: string;
  authenticated: boolean;
  dev_mode?: boolean;
  account?: { uid: string; name: string };
  scopes?: string[];
  expires_in?: number;
}

import { api, inShell } from "../api";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { runDeepLinkLogin } from "../authFlow";

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return api<AuthStatus>("/auth/status");
}

/** #20: what a login start returns. Mock mode completes inside the call
 * (state "authenticated"); real mode answers "awaiting_browser" with the URL
 * the shell must open — the code comes back later via the deep link. */
export type LoginStart =
  | ({ state: "authenticated" } & AuthStatus)
  | { state: "awaiting_browser"; browser_url: string };

export async function postAuthLogin(): Promise<LoginStart> {
  return api<LoginStart>("/auth/login", { method: "POST", body: {} });
}

/** The shell's deep-link handler captured skilyst://callback?code=...: hand the
 * one-time code to the runtime, which exchanges it for the AK/SK pair. */
export async function deliverAuthCode(code: string): Promise<AuthStatus> {
  return api<AuthStatus>("/auth/deliver-code", { method: "POST", body: { code } });
}

export async function postAuthLogout(): Promise<AuthStatus> {
  return api<AuthStatus>("/auth/logout", { method: "POST", body: {} });
}

/**
 * Login gate — shown when the runtime reports no credentials.
 *
 * #20: the sign-in button drives the deep-link flow, not a direct login call.
 * Click -> POST /auth/login -> the runtime answers {state:'awaiting_browser',
 * browser_url} -> the shell opens the system browser -> the console redirects
 * to skilyst://callback?code=... -> the Rust deep-link handler emits
 * `auth-code` -> this screen POSTs the code to /auth/deliver-code -> the
 * runtime exchanges it (PKCE) for the AK/SK pair and stores it in the OS
 * keychain. In mock mode the login completes inside the first call instead.
 * Secondary: dev credentials (SKILYST_DEV_PROFILE) noted for developers.
 */
export function LoginScreen({
  onAuthenticated,
}: {
  onAuthenticated: () => void;
}) {
  const [phase, setPhase] = useState<"idle" | "awaiting" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  // The pending login's abort (stops the deep-link listener) — the screen can
  // unmount while the browser is still open.
  const abort = useRef<(() => void) | null>(null);
  useEffect(() => () => abort.current?.(), []);

  const login = () => {
    setPhase("awaiting");
    setError(null);
    void runDeepLinkLogin({
      startLogin: postAuthLogin,
      inShell,
      openBrowser: (url) => invoke("shell_open", { url }),
      onAuthCode: (handler) =>
        listen<{ code: string }>("auth-code", (event) => handler(event.payload.code)),
      deliverCode: deliverAuthCode,
      onAuthenticated,
      onFailed: (message) => {
        setPhase("error");
        setError(message);
      },
    }).then((stop) => {
      abort.current = stop;
    });
  };

  return (
    <Center style={{ height: "100vh" }}>
      <Card shadow="sm" padding="xl" radius="md" withBorder style={{ maxWidth: 420 }}>
        <Stack align="center" gap="lg">
          <Title order={2} c="indigo">Skilyst Agent</Title>
          <Text c="dimmed" ta="center">
            Sign in to start creating.
          </Text>

          {phase === "awaiting" ? (
            <Stack align="center" gap="sm">
              <Loader size="sm" />
              <Text size="sm" c="dimmed">
                Waiting for browser authorization...
              </Text>
              <Text size="xs" c="dimmed">
                Complete the login in the browser window, then return here.
              </Text>
            </Stack>
          ) : (
            <Button fullWidth size="md" onClick={login}>
              Sign in
            </Button>
          )}

          {phase === "error" && error && (
            <Text size="sm" c="red" ta="center">{error}</Text>
          )}

          <Text size="xs" c="dimmed" ta="center">
            Developer? Set <code>SKILYST_DEV_PROFILE</code> to use local
            credentials and skip this screen.
          </Text>
        </Stack>
      </Card>
    </Center>
  );
}

/** Header account chip + logout popover (used once authenticated). */
export function AccountChip({
  status,
  onLogout,
}: {
  status: AuthStatus;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const name = status.account?.name || status.account?.uid || "signed in";
  const hours = status.expires_in ? Math.floor(status.expires_in / 3600) : null;
  return (
    <Group gap="xs" style={{ position: "relative" }}>
      <Badge
        variant="light"
        color="indigo"
        style={{ cursor: "pointer" }}
        onClick={() => setOpen((v) => !v)}
      >
        {name}
      </Badge>
      {open && (
        <Card
          shadow="sm" padding="sm" radius="md" withBorder
          style={{ position: "absolute", top: "130%", right: 0, zIndex: 50, minWidth: 220 }}
        >
          <Stack gap="xs">
            <Text size="sm" fw={600}>{name}</Text>
            {status.scopes && (
              <Text size="xs" c="dimmed">scopes: {status.scopes.join(", ")}</Text>
            )}
            {hours !== null && (
              <Text size="xs" c="dimmed">token expires in ~{hours}h</Text>
            )}
            <Button size="xs" variant="light" color="red" onClick={onLogout}>
              Sign out
            </Button>
          </Stack>
        </Card>
      )}
    </Group>
  );
}
