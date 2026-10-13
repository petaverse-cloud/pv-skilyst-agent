/**
 * #91: a creation card on the desktop Home — the web#386 shared card
 * contract, MVP subset. The contract lives on web#386; this surface
 * implements it, it does not fork it:
 *
 *   main content = the play target (click plays the creation)
 *   action row    = Skills Preview · fork · like (+ count) · comments
 *                   (structural placeholder this cycle)
 *
 * The media surface is honest about what it holds: a video card shows an
 * inline player (poster = thumbnail, click = play); an image card IS the
 * content; anything else degrades to a typed placeholder, never a silent
 * gray box.
 */
import { ActionIcon, Badge, Box, Card, Group, Image, Stack, Text, Tooltip } from "@mantine/core";
import { IconCode, IconCopy, IconHeart, IconMessage2 } from "@tabler/icons-react";
import { useState } from "react";
import { navigate } from "../router";
import { setCreationFavorite, type CreationWork } from "../beehiveClient";

export function CreationCard({ work }: { work: CreationWork }) {
  const [liked, setLiked] = useState(false);
  const [likeBusy, setLikeBusy] = useState(false);
  const [likeError, setLikeError] = useState<string | null>(null);

  // The play target: video cards play inline (paused, poster-first, no
  // autoplay — the click IS the intent); images are their own content.
  const isVideo = work.asset_type === "video" || work.mime_type.startsWith("video/");
  const isImage = work.asset_type === "image" || work.mime_type.startsWith("image/");
  const poster = work.thumbnail_url || work.url;

  const like = async () => {
    setLikeError(null);
    setLikeBusy(true);
    try {
      await setCreationFavorite(work.id, !liked);
      setLiked((state) => !state);
    } catch (error) {
      // Loud, in-card — a like that silently fails is worse than no like.
      setLikeError(String((error as Error)?.message ?? error));
    } finally {
      setLikeBusy(false);
    }
  };

  return (
    <Card withBorder padding={0} radius="md" style={{ overflow: "hidden" }} data-testid="creation-card">
      <Box style={{ position: "relative", background: "var(--mantine-color-dark-7)" }} h={150}>
        {isVideo ? (
          <video
            src={work.url}
            poster={poster}
            controls
            preload="metadata"
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
            data-testid="creation-media-video"
          />
        ) : isImage ? (
          <Image src={work.url} alt={work.name} h={150} fit="cover" data-testid="creation-media-image" />
        ) : (
          <Group justify="center" align="center" h={150}>
            <Text size="sm" c="dimmed" data-testid="creation-media-unknown">
              {work.mime_type || work.asset_type || "unsupported media"}
            </Text>
          </Group>
        )}
        {work.duration ? (
          <Badge
            size="xs"
            color="dark"
            variant="filled"
            style={{ position: "absolute", bottom: 6, right: 6 }}
            data-testid="creation-duration"
          >
            {Math.round(work.duration)}s
          </Badge>
        ) : null}
      </Box>

      <Stack gap={6} p="sm">
        <Text size="sm" fw={600} lineClamp={1} title={work.name}>
          {work.name}
        </Text>
        {/* The action row — the contract's four affordances. */}
        <Group gap={4} justify="space-between">
          <Group gap={2}>
            <Tooltip label="How this was made — the workflow board">
              <ActionIcon
                size="sm"
                variant="subtle"
                aria-label="skills preview"
                data-testid="creation-skills-preview"
                onClick={() => navigate({ module: "workbench" })}
              >
                <IconCode size={15} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Fork the skill that made this">
              <ActionIcon
                size="sm"
                variant="subtle"
                aria-label="fork"
                data-testid="creation-fork"
                onClick={() => navigate({ module: "workbench" })}
              >
                <IconCopy size={15} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label={liked ? "Un-like" : "Like"}>
              <ActionIcon
                size="sm"
                variant={liked ? "filled" : "subtle"}
                color={liked ? "red" : undefined}
                aria-label="like"
                data-testid="creation-like"
                loading={likeBusy}
                onClick={() => void like()}
              >
                <IconHeart size={15} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Comments (coming soon)">
              <ActionIcon size="sm" variant="subtle" aria-label="comments" data-testid="creation-comments" disabled>
                <IconMessage2 size={15} />
              </ActionIcon>
            </Tooltip>
          </Group>
          {likeError ? (
            <Text size="xs" c="red" lineClamp={1} title={likeError} data-testid="creation-like-error">
              {likeError}
            </Text>
          ) : null}
        </Group>
      </Stack>
    </Card>
  );
}
