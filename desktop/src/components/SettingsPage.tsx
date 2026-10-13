import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { api, type DoctorReport, type RedactedConfig, type RuntimeInfo } from "../api";
import {
  getBillingHistory,
  getWalletBalance,
  type BillingEvent,
} from "../beehiveClient";
import type { AuthStatus } from "./LoginScreen";

/** #99: core amounts are micro-USD integers — display at the edge, only here. */
function formatMicroUsd(micro: number): string {
  const usd = micro / 1_000_000;
  return `${usd < 0 ? "-" : "+"}$${Math.abs(usd).toFixed(4)}`;
}

/** Ledger timestamps render as locale dates; the zero-value 0001-01-01
 *  sentinel (core's unset) renders as "—" instead of a nonsense year. */
function formatLedgerTime(iso: string): string {
  if (!iso || iso.startsWith("0001-")) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/**
 * Settings (phase 1): show what the runtime actually resolved, and let the operator
 * pick the model used for the next message.
 *
 * Model *routing* (base_url, key, fallbacks) is read from the runtime's own
 * configuration -- the shell never writes credentials anywhere, and there is exactly
 * one place that holds them (`~/.skilyst/env` or the process environment). The model
 * selector is a per-message override sent with the request, so an experiment cannot
 * silently become the stored default.
 */
export default function SettingsPage({
  info,
  model,
  onModelChange,
  onStopRuntime,
  onStartRuntime,
  connecting,
  authStatus,
  onSignOut,
  onRefreshCredential,
}: {
  info: RuntimeInfo | null;
  model: string;
  onModelChange: (value: string) => void;
  /** #31: start/stop moved here from the old header — infrastructure, not a module. */
  onStopRuntime: () => void;
  onStartRuntime: () => void;
  connecting: boolean;
  /** The account card: who is signed in, what scopes the pair carries,
   * when it expires — plus the two actions (sign out / refresh). */
  authStatus: AuthStatus | null;
  onSignOut: () => void;
  onRefreshCredential: () => void;
}) {
  const [config, setConfig] = useState<RedactedConfig | null>(null);
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // #99 billing ledger: read-only wallet + history.
  const [billing, setBilling] = useState<{ events: BillingEvent[]; total: number } | null>(null);
  const [billingError, setBillingError] = useState<string | null>(null);
  const [walletBalance, setWalletBalance] = useState<number | null>(null);

  const reloadBilling = useCallback(async () => {
    setBillingError(null);
    setBilling(null);
    try {
      const [history, balance] = await Promise.all([
        getBillingHistory(20, 0),
        getWalletBalance().catch(() => null),
      ]);
      setBilling(history);
      setWalletBalance(balance);
    } catch (err) {
      setBilling(null);
      setBillingError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextConfig, nextDoctor] = await Promise.all([
        api<RedactedConfig>("/config"),
        api<DoctorReport>("/doctor"),
      ]);
      setConfig(nextConfig);
      setDoctor(nextDoctor);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  // The billing face needs an authenticated proxy; in dev mode (no account)
  // it stays in the not-loaded state rather than erroring per render.
  useEffect(() => {
    if (authStatus?.authenticated) void reloadBilling();
  }, [authStatus?.authenticated, reloadBilling]);

  useEffect(() => {
    if (info) void refresh();
  }, [info, refresh]);

  const fallbacks = config?.llm.fallbacks ?? [];

  return (
    <Stack gap="md" p="md">
      <Group justify="space-between">
        <Title order={4}>Settings</Title>
        <Button
          size="xs"
          variant="default"
          leftSection={<IconRefresh size={14} />}
          onClick={() => void refresh()}
          loading={loading}
        >
          Refresh
        </Button>
      </Group>

      {error ? <Alert color="red" title="Runtime error">{error}</Alert> : null}

      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>Account</Title>
          {authStatus?.authenticated ? (
            <Group gap="xs">
              <Button
                size="xs"
                variant="default"
                onClick={onRefreshCredential}
                title="Rotate the stored pair server-side (72h overlap on the old key). Falls back to the full sign-in when the pair is past the window."
                data-testid="refresh-credential"
              >
                Refresh credential
              </Button>
              <Button size="xs" color="red" variant="light" onClick={onSignOut} data-testid="settings-sign-out">
                Sign out
              </Button>
            </Group>
          ) : (
            <Badge size="sm" color="orange">
              {authStatus?.dev_mode ? "dev mode (no account)" : "not signed in"}
            </Badge>
          )}
        </Group>
        {authStatus?.authenticated ? (
          <Table variant="vertical" withRowBorders={false} fz="sm">
            <Table.Tbody>
              <Table.Tr>
                <Table.Th w={200}>Account</Table.Th>
                <Table.Td>
                  {authStatus.account?.name ?? "—"}
                  {authStatus.account?.uid ? <Text size="xs" c="dimmed" span> · {authStatus.account.uid}</Text> : null}
                </Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Scopes</Table.Th>
                <Table.Td>
                  <Group gap={4}>
                    {(authStatus.scopes ?? []).map((scope) => (
                      <Badge key={scope} size="xs" variant="light" data-testid="scope-badge">{scope}</Badge>
                    ))}
                    {(authStatus.scopes ?? []).length === 0 ? <Text size="xs" c="dimmed">none reported</Text> : null}
                  </Group>
                </Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Expires in</Table.Th>
                <Table.Td>
                  {authStatus.expires_in != null ? (
                    <Badge
                      size="xs"
                      color={authStatus.expires_in > 7 * 24 * 3600 ? "teal" : authStatus.expires_in > 72 * 3600 ? "yellow" : "red"}
                      data-testid="credential-expiry"
                    >
                      {Math.floor(authStatus.expires_in / 86400)} days
                    </Badge>
                  ) : "—"}
                </Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Stored in</Table.Th>
                <Table.Td>
                  <Badge size="xs" variant="light" color="teal">macOS keychain</Badge>
                  <Text size="xs" c="dimmed" span> · the pair never touches the webview</Text>
                </Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        ) : (
          <Text size="sm" c="dimmed">
            {authStatus?.dev_mode
              ? "Running in dev mode: requests stay local, no platform account is signed in."
              : "No account is signed in. The app gate should be showing the sign-in screen."}
          </Text>
        )}
      </Card>

      {/* ── Billing (#99): the ledger read face — wallet balance + history.
          Read-only by design; the top-up flow waits on the money ruling. ── */}
      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>Billing</Title>
          <Button
            size="xs"
            variant="subtle"
            leftSection={<IconRefresh size={14} />}
            onClick={() => void reloadBilling()}
            data-testid="billing-refresh"
          >
            Refresh
          </Button>
        </Group>
        {billingError ? (
          <Alert color="red" title="Could not load the billing ledger">
            {billingError}
          </Alert>
        ) : billing === null ? (
          <Text size="sm" c="dimmed" data-testid="billing-loading">
            Loading billing…
          </Text>
        ) : (
          <Stack gap={4}>
            <Group gap="xs">
              <Text size="sm" fw={600}>Balance</Text>
              <Text size="sm" c="teal" data-testid="billing-balance">
                ${(walletBalance ?? 0).toFixed(2)}
              </Text>
            </Group>
            {billing.events.length === 0 ? (
              <Text size="sm" c="dimmed" py="xs" data-testid="billing-empty">
                No billing events yet — runs and top-ups will appear here.
              </Text>
            ) : (
              <Table variant="vertical" withRowBorders={false} fz="sm">
                <Table.Tbody>
                  {billing.events.slice(0, 8).map((e) => (
                    <Table.Tr key={e.event_id}>
                      <Table.Td w={110}>
                        <Badge size="xs" variant="light" color={e.amount < 0 ? "grape" : "teal"}>
                          {e.event_type}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="dimmed">
                          {formatMicroUsd(e.amount)}
                          {e.job_id ? ` · ${e.job_id}` : ""}
                        </Text>
                      </Table.Td>
                      <Table.Td w={170}>
                        <Text size="xs" c="dimmed">{formatLedgerTime(e.created_at)}</Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
            {billing.total > billing.events.length ? (
              <Text size="xs" c="dimmed">
                Showing the latest {billing.events.length} of {billing.total} events.
              </Text>
            ) : null}
          </Stack>
        )}
      </Card>

      <Card withBorder>
        <Title order={5} mb="xs">
          Model routing
        </Title>
        <Table variant="vertical" withRowBorders={false} fz="sm">
          <Table.Tbody>
            <Table.Tr>
              <Table.Th w={200}>Endpoint</Table.Th>
              <Table.Td>{config?.llm.base_url ?? "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>API key</Table.Th>
              <Table.Td>
                <Badge size="xs" color={config?.llm.api_key === "set" ? "teal" : "red"}>
                  {config?.llm.api_key ?? "unknown"}
                </Badge>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Configured default</Table.Th>
              <Table.Td>{config?.llm.model ?? "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Declared fallbacks</Table.Th>
              <Table.Td>{fallbacks.length ? fallbacks.join(", ") : "none"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Config file</Table.Th>
              <Table.Td>
                <Code>{config?.env_file ?? "process environment only"}</Code>
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Group align="flex-end" mt="sm" gap="sm">
          <Select
            label="Model for the next message"
            description="Sent with the request; the runtime's stored default is unchanged."
            data={[config?.llm.model ?? "", ...fallbacks].filter(Boolean)}
            value={model || null}
            onChange={(value) => onModelChange(value ?? "")}
            placeholder={config?.llm.model ?? "runtime default"}
            clearable
            w={420}
          />
          <TextInput
            label="…or type one"
            placeholder="provider/model"
            value={model}
            onChange={(event) => onModelChange(event.currentTarget.value)}
            w={260}
          />
        </Group>
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>Credential status (doctor)</Title>
          <Badge size="sm" color={doctor?.runnable ? "teal" : "orange"} data-testid="doctor-status">
            {doctor === null ? "unknown" : doctor.runnable ? "runnable" : "not runnable"}
          </Badge>
        </Group>
        <Table fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Skill</Table.Th>
              <Table.Th>Integrity</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(doctor?.integrity ?? []).map((row) => (
              <Table.Tr key={row.skill_id}>
                <Table.Td>{row.skill_id}</Table.Td>
                <Table.Td>
                  <Badge size="xs" color={row.ok ? "teal" : "red"}>
                    {row.ok ? "ok" : row.error ?? "broken"}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            ))}
            {(doctor?.integrity ?? []).length === 0 ? (
              <Table.Tr>
                <Table.Td colSpan={2}>
                  <Text size="sm" c="dimmed">
                    No skills installed.
                  </Text>
                </Table.Td>
              </Table.Tr>
            ) : null}
          </Table.Tbody>
        </Table>
        <Table variant="vertical" withRowBorders={false} fz="sm" mt="xs">
          <Table.Tbody>
            <Table.Tr>
              <Table.Th w={200}>Platform account</Table.Th>
              <Table.Td>{config?.beehive.account ?? "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Access key</Table.Th>
              <Table.Td>{config?.beehive.access_key ?? "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Secret</Table.Th>
              <Table.Td>
                <Badge size="xs" color={config?.beehive.secret_key === "set" ? "teal" : "red"}>
                  {config?.beehive.secret_key ?? "unknown"}
                </Badge>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Credential source</Table.Th>
              <Table.Td>
                <Code>{config?.beehive.source ?? "—"}</Code>
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>
            Runtime
          </Title>
          <Group gap="xs">
            {info ? (
              <Button size="xs" variant="default" onClick={onStopRuntime} data-testid="stop-runtime">
                Stop runtime
              </Button>
            ) : (
              <Button
                size="xs"
                onClick={onStartRuntime}
                loading={connecting}
                data-testid="start-runtime"
              >
                Start runtime
              </Button>
            )}
          </Group>
        </Group>
        <Table variant="vertical" withRowBorders={false} fz="sm">
          <Table.Tbody>
            <Table.Tr>
              <Table.Th w={200}>Endpoint</Table.Th>
              <Table.Td>
                <Code>{info ? `${info.base_url} (pid ${info.pid})` : "not connected"}</Code>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Mode</Table.Th>
              <Table.Td>
                <Badge size="xs" color={info?.dry_run ? "yellow" : "red"}>
                  {info?.dry_run ? "dry run" : "live (paid jobs allowed)"}
                </Badge>{" "}
                {info?.dry_run ? (
                  <Text size="xs" c="dimmed" span>
                    Paid jobs are refused until the runtime restarts with --live.
                  </Text>
                ) : null}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Skill store</Table.Th>
              <Table.Td>
                <Code>{info?.store_dir ?? "—"}</Code>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Sessions</Table.Th>
              <Table.Td>
                <Code>{info?.sessions_dir ?? "—"}</Code>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Th>Workspace</Table.Th>
              <Table.Td>
                <Code>{info?.workspace_dir ?? "—"}</Code>
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
