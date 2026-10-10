import { renderToString } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it, vi } from "vitest";

// The beehiveClient channel is stubbed — what these tests pin is the
// SkillCard VISUAL CONTRACT (review r2: mutation-testing found the first
// version's assertions vacuous — deleting the price badge / verified mark /
// fork-depth line left the suite green, and `await expect(mock).toHaveBeenCalled`
// without call parens asserted nothing). Each test renders the card directly
// and asserts markup that only exists when the visual block does: delete the
// block, the test fails. HomePage-level effect wiring (the 403 degrade) stays
// pinned by the runtime drills — SSR cannot run effects (#49 lesson).
vi.mock("../beehiveClient", () => ({
  listWorkflows: vi.fn(),
  listSkills: vi.fn(),
}));

import { SkillCard } from "./HomePage";
import type { SkillCardData } from "../beehiveClient";

function card(sk: Partial<SkillCardData> & Pick<SkillCardData, "skill_id" | "display_name">) {
  const full: SkillCardData = {
    version: "",
    author_name: "",
    author_verified: false,
    fork_depth: 0,
    usage_count: 0,
    reference_workflow: null,
    pricing: null,
    ...sk,
  };
  return renderToString(
    <MantineProvider>
      <SkillCard sk={full} />
    </MantineProvider>,
  );
}

const base = { skill_id: "skilyst/vid", display_name: "15s Video" } as const;

// React SSR inserts <!-- --> comment separators between adjacent text
// nodes ("by <!-- -->Wesley"); assertions anchor on single-node strings
// (badge labels, testids) or regex tolerant of the separators.
const node = (text: string) =>
  // "2 upstreams" renders as text nodes "2" + " upstream" + "s" with
  // <!-- --> between: tolerate separators AND whitespace per word.
  new RegExp(text.split(/\s+/).map((w) => w).join("(\\s|<!-- -->)*"));

describe("SkillCard visual contract (#54)", () => {
  it("renders the price corner badge — Free for zero, $N otherwise, none when undeclared", () => {
    expect(card({ ...base, pricing: { price_usd: 0, free: true } })).toContain("Free");
    const paid = card({ ...base, pricing: { price_usd: 12.5, free: false } });
    expect(paid).toContain("$12.5");
    expect(paid).not.toContain("Free");
    // No pricing declared -> no badge at all.
    expect(card(base)).not.toContain("Free");
  });

  it("renders the author line with the verified mark only when verified", () => {
    const html = card({ ...base, author_name: "Wesley", author_verified: true });
    expect(node("by Wesley").test(html)).toBe(true);
    expect(html).toContain("author-verified");
    const unverified = card({ ...base, author_name: "Wesley" });
    expect(node("by Wesley").test(unverified)).toBe(true);
    expect(unverified).not.toContain("author-verified");
  });

  it("renders the lineage hint — depth for forks, original otherwise", () => {
    // "2 upstreams" renders as "2" + " upstream" + "s" (separator comments
    // between) — the regex tolerates the seams at the word level.
    expect(/2(\s|<!-- -->)*upstream(\s|<!-- -->)*s/.test(card({ ...base, fork_depth: 2 }))).toBe(true);
    expect(/1(\s|<!-- -->)*upstream/.test(card({ ...base, fork_depth: 1 }))).toBe(true);
    // depth 0 is the original: no upstream text.
    expect(card(base)).not.toMatch(/upstream/);
    expect(/original/.test(card(base))).toBe(true);
  });

  it("renders usage runs only when the registry reports them", () => {
    expect(node("12 runs").test(card({ ...base, usage_count: 12 }))).toBe(true);
    expect(card(base)).not.toMatch(/\bruns\b/);
  });

  it("renders the identity block — name and version", () => {
    const html = card({ ...base, version: "1.2" });
    expect(html).toContain("15s Video");
    // "· v" + "1.2" split by the SSR separator comment.
    expect(/v(<!-- -->)?1\.2/.test(html)).toBe(true);
  });

  it("emits the skill-card testid surface", () => {
    expect(card(base)).toContain("skill-card");
  });
});
