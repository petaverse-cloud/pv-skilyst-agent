import { useState } from "react";
import { Button, Card, Stack, Text, Title, Loader, Center, Group, Badge } from "@mantine/core";

export interface AuthStatus {
  state: string;
  authenticated: boolean;
  dev_mode?: boolean;
  account?: { uid: string; name: string };
  scopes?: string[];
  expires_in?: number;
}

import { api } from "../api";

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return api<AuthStatus>("/auth/status");
}

export async function postAuthLogin(): Promise<AuthStatus> {
  return api<AuthStatus>("/auth/login", { method: "POST", body: {} });
}

export async function postAuthLogout(): Promise<AuthStatus> {
  return api<AuthStatus>("/auth/logout", { method: "POST", body: {} });
}

/**
 * Login gate — shown when the runtime reports no credentials.
 * Primary: web-console account via browser (Figma-style deep link).
 * Secondary: dev credentials (SKILYST_DEV_PROFILE) noted for developers.
 */
export function LoginScreen({
  onAuthenticated,
}: {
  onAuthenticated: () => void;
}) {
  const [phase, setPhase] = useState<"idle" | "awaiting" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const login = async () => {
    setPhase("awaiting");
    setError(null);
    try {
      const result = await postAuthLogin();
      // serve returns {state: "authenticated", ...} without a boolean flag in
      // some payload shapes; accept either.
      if (result.authenticated || result.state === "authenticated") {
        onAuthenticated();
        return;
      }
      setPhase("error");
      setError(
        "Login was started but did not complete. In real mode, complete the " +
          "authorization in the browser that opened, then return here."
      );
    } catch (exc) {
      setPhase("error");
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <Center style={{ height: "100vh" }}>
      <Card shadow="sm" padding="xl" radius="md" withBorder style={{ maxWidth: 420 }}>
        <Stack align="center" gap="lg">
          <Title order={2} c="indigo">Skylyst Agent</Title>
          <Text c="dimmed" ta="center">
            Sign in with your Skilyst account to start creating.
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
              Sign in with Skilyst account
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
