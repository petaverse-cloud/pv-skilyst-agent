/**
 * HomePage (issue #31) — the Gallery-evolved home: brand hero, a New
 * Workflow CTA, and a works wall of the user's workflows (server entities)
 * plus locally-owned session artifacts.
 *
 * Cover derivation (per the v0.2 needs doc): no server cover contract yet
 * (BR-A pending), so a workflow card gets a locally generated simplified
 * cover from its name; the explicit cover_url field plugs in when BR-A
 * lands.
 */

import { Alert, Badge, Button, Card, Group, Loader, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight, IconMessage, IconSparkles } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import type { SessionRow } from "../api";
import { BEEHIVE_TOKEN_KEY } from "../hostAdapter";
import { sessionsFor, type WorkflowSessions } from "../workflowRegistry";
import { navigate } from "../router";

type WorkflowRow = { id: string; name: string; updated_at?: string };

/**
 * Deterministic simplified cover: two hues derived from the name hash on a
 * gradient. Zero network, zero assets — the placeholder contract until
 * BR-A's explicit cover_url.
 */
function nameCover(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  const hueA = hash % 360;
  const hueB = (hueA + 60 + (hash % 90)) % 360;
  return `linear-gradient(135deg, hsl(${hueA} 55% 32%), hsl(${hueB} 48% 22%))`;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "?";
  const second = parts.length > 1 ? parts[1][0] : (parts[0]?.[1] ?? "");
  return (first + second).toUpperCase();
}

function WorkflowCard({ wf, sessionCount }: { wf: WorkflowRow; sessionCount: number }) {
  return (
    <Card
      withBorder
      padding="sm"
      style={{ cursor: "pointer", overflow: "hidden" }}
      onClick={() => navigate({ module: "workbench", workflow: wf.id })}
      data-testid="work-card"
    >
      <Group
        justify="center"
        align="center"
        h={110}
        mb="xs"
        style={{
          borderRadius: 6,
          background: nameCover(wf.name),
          color: "white",
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: 1,
        }}
      >
        {initials(wf.name)}
      </Group>
      <Text size="sm" fw={500} truncate>
        {wf.name}
      </Text>
      <Group justify="space-between" mt={2}>
        <Text size="xs" c="dimmed">
          {wf.updated_at?.slice(0, 10) ?? "—"}
        </Text>
        {sessionCount > 0 && (
          <Badge size="xs" variant="light" leftSection={<IconMessage size={11} />}>
            {sessionCount}
          </Badge>
        )}
      </Group>
    </Card>
  );
}

export default function HomePage({
  sessions,
  registry,
  runtimeConnected,
}: {
  sessions: SessionRow[];
  registry: WorkflowSessions;
  runtimeConnected: boolean;
}) {
  const [workflows, setWorkflows] = useState<WorkflowRow[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadWorkflows = useCallback(async () => {
    setListError(null);
    setWorkflows(null);
    const token = window.localStorage.getItem(BEEHIVE_TOKEN_KEY);
    if (!token) {
      // Not signed into the canvas data source: an empty works wall, not an
      // error — the user may purely use local chat.
      setWorkflows([]);
      return;
    }
    try {
      const res = await fetch("/api/v1/workflows?limit=50&offset=0", {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.status === 401) {
        window.localStorage.removeItem(BEEHIVE_TOKEN_KEY);
        setWorkflows([]);
        return;
      }
      if (!res.ok) throw new Error(`workflow list failed (HTTP ${res.status})`);
      const data = await res.json();
      setWorkflows((data.payload ?? data).workflows ?? []);
    } catch (exc) {
      // Loud failure (G4-15): a broken list must be visible and diagnosable.
      setListError(exc instanceof Error ? exc.message : String(exc));
      setWorkflows([]);
    }
  }, []);

  useEffect(() => {
    void loadWorkflows();
  }, [loadWorkflows]);

  const artifactCount = sessions.reduce((sum, s) => sum + (s.artifacts ?? 0), 0);

  return (
    <Stack gap="lg" p="xl" style={{ overflow: "auto", flex: 1 }} data-testid="home-page">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={4}>
          <Title order={3}>Create with your agent</Title>
          <Text size="sm" c="dimmed" maw={520}>
            Describe the shot you want — the agent builds the workflow, wires the nodes, and
            generates the output. Everything stays on this machine unless you sign in.
          </Text>
        </Stack>
        <Button
          size="md"
          leftSection={<IconSparkles size={16} />}
          rightSection={<IconArrowRight size={16} />}
          onClick={() => navigate({ module: "workbench" })}
          data-testid="new-workflow-cta"
        >
          New workflow
        </Button>
      </Group>

      {listError ? (
        <Alert color="red" title="Could not load your workflows">
          {listError}
        </Alert>
      ) : null}

      {workflows === null ? (
        <Group gap="sm">
          <Loader size="sm" />
          <Text size="sm" c="dimmed">
            Loading your workflows…
          </Text>
        </Group>
      ) : (
        <Stack gap="xs">
          <Group justify="space-between">
            <Title order={5}>Your works</Title>
            <Text size="xs" c="dimmed">
              {workflows.length} workflow{workflows.length === 1 ? "" : "s"} · {artifactCount} local
              artifact{artifactCount === 1 ? "" : "s"}
              {runtimeConnected ? "" : " · runtime offline"}
            </Text>
          </Group>
          {workflows.length === 0 ? (
            <Text size="sm" c="dimmed" py="xl" ta="center">
              No workflows yet — the canvas data source has nothing under this account.
              Start one from the workbench.
            </Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, lg: 4 }}>
              {workflows.map((wf) => (
                <WorkflowCard
                  key={wf.id}
                  wf={wf}
                  sessionCount={sessionsFor(registry, wf.id).length}
                />
              ))}
            </SimpleGrid>
          )}
        </Stack>
      )}
    </Stack>
  );
}
