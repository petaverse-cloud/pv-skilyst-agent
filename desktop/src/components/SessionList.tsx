import { Badge, Group, NavLink, ScrollArea, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconMessage, IconPlus } from "@tabler/icons-react";
import type { SessionRow } from "../api";

function when(seconds?: number): string {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString();
}

export default function SessionList({
  sessions,
  activeId,
  onOpen,
  onNew,
  busy,
}: {
  sessions: SessionRow[];
  activeId: string | null;
  onOpen: (sessionId: string) => void;
  onNew: () => void;
  busy: boolean;
}) {
  return (
    <Stack gap="xs" h="100%">
      <UnstyledButton onClick={onNew} disabled={busy} data-testid="new-session">
        <Group gap="xs" p="xs">
          <IconPlus size={16} />
          <Text size="sm">New session</Text>
        </Group>
      </UnstyledButton>
      <ScrollArea style={{ flex: 1 }}>
        {sessions.length === 0 ? (
          <Text size="xs" c="dimmed" p="xs">
            No sessions yet. Send a message to start one.
          </Text>
        ) : (
          sessions.map((session) => (
            <NavLink
              key={session.session_id}
              active={session.session_id === activeId}
              onClick={() => onOpen(session.session_id)}
              label={session.title || session.session_id}
              description={when(session.updated_at)}
              leftSection={<IconMessage size={16} />}
              rightSection={
                <Badge size="xs" variant="light">
                  {session.messages}
                </Badge>
              }
            />
          ))
        )}
      </ScrollArea>
    </Stack>
  );
}
