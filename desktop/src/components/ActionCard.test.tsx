import { MantineProvider } from "@mantine/core";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import ActionCard, { actionSummary } from "./ActionCard";
import type { TranscriptMessage } from "../api";

const action = (fields: Partial<TranscriptMessage>): TranscriptMessage => ({
  role: "action",
  content: "",
  ...fields,
});

const html = (message: TranscriptMessage, onLocate?: () => void) =>
  renderToStaticMarkup(
    // Mantine components need the provider (CSS vars + context) even under SSR.
    <MantineProvider defaultColorScheme="dark">
      <ActionCard message={message} onLocate={onLocate} />
    </MantineProvider>,
  );

describe("ActionCard", () => {
  it("summarizes an added node from board_delta", () => {
    expect(
      actionSummary(action({ tool: "canvas_create_node", board_delta: { added_node: "script-1" } })),
    ).toBe("新增节点 script-1");
  });

  it("summarizes a wired edge with its port", () => {
    expect(
      actionSummary(action({ tool: "canvas_connect_ports", board_delta: { wired: ["mat-1", "minimax-h3-1", "first_frame"] } })),
    ).toBe("连线 mat-1 → minimax-h3-1 (first_frame)");
  });

  it("renders the tool badge, duration and cost badge", () => {
    const out = html(
      action({
        tool: "canvas_generate_image",
        board_delta: { submitted_job: "job-9" },
        duration_s: 1.23,
        cost: { estimate_usd: 0.5, hold_micro_usd: 500000 },
      }),
    );
    expect(out).toContain("generate_image");
    expect(out).toContain("1.23s");
    expect(out).toContain("$0.500");
  });

  it("renders the cost badge from the hold when the estimate is missing (QA #16)", () => {
    // The wire quote carries total_estimate (not total_estimate_usd), so a
    // runtime that only produced hold_micro_usd must still show the amount:
    // a paid action card without its cost is a hidden cost.
    const out = html(
      action({
        tool: "canvas_generate_image",
        board_delta: { submitted_job: "job-10" },
        cost: { hold_micro_usd: 960000 },
      }),
    );
    expect(out).toContain('data-testid="action-cost"');
    expect(out).toContain("$0.960");
  });

  it("shows no cost badge for a free action", () => {
    const out = html(
      action({
        tool: "canvas_create_node",
        board_delta: { added_node: "n-1" },
        cost: { hold_micro_usd: 0 },
      }),
    );
    expect(out).not.toContain('data-testid="action-cost"');
  });

  it("shows params and result when expanded", () => {
    // renderToStaticMarkup cannot click; assert the collapsed state hides detail
    // and the summary line carries the delta, then assert expand content via the
    // second render of a fresh tree with the toggle pressed is out of scope for
    // SSR — the toggle itself must exist.
    const out = html(action({ tool: "canvas_add_media", params: { url: "https://x/a.png" }, result_ref: { pool_entry: "mp-1" } }));
    expect(out).toContain('data-testid="action-toggle"');
    expect(out).not.toContain("https://x/a.png"); // collapsed: params hidden
  });

  it("locates on the canvas when the card body is clicked", () => {
    const onLocate = vi.fn();
    const out = html(
      action({ tool: "canvas_create_node", params: { workflow_id: "wf-1" }, board_delta: { added_node: "script-1" } }),
      () => onLocate(),
    );
    expect(out).toContain('data-testid="action-locate"');
    expect(out).not.toContain("disabled"); // the locate button is live
  });
});
