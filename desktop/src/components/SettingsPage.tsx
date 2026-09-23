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
}: {
  info: RuntimeInfo | null;
  model: string;
  onModelChange: (value: string) => void;
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
        <Title order={5} mb="xs">
          Runtime
        </Title>
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
                </Badge>
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
