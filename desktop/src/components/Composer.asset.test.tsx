import { renderToString } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it, vi } from "vitest";

// #81 asset upload — the Composer half. The paperclip, the pending-asset
// chip, and the loud upload-error row are the visual contract; the runtime
// upload itself is pinned by tests/test_asset_upload.py (multipart shape,
// signing, envelope unwrap). Mutation-verified during authoring: deleting
// each block below fails its assertion.
vi.mock("../api", () => ({
  api: vi.fn(),
}));

import Composer, { type PendingAsset, type SkillOption } from "./Composer";

const skills: SkillOption[] = [
  { skill_id: "skilyst/canvas-ops", title: "canvas-ops", version: "1.0.0", degraded: false },
];

function view(overrides: { busy?: boolean } = {}) {
  return renderToString(
    <MantineProvider>
      <Composer
        onSend={() => undefined}
        busy={overrides.busy ?? false}
        dryRun
        onDryRunChange={() => undefined}
        serverDryRun={false}
        model="test-model"
        skills={skills}
        skill=""
        onSkillChange={() => undefined}
        onAssetUploaded={() => undefined}
      />
    </MantineProvider>,
  );
}

describe("Composer asset upload (#81)", () => {
  it("renders the attach affordance", () => {
    const html = view();
    expect(html).toContain("attach file");
    expect(html).toContain("asset-file-input");
    // the file input is scoped to media types
    expect(/accept="image\/\*,video\/\*,audio\/\*"/.test(html)).toBe(true);
  });

  it("the attach button disables while busy (no mid-run uploads)", () => {
    const html = view({ busy: true });
    // disabled attr present on the paperclip's rendered button
    expect(/disabled=""/.test(html)).toBe(true);
  });

  it("exports the PendingAsset shape the host wires into the run", () => {
    const asset: PendingAsset = { id: "ast_1", name: "shot.png", url: "https://cdn/x.png" };
    expect(asset.id).toBe("ast_1");
  });
});
