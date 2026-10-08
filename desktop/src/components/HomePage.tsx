/**
 * HomePage (issue #31 + M3/M4) — the Gallery-evolved home.
 *
 * Visual direction (2026-10-08 Wesley: "设计也不好看"): a dark hero band
 * with a subtle gradient, works grid with hover elevation, and the cover
 * pipeline from the v0.2 needs doc:
 *
 *   cover_url (BR-A, explicit workflow-level setting) → name gradient (default)
 *   created_via (BR-B) → "agent" corner badge
 */

import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { IconArrowRight, IconMessage, IconSparkles } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import type { SessionRow } from "../api";
import { BEEHIVE_TOKEN_KEY } from "../hostAdapter";
import { sessionsFor, type WorkflowSessions } from "../workflowRegistry";
import { navigate } from "../router";

type WorkflowRow = {
  id: string;
  name: string;
  updated_at?: string;
  /** BR-A: explicit workflow-level cover (server settings container). */
  settings?: { cover_url?: string | null } | null;
  /** BR-B: provenance — "agent" entries earn a corner badge. */
  created_via?: string | null;
};

/**
 * Deterministic simplified cover from the name hash — two hues on a diagonal.
 * Zero network, zero assets; the placeholder until an explicit cover exists.
 */
function nameCover(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  const hueA = hash % 360;
  const hueB = (hueA + 60 + (hash % 90)) % 360;
  return `linear-gradient(135deg, hsl(${hueA} 55% 38%), hsl(${hueB} 48% 24%))`;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "?";
  const second = parts.length > 1 ? parts[1][0] : (parts[0]?.[1] ?? "");
  return (first + second).toUpperCase();
}

function WorkflowCard({ wf, sessionCount }: { wf: WorkflowRow; sessionCount: number }) {
  const cover = wf.settings?.cover_url ?? null;
  const viaAgent = wf.created_via === "agent";
  return (
    <Card
      withBorder
      padding={0}
      radius="md"
      style={{ cursor: "pointer", overflow: "hidden", transition: "transform .15s ease, box-shadow .15s ease" }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-2px)";
        e.currentTarget.style.boxShadow = "0 8px 24px rgba(0,0,0,.45)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "";
        e.currentTarget.style.boxShadow = "";
      }}
      onClick={() => navigate({ module: "workbench", workflow: wf.id })}
      data-testid="work-card"
    >
      <Box
        h={130}
        style={{
          position: "relative",
          background: cover ? `center / cover no-repeat url(${cover})` : nameCover(wf.name),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {!cover && (
          <Text
            c="white"
            fz={30}
            fw={700}
            lh={1}
            style={{ letterSpacing: 1, textShadow: "0 1px 8px rgba(0,0,0,.35)" }}
          >
            {initials(wf.name)}
          </Text>
        )}
        {viaAgent && (
          <Badge
            size="xs"
            variant="filled"
            color="violet"
            leftSection={<IconSparkles size={10} />}
            style={{ position: "absolute", top: 8, right: 8 }}
            data-testid="agent-created-badge"
          >
            agent
          </Badge>
        )}
        {sessionCount > 0 && (
          <Badge
            size="xs"
            variant="light"
            color="dark"
            leftSection={<IconMessage size={10} />}
            style={{ position: "absolute", bottom: 8, right: 8 }}
          >
            {sessionCount}
          </Badge>
        )}
      </Box>
      <Stack gap={2} p="sm">
        <Text size="sm" fw={500} truncate>
          {wf.name}
        </Text>
        <Text size="xs" c="dimmed">
          {wf.updated_at?.slice(0, 10) ?? "—"}
        </Text>
      </Stack>
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
  const agentCount = (workflows ?? []).filter((w) => w.created_via === "agent").length;

  return (
    <Stack gap={0} style={{ overflow: "auto", flex: 1 }} data-testid="home-page">
      {/* ── Hero band ── */}
      <Box
        px="xl"
        py="xl"
        style={{
          background:
            "radial-gradient(1100px 280px at 18% -40%, rgba(124,92,255,.25), transparent 60%), radial-gradient(900px 240px at 88% -30%, rgba(38,208,124,.14), transparent 55%)",
          borderBottom: "1px solid var(--mantine-color-dark-4)",
        }}
      >
        <Stack gap={6} maw={640}>
          <Group gap="xs">
            <ThemeIcon variant="light" color="violet" size="lg" radius="md">
              <IconSparkles size={18} />
            </ThemeIcon>
            <Text size="xs" fw={700} c="violet.4" tt="uppercase" style={{ letterSpacing: 1.5 }}>
              Skilyst Studio
            </Text>
          </Group>
          <Title order={2} lh={1.15}>
            Create with your agent, visually.
          </Title>
          <Text size="sm" c="dimmed" maw={520} lh={1.55}>
            Describe the shot you want — the agent builds the workflow, wires the nodes, and
            generates the output. Everything stays on this machine unless you sign in.
          </Text>
          <Group gap="sm" mt="xs">
            <Button
              size="sm"
              leftSection={<IconSparkles size={15} />}
              onClick={() => navigate({ module: "workbench" })}
              data-testid="new-workflow-cta"
            >
              New workflow
            </Button>
            <Button
              size="sm"
              variant="subtle"
              color="gray"
              rightSection={<IconArrowRight size={14} />}
              onClick={() => navigate({ module: "workbench" })}
            >
              Open workbench
            </Button>
          </Group>
        </Stack>
      </Box>

      {/* ── Works wall ── */}
      <Stack gap="sm" px="xl" py="xl" style={{ flex: 1 }}>
        {listError ? (
          <Alert color="red" title="Could not load your workflows">
            {listError}
          </Alert>
        ) : null}
        {workflows === null ? (
          <Group gap="sm" py="xl" justify="center">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              Loading your workflows…
            </Text>
          </Group>
        ) : (
          <>
            <Group justify="space-between">
              <Title order={5}>Your works</Title>
              <Text size="xs" c="dimmed">
                {workflows.length} workflow{workflows.length === 1 ? "" : "s"}
                {agentCount > 0 ? ` · ${agentCount} agent-created` : ""}
                {artifactCount > 0 ? ` · ${artifactCount} local artifact${artifactCount === 1 ? "" : "s"}` : ""}
                {runtimeConnected ? "" : " · runtime offline"}
              </Text>
            </Group>
            {workflows.length === 0 ? (
              <Text size="sm" c="dimmed" py="xl" ta="center" maw={420} mx="auto">
                No workflows yet — the canvas data source has nothing under this account.
                Start one from the workbench.
              </Text>
            ) : (
              <SimpleGrid cols={{ base: 2, sm: 3, lg: 4 }} spacing="md" style={{ alignItems: "start" }}>
                {workflows.map((wf) => (
                  <WorkflowCard
                    key={wf.id}
                    wf={wf}
                    sessionCount={sessionsFor(registry, wf.id).length}
                  />
                ))}
              </SimpleGrid>
            )}
          </>
        )}
      </Stack>
    </Stack>
  );
}
