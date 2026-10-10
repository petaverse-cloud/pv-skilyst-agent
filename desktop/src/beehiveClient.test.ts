import { describe, expect, it, vi, beforeEach } from "vitest";

// The api() channel is stubbed — normalizeSkillCard is a pure function over
// the raw row; the contract under test is what the card renderer receives.
vi.mock("./api", () => ({
  api: vi.fn(),
}));

import { listSkills } from "./beehiveClient";
import { api } from "./api";

const mockApi = vi.mocked(api);

beforeEach(() => {
  mockApi.mockReset();
});

describe("normalizeSkillCard (via listSkills)", () => {
  it("normalizes core row shapes and keeps unpinned raw fields", async () => {
    mockApi.mockResolvedValue({
      skills: [
        {
          skill_id: "vid/15s",
          display_name: "15s Video Skill",
          version: "1.2",
          author: { name: "Wesley", verified: true },
          fork_depth: 2,
          usage_count: 12,
          reference_workflow: { id: "wf-1", cover_url: "https://x/c.jpg" },
          pricing: { price_usd: 0 },
          extra_future_field: "kept for the card to grow into",
        },
      ],
    });

    const [card] = await listSkills(1, 0);

    expect(card.skill_id).toBe("vid/15s");
    expect(card.display_name).toBe("15s Video Skill");
    expect(card.author_verified).toBe(true);
    expect(card.fork_depth).toBe(2);
    expect(card.reference_workflow?.cover_url).toBe("https://x/c.jpg");
    expect(card.pricing).toEqual({ price_usd: 0, free: true });
    // The raw passthrough carries fields the spec has not pinned yet.
    expect(card.extra_future_field).toBe("kept for the card to grow into");
  });

  it("coerces hostile raw values — normalized output wins over the raw row", async () => {
    // The spread order regression: when ...row lands AFTER the normalized
    // fields, a raw pricing.price_usd string (or a non-string display_name)
    // punches through to the card renderer. The normalizer's whole job is
    // that the card layer never sees a raw-typed field.
    mockApi.mockResolvedValue([
      {
        skill_id: "x",
        display_name: 42 as unknown as string, // number, not string
        version: 3 as unknown as string,
        fork_depth: "4" as unknown as number, // string, not number
        usage_count: "many" as unknown as number,
        author: { name: 7 as unknown as string, verified: 1 as unknown as boolean },
        pricing: { price_usd: "12.5" as unknown as number },
        reference_workflow: { id: 5 as unknown as string, content_hash: null as unknown as string },
      },
    ]);

    const [card] = await listSkills(1, 0);

    expect(card.display_name).toBe("42"); // String() coerced, raw number does NOT survive
    expect(card.version).toBe("3");
    expect(card.fork_depth).toBe(4); // Number() coerced
    expect(card.usage_count).toBeNaN(); // Number("many") — degraded, not raw
    expect(card.author_verified).toBe(true); // Boolean(1)
    expect(card.pricing?.price_usd).toBe(12.5); // Number("12.5") — coerced, not the raw string
    expect(card.pricing?.free).toBe(false);
    expect(card.reference_workflow?.id).toBe("5");
    expect(card.reference_workflow?.content_hash).toBeUndefined(); // null → undefined, no leak
  });

  it("tolerates snake/camel drift and missing optionals — degrade, never break", async () => {
    mockApi.mockResolvedValue([
      { id: "y", name: "Camel Row", referenceWorkflow: { workflow_id: "wf-9" } },
    ]);

    const [card] = await listSkills(1, 0);

    expect(card.skill_id).toBe("y"); // id fallback
    expect(card.display_name).toBe("Camel Row"); // name fallback
    expect(card.version).toBe(""); // absent → empty
    expect(card.author_name).toBe("");
    expect(card.author_verified).toBe(false);
    expect(card.fork_depth).toBe(0);
    expect(card.pricing).toBeNull();
    expect(card.reference_workflow?.id).toBe("wf-9"); // camelCase + workflow_id fallback
  });
});
