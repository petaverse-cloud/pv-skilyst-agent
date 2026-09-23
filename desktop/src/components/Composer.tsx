import { ActionIcon, Badge, Group, Stack, Switch, Text, Textarea } from "@mantine/core";
import { IconSend } from "@tabler/icons-react";
import { useState } from "react";

export default function Composer({
  onSend,
  busy,
  dryRun,
  onDryRunChange,
  serverDryRun,
  model,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  serverDryRun: boolean;
  model: string;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    onSend(value);
  };

  return (
    <Stack gap="xs" p="md" style={{ borderTop: "1px solid var(--mantine-color-dark-4)" }}>
      <Group gap="xs">
        <Badge size="xs" variant="light">
          {model || "model: runtime default"}
        </Badge>
        {serverDryRun ? (
          <Badge size="xs" variant="light" color="yellow" data-testid="dry-run-badge">
            dry run (server)
          </Badge>
        ) : (
          <Switch
            size="xs"
            checked={dryRun}
            onChange={(event) => onDryRunChange(event.currentTarget.checked)}
            label="dry run (no paid job)"
          />
        )}
        <Text size="xs" c="dimmed">
          Enter sends, Shift+Enter adds a line
        </Text>
      </Group>
      <Group gap="xs" align="flex-end" wrap="nowrap">
        <Textarea
          style={{ flex: 1 }}
          placeholder="Describe the shot you want, or ask about a skill…"
          autosize
          minRows={2}
          maxRows={8}
          value={text}
          disabled={busy}
          onChange={(event) => setText(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          data-testid="composer"
        />
        <ActionIcon size="lg" onClick={submit} loading={busy} disabled={busy} aria-label="send">
          <IconSend size={18} />
        </ActionIcon>
      </Group>
    </Stack>
  );
}
