/**
 * ModuleNav (issue #31) — the left rail, aligned with the web console's
 * AppSidebar semantics: module entries with icons, the account chip and the
 * runtime mode badge in the footer. The session list does NOT live here —
 * chat history belongs to the workbench (workflow domain), per the
 * 2026-10-08 ruling.
 */

import {
  Badge,
  Box,
  Group,
  NavLink,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconHome,
  IconMessages,
  IconSettings,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarRightCollapse,
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
    { id: "home", label: "Home", icon: <IconHome size={17} stroke={1.7} /> },
    { id: "workbench", label: "Workbench", icon: <IconMessages size={17} stroke={1.7} /> },
    { id: "settings", label: "Settings", icon: <IconSettings size={17} stroke={1.7} /> },
  ] as const;

  return (
    <Stack gap={0} h="100%" justify="space-between" w={collapsed ? 60 : 210} style={{ transition: "width .18s ease" }}>
      <Box>
        {/* Brand */}
        <Group justify={collapsed ? "center" : "space-between"} wrap="nowrap" px="sm" py="md">
          {!collapsed && (
            <Group gap={6} wrap="nowrap">
              <Box
                w={20}
                h={20}
                style={{
                  borderRadius: 6,
                  background: "linear-gradient(135deg, #7c5cff, #26d07c)",
                  flexShrink: 0,
                }}
              />
              <Text size="sm" fw={700} truncate>
                Skilyst
              </Text>
            </Group>
          )}
          <Tooltip label={collapsed ? "Expand" : "Collapse"} position="right" withArrow>
            <UnstyledButton onClick={toggle} c="dimmed" data-testid="nav-collapse">
              {collapsed ? (
                <IconLayoutSidebarRightCollapse size={16} stroke={1.7} />
              ) : (
                <IconLayoutSidebarLeftCollapse size={16} stroke={1.7} />
              )}
            </UnstyledButton>
          </Tooltip>
        </Group>

        {/* Module entries */}
        <Stack gap={2} px="xs">
          {modules.map((mod) => (
            <NavLink
              key={mod.id}
              active={route.module === mod.id}
              label={collapsed ? undefined : mod.label}
              leftSection={mod.icon}
              href={href({ module: mod.id })}
              variant="subtle"
              w="100%"
              styles={{
                root: { borderRadius: 8 },
                label: { fontWeight: route.module === mod.id ? 600 : 500 },
              }}
              data-testid={`nav-${mod.id}`}
            />
          ))}
        </Stack>
      </Box>

      {/* Footer: runtime + account */}
      <Stack gap="xs" px="sm" pb="sm" align={collapsed ? "center" : "stretch"}>
        <RuntimeBadge state={runtimeBadge} />
        {authStatus?.authenticated ? (
          <AccountChip status={authStatus} onLogout={onLogout} />
        ) : null}
        {collapsed ? null : (
          <Text size="xs" c="dimmed" lh={1.3}>
            Local runtime · sessions stay on this machine
          </Text>
        )}
      </Stack>
    </Stack>
  );
}
