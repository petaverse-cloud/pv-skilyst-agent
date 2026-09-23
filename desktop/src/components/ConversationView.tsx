import { Anchor, Badge, Card, Code, Group, Loader, Paper, ScrollArea, Stack, Text } from "@mantine/core";
import type { SessionDetail, TranscriptMessage } from "../api";

function roleLabel(role: string): string {
  if (role === "user") return "You";
  if (role === "assistant") return "Skilyst";
  return role;
}

function Bubble({ message }: { message: TranscriptMessage }) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  if (isTool) {
    return (
      <Card withBorder padding="xs" data-testid="tool-message">
        <Group gap="xs" mb={4}>
          <Badge size="xs" variant="light" color="grape">
            tool
          </Badge>
          <Text size="xs" c="dimmed">
            {message.name ?? "tool"}
          </Text>
        </Group>
        <Code block style={{ maxHeight: 180, overflow: "auto" }}>
          {message.content}
        </Code>
      </Card>
    );
  }
  return (
    <Paper
      withBorder
      p="sm"
      maw="85%"
      ml={isUser ? "auto" : 0}
      mr={isUser ? 0 : "auto"}
      bg={isUser ? "var(--mantine-color-blue-light)" : undefined}
      data-testid={`message-${message.role}`}
    >
      <Text size="xs" c="dimmed" mb={4}>
        {roleLabel(message.role)}
      </Text>
      <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
        {message.content}
      </Text>
    </Paper>
  );
}

export default function ConversationView({
  detail,
  streaming,
  notes,
  busy,
}: {
  detail: SessionDetail | null;
  streaming: string;
  notes: string[];
  busy: boolean;
}) {
  const messages = detail?.messages ?? [];
  const artifacts = detail?.artifacts ?? [];
  return (
    <ScrollArea style={{ flex: 1 }} p="md">
      <Stack gap="sm">
        {detail === null && !busy ? (
          <Text c="dimmed" size="sm">
            No session open. Ask something to start one.
          </Text>
        ) : null}
        {messages.map((message, index) => (
          <Bubble key={`${message.seq ?? index}-${message.role}`} message={message} />
        ))}
        {streaming ? (
          <Paper withBorder p="sm" maw="85%" mr="auto" data-testid="streaming-answer">
            <Text size="xs" c="dimmed" mb={4}>
              Skilyst
            </Text>
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              {streaming}
            </Text>
          </Paper>
        ) : null}
        {notes.length > 0 ? (
          <Card withBorder padding="xs" bg="var(--mantine-color-dark-7)" data-testid="progress-notes">
            <Text size="xs" c="dimmed" mb={4}>
              Progress
            </Text>
            <Stack gap={2}>
              {notes.slice(-8).map((line, index) => (
                <Text key={index} size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>
                  {line}
                </Text>
              ))}
            </Stack>
          </Card>
        ) : null}
        {artifacts.length > 0 ? (
          <Card withBorder padding="xs">
            <Text size="xs" c="dimmed" mb={4}>
              Artifacts
            </Text>
            {artifacts.map((artifact, index) => (
              <Anchor key={index} href={artifact.artifact_url} target="_blank" size="xs">
                {artifact.artifact_url ?? JSON.stringify(artifact)}
              </Anchor>
            ))}
          </Card>
        ) : null}
        {busy ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="xs" c="dimmed">
              the runtime is working
            </Text>
          </Group>
        ) : null}
      </Stack>
    </ScrollArea>
  );
}
