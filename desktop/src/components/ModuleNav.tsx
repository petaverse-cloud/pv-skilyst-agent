/**
 * ModuleNav (issue #31) — the left rail, aligned with the web console's
 * AppSidebar semantics: grouped module entries only, collapsible, with the
 * account chip and the runtime mode badge in the footer. The session list
 * does NOT live here — chat history belongs to the workbench (workflow
 * domain), per the 2026-10-08 ruling.
 */

import { NavLink, Badge, Box, Group, Stack, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconHome,
  IconMessages,
  IconSettings,
  IconLayoutSidebarLeftCollapse,
} from "@tabler/icons-react";
import type { Route } from "../router";
import { href } from "../router";
import { AccountChip, type AuthStatus } from "./LoginScreen";

export type RuntimeBadgeState = { dryRun: boolean; port: number } | "connecting" | "off";

function RuntimeBadge({ state }: { state: RuntimeBadgeState }) {
  if (state === "off") {
    return (
      <Badge size="xs" variant="light" color="gray" data-testid="runtime-mode">
        runtime off
      </Badge>
    );
  }
  if (state === "connecting") {
    return (
      <Badge size="xs" variant="light" color="gray" data-testid="runtime-mode">
        connecting…
      </Badge>
    );
  }
  return (
    <Badge size="xs" variant="light" color={state.dryRun ? "yellow" : "red"} data-testid="runtime-mode">
      {state.dryRun ? "dry run" : "live"} · port {state.port}
    </Badge>
  );
}

export default function ModuleNav({
  route,
  authStatus,
  runtimeBadge,
  onLogout,
}: {
  route: Route;
  authStatus: AuthStatus | null;
  runtimeBadge: RuntimeBadgeState;
  onLogout: () => void;
}) {
  const [collapsed, { toggle }] = useDisclosure(false);

  const modules = [
    { id: "home", label: "Home", icon: <IconHome size={16} /> },
    { id: "workbench", label: "Workbench", icon: <IconMessages size={16} /> },
    { id: "settings", label: "Settings", icon: <IconSettings size={16} /> },
  ] as const;

  return (
    <Stack gap="xs" h="100%" justify="space-between" p="xs" w={collapsed ? 56 : 200}>
      <Stack gap="xs">
        <Group justify={collapsed ? "center" : "space-between"} wrap="nowrap">
          {!collapsed && (
            <Text size="sm" fw={600} truncate>
              Skilyst
            </Text>
          )}
          <Tooltip label={collapsed ? "Expand" : "Collapse"} position="right">
            <UnstyledButton onClick={toggle} c="dimmed" data-testid="nav-collapse">
              <IconLayoutSidebarLeftCollapse size={16} className={collapsed ? "" : "flip-x"} />
            </UnstyledButton>
          </Tooltip>
        </Group>
        {modules.map((mod) => (
          <NavLink
            key={mod.id}
            active={route.module === mod.id}
            label={collapsed ? undefined : mod.label}
            leftSection={mod.icon}
            href={href({ module: mod.id })}
            variant="light"
            w="100%"
            data-testid={`nav-${mod.id}`}
          />
        ))}
      </Stack>
      <Stack gap="xs" align={collapsed ? "center" : "stretch"}>
        <RuntimeBadge state={runtimeBadge} />
        {authStatus?.authenticated ? (
          <AccountChip status={authStatus} onLogout={onLogout} />
        ) : null}
        {collapsed ? null : (
          <Box>
            <Text size="xs" c="dimmed" lh={1.2}>
              Local runtime · sessions stay on this machine
            </Text>
          </Box>
        )}
      </Stack>
    </Stack>
  );
}
