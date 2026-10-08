import { useEffect, useRef, useState } from "react";
import { Box, Button, Card, Stack, Text, Title, Loader, Center, Group, Badge } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";

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
    <Center
      style={{
        height: "100vh",
        background:
          "radial-gradient(900px 420px at 20% 0%, rgba(124,92,255,.16), transparent 60%), radial-gradient(700px 380px at 90% 100%, rgba(38,208,124,.10), transparent 55%)",
      }}
    >
      <Stack align="center" gap="xl" maw={440} px="lg">
        <Group gap={8} wrap="nowrap">
          <Box
            w={34}
            h={34}
            style={{
              borderRadius: 10,
              background: "linear-gradient(135deg, #7c5cff, #26d07c)",
            }}
          />
          <Text fz={22} fw={800}>
            Skilyst
          </Text>
        </Group>
        <Card shadow="lg" padding="xl" radius="lg" withBorder style={{ width: "100%" }}>
          <Stack align="center" gap="md">
            <Title order={3} ta="center">
              Sign in to start creating
            </Title>
            <Text size="sm" c="dimmed" ta="center" lh={1.5}>
              Your agent builds workflows, wires nodes, and generates output — visually.
              Authorization happens in your browser; credentials are stored in your keychain,
              never uploaded.
            </Text>

            {phase === "awaiting" ? (
              <Stack align="center" gap="sm" w="100%">
                <Loader size="sm" />
                <Text size="sm" fw={500}>
                  Waiting for browser authorization…
                </Text>
                <Text size="xs" c="dimmed" ta="center">
                  Complete the login in the browser window, then return here.
                </Text>
              </Stack>
            ) : (
              <Button
                fullWidth
                size="md"
                radius="md"
                leftSection={<IconSparkles size={16} />}
                onClick={login}
                data-testid="signin-button"
              >
                Sign in with Skilyst
              </Button>
            )}

            {phase === "error" && error && (
              <Text size="sm" c="red.6" ta="center" data-testid="signin-error">
                {error}
              </Text>
            )}
          </Stack>
        </Card>
        <Text size="xs" c="dimmed" ta="center">
          Developer? Set <code>SKILYST_DEV_PROFILE</code> to use local credentials and skip
          this screen.
        </Text>
      </Stack>
    </Center>
  );
}

/** Renewal urgency model, exported for tests and reuse.
 * Returns null when expiry is unknown (no expires_in from the runtime). */
export function expiryUrgency(expiresIn: number | undefined): {
  expiringSoon: boolean;
  daysLeft: number | null;
} {
  if (!expiresIn) return { expiringSoon: false, daysLeft: null };
  const hours = Math.floor(expiresIn / 3600);
  return { expiringSoon: hours <= 72, daysLeft: Math.round((hours / 24) * 10) / 10 };
}

/** Header account chip + logout popover (used once authenticated). */
export function AccountChip({
  status,
  onLogout,
  onReauthorize,
}: {
  status: AuthStatus;
  onLogout: () => void;
  /** Called when the user wants to proactively renew before expiry. */
  onReauthorize?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const name = status.account?.name || status.account?.uid || "signed in";
  const { expiringSoon, daysLeft } = expiryUrgency(status.expires_in);
  return (
    <Group gap="xs" style={{ position: "relative" }}>
      <Badge
        variant="light"
        color={expiringSoon ? "yellow" : "indigo"}
        style={{ cursor: "pointer" }}
        onClick={() => setOpen((v) => !v)}
        data-testid="account-chip"
      >
        {name}
        {expiringSoon ? " · expiring" : ""}
      </Badge>
      {open && (
        <Card
          shadow="sm" padding="sm" radius="md" withBorder
          style={{ position: "absolute", top: "130%", right: 0, zIndex: 50, minWidth: 240 }}
        >
          <Stack gap="xs">
            <Text size="sm" fw={600}>{name}</Text>
            {status.scopes && (
              <Text size="xs" c="dimmed">scopes: {status.scopes.join(", ")}</Text>
            )}
            {daysLeft !== null && (
              <Text size="xs" c={expiringSoon ? "yellow.4" : "dimmed"}>
                {expiringSoon
                  ? `session expires in ~${daysLeft} day${daysLeft === 1 ? "" : "s"} — renew now to stay signed in`
                  : `signed in for ~${daysLeft} more day${daysLeft === 1 ? "" : "s"}`}
              </Text>
            )}
            {expiringSoon && onReauthorize && (
              <Button size="xs" variant="light" color="yellow" onClick={onReauthorize}>
                Re-authorize now
              </Button>
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
