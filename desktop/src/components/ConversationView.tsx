import {
  ActionIcon,
  Anchor,
  Badge,
  Box,
  Card,
  Code,
  Group,
  Loader,
  Paper,
  ScrollArea,
  Stack,
  Text,
} from "@mantine/core";
import { IconArrowDown } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import type { SessionDetail, TranscriptMessage } from "../api";
import { describeActivity, formatElapsed, type StreamState } from "../stream";
import Markdown from "./Markdown";

function roleLabel(role: string): string {
  if (role === "user") return "You";
  if (role === "assistant") return "Skilyst";
  return role;
}

function Bubble({ message }: { message: TranscriptMessage }) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  if (isTool) {
    // Tool calls are data, not prose: the card keeps the raw JSON, unrendered.
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
      {isUser ? (
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
          {message.content}
        </Text>
      ) : (
        <Markdown content={message.content} />
      )}
    </Paper>
  );
}

/** Seconds since the run started, for the status line. Resets when the run ends. */
function useBusySeconds(busy: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!busy) {
      setSeconds(0);
      return;
    }
    const started = Date.now();
    setSeconds(0);
    const timer = window.setInterval(() => setSeconds((Date.now() - started) / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  return seconds;
}

export default function ConversationView({
  detail,
  stream,
  notes,
  busy,
}: {
  detail: SessionDetail | null;
  stream: StreamState;
  notes: string[];
  busy: boolean;
}) {
  const messages = detail?.messages ?? [];
  const artifacts = detail?.artifacts ?? [];
  const elapsed = useBusySeconds(busy);

  const viewport = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  // "Stuck" = the view is at the bottom, so new output should follow it. Scrolling
  // up un-sticks it: an answer arriving must never yank the user out of what they
  // were reading.
  const stuckRef = useRef(true);
  const [stuck, setStuck] = useState(true);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const onScroll = () => {
      const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
      const near = distance <= 80;
      stuckRef.current = near;
      setStuck(near);
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => element.removeEventListener("scroll", onScroll);
  }, [detail?.session_id]);

  // Opening a session starts at its end: the latest answer is what the user came for.
  useEffect(() => {
    stuckRef.current = true;
    setStuck(true);
    bottom.current?.scrollIntoView({ block: "end" });
  }, [detail?.session_id]);

  const streamed = stream.blocks.length;
  const current = stream.current.length;
  useEffect(() => {
    if (!stuckRef.current) return;
    // Instant while tokens are arriving (a smooth animation per token is a slideshow),
    // smooth when a whole message lands.
    bottom.current?.scrollIntoView({ block: "end", behavior: current ? "auto" : "smooth" });
  }, [messages.length, streamed, current, notes.length, busy]);

  const jumpToLatest = () => {
    stuckRef.current = true;
    setStuck(true);
    bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  };

  const activity = describeActivity(stream, notes);
  const showStream = busy || stream.blocks.length > 0 || stream.current !== "";

  return (
    <Box style={{ flex: 1, minHeight: 0, position: "relative" }}>
      <ScrollArea style={{ height: "100%" }} p="md" viewportRef={viewport} data-testid="transcript">
        <Stack gap="sm">
          {detail === null && !busy ? (
            <Text c="dimmed" size="sm">
              No session open. Ask something to start one.
            </Text>
          ) : null}
          {messages.map((message, index) => (
            // An assistant turn that only called tools carries no text; the tool cards
            // below it are its content.
            message.role === "assistant" && !message.content?.trim() ? null : (
              <Bubble key={`${message.seq ?? index}-${message.role}`} message={message} />
            )
          ))}
          {stream.blocks.map((block, index) => (
            <Bubble key={`stream-${index}`} message={{ role: "assistant", content: block }} />
          ))}
          {showStream ? (
            <Paper withBorder p="sm" maw="85%" mr="auto" data-testid="streaming-answer">
              <Group gap="xs" mb={stream.current ? 6 : 0}>
                <Text size="xs" c="dimmed">
                  Skilyst
                </Text>
                <Badge size="xs" variant="light" color={busy ? "blue" : "gray"} data-testid="stream-status">
                  {activity}
                </Badge>
                {busy ? (
                  <Text size="xs" c="dimmed" data-testid="stream-elapsed">
                    {formatElapsed(elapsed)}
                  </Text>
                ) : null}
                {busy ? <Loader size={14} type="dots" data-testid="stream-spinner" /> : null}
              </Group>
              {stream.current ? <Markdown content={stream.current} streaming /> : null}
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
          <div ref={bottom} />
        </Stack>
      </ScrollArea>
      {!stuck && (messages.length > 0 || showStream) ? (
        <ActionIcon
          variant="filled"
          color="blue"
          radius="xl"
          size="md"
          aria-label="jump to latest"
          data-testid="jump-to-latest"
          onClick={jumpToLatest}
          style={{ position: "absolute", bottom: 14, right: 20, zIndex: 3 }}
        >
          <IconArrowDown size={16} />
        </ActionIcon>
      ) : null}
    </Box>
  );
}
