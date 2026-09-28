/**
 * CanvasView (A3 S1) — the desktop's minimal integration of the
 * skilyst-studio component package: log into beehive, pick a workflow,
 * render it READ-ONLY on the package's WorkflowCanvas.
 *
 * This is the S1 acceptance surface ("画板只读渲染一个 workflow"). The full
 * workbench (Composer-driven node ops, mediapool, lock UX) lands S2-S4.
 */

import { Alert, Badge, Button, Group, Loader, Stack, Text, TextInput, Title } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { HostProvider } from "@petaverse/skilyst-studio/host";
import WorkflowCanvas from "@petaverse/skilyst-studio/canvas";
import type { Workflow } from "@petaverse/skilyst-studio/types";
import { BEEHIVE_TOKEN_KEY, desktopHostAdapter } from "../hostAdapter";

type Stage = "login" | "list" | "canvas";

/** A3 S3: where an action card asked to locate — open this board on arrival. */
export type CanvasFocus = { workflow_id: string; node_key?: string } | null;

export function CanvasView({ onExit, focus }: { onExit: () => void; focus?: CanvasFocus }) {
  const host = useMemo(() => desktopHostAdapter(), []);
  // MaterialPanel (package) rides react-query; readOnly mode never mounts it
  // but the provider stays up so the whole package surface is usable.
  const queryClient = useMemo(() => new QueryClient(), []);
  const [stage, setStage] = useState<Stage>(() =>
    window.localStorage.getItem(BEEHIVE_TOKEN_KEY) ? "list" : "login",
  );
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  const loadWorkflows = useCallback(async () => {
    setListError(null);
    setWorkflows(null);
    try {
      const res = await fetch("/api/v1/workflows?limit=50&offset=0", {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${window.localStorage.getItem(BEEHIVE_TOKEN_KEY) ?? ""}`,
        },
      });
      if (res.status === 401) {
        window.localStorage.removeItem(BEEHIVE_TOKEN_KEY);
        setStage("login");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setWorkflows((data.payload ?? data).workflows ?? []);
    } catch (exc) {
      setListError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    if (stage === "list") void loadWorkflows();
  }, [stage, loadWorkflows]);

  // A3 S3 action-card locate: when a focus arrives with the workflow list,
  // auto-select that board (straight to the canvas stage, skipping the manual
  // pick). Node-level highlight needs a focus API on the studio package's
  // WorkflowCanvas, which it does not expose yet — the board opens, the node
  // badge in the header names the target.
  useEffect(() => {
    if (focus && stage === "list" && workflows?.some((wf) => wf.id === focus.workflow_id)) {
      setActiveId(focus.workflow_id);
      setStage("canvas");
    }
  }, [focus, stage, workflows]);

  const login = useCallback(async () => {
    setLoginError(null);
    try {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.payload?.error ?? err?.error ?? `HTTP ${res.status}`);
      }
      const data = await res.json();
      const token = (data.payload ?? data).token as string;
      window.localStorage.setItem(BEEHIVE_TOKEN_KEY, token);
      setStage("list");
    } catch (exc) {
      setLoginError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [username, password]);

  if (stage === "login") {
    return (
      <Stack align="center" justify="center" style={{ flex: 1 }} gap="md" m="xl">
        <Title order={4}>Beehive 登录（画板数据源）</Title>
        <Text size="xs" c="dimmed" maw={420} ta="center">
          A3 S1 只读画板从 beehive dev API 读取 workflow。凭据仅存本机 localStorage，不入库。
        </Text>
        <TextInput
          label="用户名"
          value={username}
          onChange={(e) => setUsername(e.currentTarget.value)}
          w={280}
          data-testid="canvas-login-user"
        />
        <TextInput
          label="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
          w={280}
          data-testid="canvas-login-pass"
        />
        {loginError ? (
          <Alert color="red" w={280}>
            {loginError}
          </Alert>
        ) : null}
        <Button onClick={() => void login()} disabled={!username || !password} data-testid="canvas-login-submit">
          登录
        </Button>
      </Stack>
    );
  }

  if (stage === "list") {
    return (
      <Stack gap="sm" style={{ flex: 1, overflow: "auto" }} p="md">
        <Group justify="space-between">
          <Group gap="sm">
            <Title order={4}>Workflow 画板</Title>
            <Badge size="sm" variant="light" color="teal">A3 S1 · 只读</Badge>
          </Group>
          <Group gap="xs">
            <Button size="xs" variant="default" onClick={() => void loadWorkflows()}>刷新</Button>
            <Button
              size="xs"
              variant="subtle"
              onClick={() => {
                window.localStorage.removeItem(BEEHIVE_TOKEN_KEY);
                setStage("login");
              }}
            >
              退出登录
            </Button>
            <Button size="xs" variant="subtle" onClick={onExit}>返回会话</Button>
          </Group>
        </Group>
        {listError ? (
          <Alert color="red">{listError}</Alert>
        ) : workflows === null ? (
          <Group gap="sm" mt="md"><Loader size="sm" /><Text size="sm" c="dimmed">加载 workflow 列表…</Text></Group>
        ) : workflows.length === 0 ? (
          <Text size="sm" c="dimmed" mt="md">
            该账号在 dev 环境还没有 workflow。先在 web console studio 创建一个。
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
  }

  // stage === "canvas": the package renders the workflow, read-only.
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <Group px="md" py={6} justify="space-between" bg="var(--mantine-color-dark-8)">
        <Group gap="sm">
          <Button size="xs" variant="subtle" onClick={() => setStage("list")}>← Workflow 列表</Button>
          <Badge size="sm" variant="light" color="teal">只读渲染 · skilyst-studio 包</Badge>
          {focus?.node_key ? (
            <Badge size="sm" variant="light" color="blue" data-testid="canvas-focus-node">
              定位节点: {focus.node_key}
            </Badge>
          ) : null}
        </Group>
        <Button size="xs" variant="subtle" onClick={onExit}>返回会话</Button>
      </Group>
      <div style={{ flex: 1, minHeight: 0 }}>
        <QueryClientProvider client={queryClient}>
          <HostProvider adapter={host}>
            <WorkflowCanvas workflowId={activeId} variant="designer" readOnly />
          </HostProvider>
        </QueryClientProvider>
      </div>
    </div>
  );
}
