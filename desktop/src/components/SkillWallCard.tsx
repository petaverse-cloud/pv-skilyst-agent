import { Badge, Box, Button, Card, Group, Text } from "@mantine/core";
import { useState } from "react";
import { navigate } from "../router";
import type { SkillPreviewCard } from "../beehiveClient";

/**
 * #102 (core#833): the wall's subject is the SKILL. One card = one skill;
 * the main content is its preview media (demo/reference run outputs,
 * media-first), the footer carries display_name + fork count. Like the
 * web console's card: fork is the action (it lands in the workbench);
 * comments stay out of the MVP subset. slug is never rendered (#818).
 *
 * Previews are bare URIs (the feed contract has no mime field) — the
 * extension carries the type; an unknown extension renders the typed
 * placeholder, not a gray box.
 */
function previewKind(uri: string): "video" | "image" | "audio" | "other" {
  const clean = uri.split("?")[0].toLowerCase();
  if (/\.(mp4|webm|mov|m4v)$/.test(clean)) return "video";
  if (/\.(png|jpe?g|webp|gif|avif)$/.test(clean)) return "image";
  if (/\.(mp3|wav|ogg|m4a|aac)$/.test(clean)) return "audio";
  return "other";
}

export default function SkillWallCard({ card }: { card: SkillPreviewCard }) {
  const [broken, setBroken] = useState(false);
  const first = card.previews[0];
  const kind = first ? previewKind(first) : "none";
  const preview = first && !broken ? first : null;

  return (
    <Card withBorder padding={0} radius="md" style={{ overflow: "hidden" }} data-testid="skill-wall-card">
      <Box
        style={{ position: "relative", background: "var(--mantine-color-dark-7)", minHeight: 120 }}
      >
        {preview && kind === "video" ? (
          <video
            src={preview}
            controls
            preload="metadata"
            playsInline
            style={{ width: "100%", display: "block" }}
            data-testid="skill-preview-video"
          />
        ) : preview && kind === "image" ? (
          <img
            src={preview}
            alt={card.display_name}
            onError={() => setBroken(true)}
            style={{ width: "100%", display: "block" }}
            data-testid="skill-preview-image"
          />
        ) : preview && kind === "audio" ? (
          <Box p="sm">
            <audio src={preview} controls style={{ width: "100%" }} data-testid="skill-preview-audio" />
          </Box>
        ) : (
          <Group justify="center" align="center" style={{ minHeight: 120 }} p="md">
            {/* Preview media lands with the demo runs (#799); the empty
                state is a typed placeholder, never a gray box. */}
            <Text size="xs" c="dimmed" ta="center" data-testid="skill-preview-empty">
              Preview coming with the demo runs
            </Text>
          </Group>
        )}
        <Badge
          size="xs"
          variant="light"
          color={card.tier === "official" ? "violet" : "teal"}
          style={{ position: "absolute", top: 8, left: 8 }}
          data-testid="skill-tier-badge"
        >
          {card.tier}
        </Badge>
      </Box>
      <Box p="sm">
        <Text size="sm" fw={600} lineClamp={1} title={card.display_name} data-testid="skill-card-name">
          {card.display_name}
        </Text>
        <Group justify="space-between" mt={4}>
          <Text size="xs" c="dimmed" data-testid="skill-fork-count">
            {card.fork_count} {card.fork_count === 1 ? "fork" : "forks"}
          </Text>
          <Button
            size="compact-xs"
            variant="light"
            color="teal"
            onClick={() => navigate({ module: "workbench" })}
            data-testid="skill-fork-button"
          >
            Fork
          </Button>
        </Group>
      </Box>
    </Card>
  );
}
