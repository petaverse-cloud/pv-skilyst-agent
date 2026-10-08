/**
 * CanvasView (A3 S1) — the desktop's minimal integration of the
 * skilyst-studio component package: log into the skilyst platform, pick a workflow,
 * render it READ-ONLY on the package's WorkflowCanvas.
 *
 * This is the S1 acceptance surface (read-only render of one workflow). The full
 * workbench (Composer-driven node ops, mediapool, lock UX) lands S2-S4.
 */

import { Alert, Badge, Button, Group, Loader, Stack, Text, TextInput, Title } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HostProvider } from "@petaverse/skilyst-studio/host";
import WorkflowCanvas, { type WorkflowCanvasHandle } from "@petaverse/skilyst-studio/canvas";
import type { Workflow } from "@petaverse/skilyst-studio/types";
import { desktopHostAdapter } from "../hostAdapter";
import { listWorkflows } from "../beehiveClient";

type Stage = "list" | "canvas";

/** A3 S3: where an action card asked to locate — open this board on arrival. */
export type CanvasFocus = { workflow_id: string; node_key?: string } | null;

export function CanvasView({
  onExit,
  focus,
  initialWorkflowId,
}: {
  onExit: () => void;
  focus?: CanvasFocus;
  /** #31: the workbench pane opens the canvas bound to this workflow. */
  initialWorkflowId?: string | null;
}) {
  const host = useMemo(() => desktopHostAdapter(), []);
  // MaterialPanel (package) rides react-query; readOnly mode never mounts it
  // but the provider stays up so the whole package surface is usable.
  const queryClient = useMemo(() => new QueryClient(), []);
  // The web-token login stage is retired (#36 unified posture): the app gate
  // (keychain AK/SK via the runtime) IS the data-source login. Canvas data
  // flows through the runtime proxy like every other beehive call.
  const [stage, setStage] = useState<Stage>("list");
  // The workbench embed starts straight on the bound board (skipping the
  // manual pick) when a workflow id is provided and we are signed in.
  const [activeId, setActiveId] = useState<string | null>(initialWorkflowId ?? null);
  useEffect(() => {
    if (initialWorkflowId && stage === "list") {
      setActiveId(initialWorkflowId);
      setStage("canvas");
    }
  }, [initialWorkflowId, stage]);
  const loginError = null as string | null; // retired with the web-token stage
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadWorkflows = useCallback(async () => {
    setListError(null);
    setWorkflows(null);
    try {
      // Unified posture (#36): runtime proxy, keychain-signed. A 401 here means
      // the app-level login lapsed — the App gate handles that screen.
      const rows = await listWorkflows(50, 0);
      setWorkflows(rows as unknown as Workflow[]);
    } catch (exc) {
      setListError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    if (stage === "list") void loadWorkflows();
  }, [stage, loadWorkflows]);

  // A3 S3 action-card locate: when a focus arrives with the workflow list,
  // auto-select that board (straight to the canvas stage, skipping the manual
  // pick). S4: the package now exposes focusNode(nodeId) — the node-level
  // highlight + viewport centering fires once the canvas has mounted.
  useEffect(() => {
    if (focus && stage === "list" && workflows?.some((wf) => wf.id === focus.workflow_id)) {
      setActiveId(focus.workflow_id);
      setStage("canvas");
    }
  }, [focus, stage, workflows]);

  const canvasRef = useRef<WorkflowCanvasHandle>(null);

  // focusNode fires after the canvas mounts with the focused board; a short
  // settle lets the node layout land before the viewport centers on it.
  useEffect(() => {
    if (!focus?.node_key || stage !== "canvas") return;
    const timer = window.setTimeout(() => {
      canvasRef.current?.focusNode(focus.node_key!);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [focus, stage]);

  // stage === "list": the login stage is retired; the app gate owns sign-in.
  return (
      <Stack gap="sm" style={{ flex: 1, overflow: "auto" }} p="md">
        <Group justify="space-between">
          <Group gap="sm">
            <Title order={4}>Workflow boards</Title>
            <Badge size="sm" variant="light" color="teal">A3 S4 · Workbench</Badge>
          </Group>
          <Group gap="xs">
            <Button size="xs" variant="default" onClick={() => void loadWorkflows()}>Refresh</Button>
            <Button size="xs" variant="subtle" onClick={onExit}>Back to chat</Button>
          </Group>
        </Group>
        {listError ? (
          <Alert color="red">{listError}</Alert>
        ) : workflows === null ? (
          <Group gap="sm" mt="md"><Loader size="sm" /><Text size="sm" c="dimmed">Loading workflow list…</Text></Group>
        ) : workflows.length === 0 ? (
          <Text size="sm" c="dimmed" mt="md">
            This account has no workflows in the dev environment yet. Create one in the web console studio first.
          </Text>
        ) : (
          workflows.map((wf) => (
            <Group
              key={wf.id}
              justify="space-between"
              px="sm"
              py="xs"
              style={{ border: "1px solid var(--mantine-color-dark-4)", borderRadius: 6, cursor: "pointer" }}
              onClick={() => {
                setActiveId(wf.id);
                setStage("canvas");
              }}
            >
              <div>
                <Text size="sm" fw={500}>{wf.name}</Text>
                <Text size="xs" c="dimmed" ff="monospace">{wf.id} · {(wf.nodes ?? []).length} nodes</Text>
              </div>
              <Text size="xs" c="dimmed">{wf.updated_at?.slice(0, 16).replace("T", " ")}</Text>
            </Group>
          ))
        )}
      </Stack>
  );

  // stage === "canvas": the package renders the workflow, read-only.
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <Group px="md" py={6} justify="space-between" bg="var(--mantine-color-dark-8)">
        <Group gap="sm">
          <Button size="xs" variant="subtle" onClick={() => setStage("list")}>← Workflow list</Button>
          <Badge size="sm" variant="light" color="teal">Full workbench · skilyst-studio package</Badge>
          {(() => {
            const focusedNode = focus?.node_key;
            return focusedNode != null ? (
              <Badge size="sm" variant="light" color="blue" data-testid="canvas-focus-node">
                Focus node: {focusedNode}
              </Badge>
            ) : null;
          })()}
        </Group>
        <Button size="xs" variant="subtle" onClick={onExit}>Back to chat</Button>
      </Group>
      <div style={{ flex: 1, minHeight: 0 }}>
        <QueryClientProvider client={queryClient}>
          <HostProvider adapter={host}>
            <WorkflowCanvas ref={canvasRef} workflowId={activeId} variant="designer" liveBoard />
          </HostProvider>
        </QueryClientProvider>
      </div>
    </div>
  );
}
