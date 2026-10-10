import { ActionIcon, Badge, Group, Select, Stack, Switch, Text, Textarea } from "@mantine/core";
import { IconSend } from "@tabler/icons-react";
import { useState } from "react";

export type SkillOption = {
  skill_id: string;
  title: string;
  version: string;
  degraded: boolean;
};

export default function Composer({
  onSend,
  busy,
  dryRun,
  onDryRunChange,
  serverDryRun,
  model,
  skills,
  skill,
  onSkillChange,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  serverDryRun: boolean;
  model: string;
  /** M3a (#52): installed skills for the picker (GET /skills). */
  skills: SkillOption[];
  /** The selected skill id ("" = no skill, plain conversation). */
  skill: string;
  onSkillChange: (value: string) => void;
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
        {/* M3a (#52): skill picker — the main selector; empty value = plain
            conversation. Degraded installs are visible with a ⚠ marker so the
            user knows WHY a skill is listed but not loadable (G4: the picker
            shows what is wrong instead of hiding it). */}
        <Select
          size="xs"
          clearable
          placeholder="skill"
          data-testid="skill-picker"
          value={skill || null}
          onChange={(value) => onSkillChange(value ?? "")}
          style={{ minWidth: 220 }}
          maxDropdownHeight={280}
          data={skills.map((s) => ({
            value: s.skill_id,
            label: `${s.degraded ? "⚠ " : ""}${s.title} · v${s.version}`,
          }))}
        />
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
