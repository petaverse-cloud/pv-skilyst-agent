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
import type { AuthStatus } from "./LoginScreen";

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
