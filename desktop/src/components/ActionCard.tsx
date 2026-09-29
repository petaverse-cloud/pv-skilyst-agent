/**
 * ActionCard (A3 S3, R3 message model) — renders a messages.jsonl `action`
 * row as an expandable card: one-line human summary derived from
 * board_delta, tool badge, duration/cost, full params/result on expand.
 * Clicking the card body (not the expand toggle) locates it on the canvas.
 */
import { Badge, Box, Card, Code, Group, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { useState } from "react";
import type { TranscriptMessage } from "../api";

/** Tool-family badge: canvas_* tools are board moves, everything else platform. */
function toolBadge(tool: string): { color: string; label: string } {
  if (tool.startsWith("canvas_")) {
    return { color: "teal", label: tool.replace("canvas_", "") };
  }
  return { color: "grape", label: tool };
}

/** The one-line human summary: what moved on the board. */
export function actionSummary(message: TranscriptMessage): string {
  const delta = (message.board_delta ?? {}) as Record<string, unknown>;
  if (typeof delta.added_node === "string") {
    return `新增节点 ${delta.added_node}`;
  }
  if (Array.isArray(delta.wired)) {
    const [from, to, port] = delta.wired as string[];
    return `连线 ${from} → ${to}${port ? ` (${port})` : ""}`;
  }
  if (typeof delta.updated_config === "string") {
    return `更新配置 ${delta.updated_config}`;
  }
  if (typeof delta.added_pool_entry === "string") {
    return `媒体池 +${delta.added_pool_entry}`;
  }
  if (typeof delta.submitted_job === "string") {
    return `提交任务 ${delta.submitted_job}`;
  }
  if (message.tool) {
    return `${message.tool} 完成`;
  }
  return "action";
}

export default function ActionCard({
  message,
  onLocate,
}: {
  message: TranscriptMessage;
  onLocate?: (message: TranscriptMessage) => void;
}) {
  const [open, setOpen] = useState(false);
  const badge = toolBadge(message.tool ?? "");
  const cost = message.cost as
    | { estimate_usd?: number; hold_micro_usd?: number }
    | undefined;
  // QA #16: estimate_usd is derived from the quote's estimate (either
  // spelling), falling back to the hold (M1: estimate == hold upper bound) --
  // a paid action must never lose its cost badge to a missing estimate.
  const costUsd = cost?.estimate_usd ?? (cost?.hold_micro_usd && cost.hold_micro_usd > 0
    ? cost.hold_micro_usd / 1_000_000
    : undefined);

  return (
    <Card withBorder padding="xs" data-testid="action-card" style={{ maxWidth: "85%" }}>
      <Group gap="xs" wrap="nowrap" align="flex-start">
        <UnstyledButton
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? "收起详情" : "展开详情"}
          data-testid="action-toggle"
          style={{ display: "flex", alignItems: "center" }}
        >
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        </UnstyledButton>
        {/* The card body locates on the canvas; the chevron toggles detail. */}
        <UnstyledButton
          onClick={() => onLocate?.(message)}
          data-testid="action-locate"
          style={{ flex: 1, textAlign: "left" }}
          disabled={!onLocate}
        >
          <Group gap="xs">
            <Badge size="xs" variant="light" color={badge.color}>
              {badge.label}
            </Badge>
            <Text size="sm">{actionSummary(message)}</Text>
          </Group>
        </UnstyledButton>
        {typeof message.duration_s === "number" ? (
          <Text size="xs" c="dimmed">
            {message.duration_s}s
          </Text>
        ) : null}
        {costUsd !== undefined ? (
          <Badge size="xs" variant="light" color="yellow" data-testid="action-cost">
            ≈${costUsd.toFixed(3)}
          </Badge>
        ) : null}
      </Group>
      {open ? (
        <Box mt="xs">
          <Text size="xs" c="dimmed" mb={2}>
            params
          </Text>
          <Code block style={{ maxHeight: 180, overflow: "auto" }}>
            {JSON.stringify(message.params ?? {}, null, 2)}
          </Code>
          <Text size="xs" c="dimmed" mb={2} mt="xs">
            result
          </Text>
          <Code block style={{ maxHeight: 180, overflow: "auto" }}>
            {JSON.stringify(message.result_ref ?? {}, null, 2)}
          </Code>
        </Box>
      ) : null}
    </Card>
  );
}
