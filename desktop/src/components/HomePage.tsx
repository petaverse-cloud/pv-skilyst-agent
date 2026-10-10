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
import { listWorkflows, listSkills, type WorkflowRow, type SkillCardData } from "../beehiveClient";
import { sessionsFor, type WorkflowSessions } from "../workflowRegistry";
import { navigate } from "../router";

// WorkflowRow comes from beehiveClient (#36 unified posture); BR-A cover_url
// and BR-B created_via are carried there.

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

/** #54: the works wall's skill-dimension card (design doc
 * docs/design/works-wall-skill-card.md §2). Every card carries the three
 * invariants — skill identity, visual body (cover or nameCover fallback),
 * lineage hint — regardless of what core P1's final field names settle to
 * (the normalizer in beehiveClient absorbs the drift). */
function SkillCard({ sk }: { sk: SkillCardData }) {
  const cover = sk.reference_workflow?.cover_url ?? null;
  const price = sk.pricing;
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
      onClick={() => navigate({ module: "workbench" })}
      data-testid="skill-card"
    >
      <Box
        h={130}
        style={{
          position: "relative",
          background: cover ? `center / cover no-repeat url(${cover})` : nameCover(sk.display_name),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {!cover && (
          <Text c="white" fz={30} fw={700} lh={1} style={{ letterSpacing: 1, textShadow: "0 1px 8px rgba(0,0,0,.35)" }}>
            {initials(sk.display_name)}
          </Text>
        )}
        {price && (
          <Badge
            size="xs"
            variant="light"
            color={price.free ? "green" : "yellow"}
            style={{ position: "absolute", top: 8, right: 8 }}
            data-testid="skill-price-badge"
          >
            {price.free ? "Free" : `$${price.price_usd}`}
          </Badge>
        )}
      </Box>
      <Stack gap={2} p="sm">
        <Text size="sm" fw={500} truncate>
          {sk.display_name}
        </Text>
        <Group gap={4} wrap="nowrap">
          <Text size="xs" c="dimmed" truncate>
            by {sk.author_name || "unknown"}
          </Text>
          {sk.author_verified && (
            <Badge size="xs" variant="light" color="blue" data-testid="author-verified">
              ✓
            </Badge>
          )}
          {sk.version && <Text size="xs" c="dimmed">· v{sk.version}</Text>}
        </Group>
        <Group gap="xs" justify="space-between">
          {/* Lineage hint — the deep fork tree stays on the console (web#356
              dual-host split); the card shows depth only. */}
          {sk.fork_depth > 0 ? (
            <Text size="xs" c="dimmed" data-testid="fork-depth">
              {sk.fork_depth} upstream{sk.fork_depth === 1 ? "" : "s"}
            </Text>
          ) : (
            <Text size="xs" c="dimmed">original</Text>
          )}
          {sk.usage_count > 0 && (
            <Text size="xs" c="dimmed">{sk.usage_count} runs</Text>
          )}
        </Group>
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
  // #54: the skill dimension of the wall — the registry list through the
  // runtime proxy. Until the account's pair is re-issued with skills:read
  // (a fresh login walks the new AgentScopes preset), this answers 403 and
  // the section degrades to the notice below — loud, not broken.
  const [skills, setSkills] = useState<SkillCardData[] | null>(null);
  const [skillsNotice, setSkillsNotice] = useState<string | null>(null);

  const loadSkills = useCallback(async () => {
    setSkillsNotice(null);
    setSkills(null);
    try {
      const rows = await listSkills(50, 0);
      setSkills(rows);
    } catch (exc) {
      // Degrade, never break (design doc §4): the workflow wall keeps
      // rendering; the skill dimension shows why it is absent.
      setSkillsNotice(exc instanceof Error ? exc.message : String(exc));
      setSkills([]);
    }
  }, []);

  const loadWorkflows = useCallback(async () => {
    setListError(null);
    setWorkflows(null);
    try {
      // Unified posture (#36): runtime proxy with keychain AK/SK signing —
      // never a direct webview→beehive call, never a localStorage web token.
      const rows = await listWorkflows(50, 0);
      setWorkflows(rows);
    } catch (exc) {
      // Loud failure (G4-15): a broken list must be visible and diagnosable.
      setListError(exc instanceof Error ? exc.message : String(exc));
      setWorkflows([]);
    }
  }, []);

  useEffect(() => {
    // Re-run when the runtime connects: on a cold start HomePage mounts
    // before startRuntime() finishes, and api() would fail its first
    // load with "not connected" forever (no retry) — the works wall
    // stayed dead even though the runtime was perfectly healthy.
    void loadWorkflows();
    void loadSkills();
  }, [loadWorkflows, loadSkills, runtimeConnected]);

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

        {/* ── Skills (the #54 dimension) ── */}
        <Box mt="lg" pt="md" style={{ borderTop: "1px solid var(--mantine-color-dark-4)" }}>
          <Group justify="space-between" mb="xs">
            <Title order={5}>Skills</Title>
            <Text size="xs" c="dimmed">
              {skills === null ? "" : `${skills.length} published`}
            </Text>
          </Group>
          {skillsNotice ? (
            // Design doc §4 degrade ladder: pre-scope notice, not an error
            // state — the registry itself is deployed; the account's pair
            // predates skills:read. A fresh sign-in lights this section up.
            <Text size="sm" c="dimmed" py="md" maw={520}>
              The skill dimension is not available for this account yet —{" "}
              {skillsNotice}. A fresh sign-in (Settings → Sign in) re-issues
              the credential with skills:read and lights this section up.
            </Text>
          ) : skills === null ? (
            <Group gap="sm" py="md" justify="center">
              <Loader size="xs" />
              <Text size="xs" c="dimmed">Loading skills…</Text>
            </Group>
          ) : skills.length === 0 ? (
            <Text size="sm" c="dimmed" py="md">
              No published skills in the registry yet.
            </Text>
          ) : (
            <SimpleGrid cols={{ base: 2, sm: 3, lg: 4 }} spacing="md" style={{ alignItems: "start" }}>
              {skills.map((sk) => (
                <SkillCard key={sk.skill_id} sk={sk} />
              ))}
            </SimpleGrid>
          )}
        </Box>
      </Stack>
    </Stack>
  );
}
