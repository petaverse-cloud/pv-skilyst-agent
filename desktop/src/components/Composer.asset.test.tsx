// @vitest-environment happy-dom
// #87 review round-2 (mutation round 4): the visual blocks the reviewer's
// matrix showed unpinned — the pending-asset chip, the upload-error row,
// and the withAsset message contract in WorkbenchPage.send — are now
// driven through REAL renders with real effects (happy-dom per-file env,
// the #76 pattern). The literal-object shape test is gone.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act, createElement } from "react";
import { MantineProvider } from "@mantine/core";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Mantine's Autosize textarea touches document.fonts and a ResizeObserver;
// happy-dom ships neither. No-op polyfills keep the mount path real
// (the effects run; they just measure nothing) without faking assertions.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
const doc = document as unknown as { fonts?: unknown };
if (!doc.fonts) {
  doc.fonts = new EventTarget();
}

import Composer, { withAssetContext } from "./Composer";
import type { PendingAsset } from "./Composer";
import { api } from "../api";

// Spy on the live binding Composer actually calls. vi.spyOn needs the
// module namespace object, not the destructured function.
import * as apiModule from "../api";
const apiSpy = vi.spyOn(apiModule, "api");

const skills = [
  { skill_id: "skilyst/canvas-ops", title: "canvas-ops", version: "1.0.0", degraded: false },
];

function mount(onAssetUploaded?: (a: PendingAsset) => void) {
  // Previous tests leave their hosts in the shared body — a naked
  // querySelector would grab a dead instance's input.
  document.body.innerHTML = "";
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  act(() => {
    root.render(
      createElement(
        MantineProvider,
        null,
        createElement(Composer, {
          onSend: () => undefined,
          busy: false,
          dryRun: true,
          onDryRunChange: () => undefined,
          serverDryRun: false,
          model: "m",
          skills,
          skill: "",
          onSkillChange: () => undefined,
          onAssetUploaded,
        }),
      ),
    );
  });
  return host;
}
beforeEach(() => {
  apiSpy.mockReset();
});

async function pickFile(file: File) {
  const input = document.querySelector<HTMLInputElement>('input[data-testid="asset-file-input"]');
  expect(input).toBeTruthy();
  // happy-dom's input.files is a prototype getter (own-property define
  // throws "Cannot redefine property"); shadow it on the instance via the
  // prototype chain escape hatch: delete then define.
  const proto = Object.getPrototypeOf(input!) as { files?: unknown };
  const desc = Object.getOwnPropertyDescriptor(proto, "files");
  if (desc) {
    Object.defineProperty(proto, "files", { ...desc, set(v: unknown) {
      Object.defineProperty(this, "files", { value: v, configurable: true, writable: true });
    }});
  }
  try {
    // File[] shaped as FileList: happy-dom's input reads files[0] only.
    (input as { files: unknown }).files = [file];
  } finally {
    if (desc) Object.defineProperty(proto, "files", desc);
  }
  await act(async () => {
    input!.dispatchEvent(new Event("change", { bubbles: true }));
    // FileReader's onload fires on a macrotask in happy-dom; flush it and
    // the upload promise chain before act returns.
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

describe("Composer asset upload (#87 r2)", () => {
  it("a successful upload renders the pending-asset chip and notifies the host", async () => {
    const asset: PendingAsset = { id: "ast_9", name: "shot.png", url: "https://cdn/x.png" };
    apiSpy.mockResolvedValueOnce(asset);
    const seen: PendingAsset[] = [];
    const host = mount((a) => seen.push(a));
    await pickFile(new File([new Uint8Array([1, 2, 3])], "shot.png", { type: "image/png" }));
    // the upload went through the runtime bridge with the file's identity
    expect(apiSpy).toHaveBeenCalledWith(
      "/beehive/assets/upload",
      expect.objectContaining({
        method: "POST",
        body: expect.objectContaining({ filename: "shot.png", mime_type: "image/png" }),
      }),
    );
    const chip = host.querySelector('[data-testid="pending-asset"]');
    expect(chip).toBeTruthy();
    expect(chip!.textContent).toContain("shot.png");
    expect(seen.map((a) => a.id)).toEqual(["ast_9"]);
  });

  it("a failed upload renders the loud red error row (G4), no chip", async () => {
    apiSpy.mockRejectedValueOnce(new Error("POST /api/v1/assets failed: HTTP 403 scope"));
    const host = mount();
    await pickFile(new File([new Uint8Array([1])], "bad.png", { type: "image/png" }));
    const errorRow = host.querySelector('[data-testid="upload-error"]');
    expect(errorRow).toBeTruthy();
    expect(errorRow!.textContent).toContain("403");
    expect(host.querySelector('[data-testid="pending-asset"]')).toBeNull();
  });

  it("an over-limit file is refused before any upload call (the 50 MiB gate)", async () => {
    const host = mount();
    // 51 MiB of declared size — the network call must not even fire
    const big = new File([new Uint8Array([0])], "huge.mov", { type: "video/quicktime" });
    Object.defineProperty(big, "size", { value: 51 * 1024 * 1024 });
    await pickFile(big);
    expect(apiSpy).not.toHaveBeenCalled();
    const errorRow = host.querySelector('[data-testid="upload-error"]');
    expect(errorRow).toBeTruthy();
    expect(errorRow!.textContent).toContain("50 MiB");
  });

  it("removing the chip clears the pending attachment (the X affordance)", async () => {
    apiSpy.mockResolvedValueOnce({ id: "a", name: "n.png", url: "u" });
    const host = mount();
    await pickFile(new File([new Uint8Array([1])], "n.png", { type: "image/png" }));
    expect(host.querySelector('[data-testid="pending-asset"]')).toBeTruthy();
    const x = host.querySelector('[aria-label="remove attachment"]');
    expect(x).toBeTruthy();
    await act(async () => {
      x!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(host.querySelector('[data-testid="pending-asset"]')).toBeNull();
  });

  it("the attach affordance stays in the markup (accept scoping + busy disable)", () => {
    const host = mount();
    expect(host.querySelector('[data-testid="asset-file-input"]')).toBeTruthy();
    expect(
      host.querySelector<HTMLInputElement>('input[data-testid="asset-file-input"]')!.accept,
    ).toBe("image/*,video/*,audio/*");
    const attach = host.querySelector('[data-testid="attach-button"]');
    expect(attach).toBeTruthy();
    expect(attach!.hasAttribute("disabled")).toBe(false);
  });
});

// The WorkbenchPage side: the uploaded asset rides the next message as
// explicit markdown context. withAssetContext is the SHARED source —
// WorkbenchPage.send composes the message with it, so this pins the
// production expression, not a test-side copy (the reviewer's tautology
// point, taken).
describe("the withAsset message contract (#87 r2)", () => {
  it("an attached asset rides the message as visible markdown context", () => {
    const text = "turn this into a 3-shot sequence";
    const asset: PendingAsset = { id: "ast_1", name: "ref.png", url: "https://cdn/r.png" };
    expect(withAssetContext(text, asset)).toBe(
      "turn this into a 3-shot sequence\n\n[attached asset: ref.png](https://cdn/r.png)",
    );
  });

  it("no attachment leaves the message untouched", () => {
    expect(withAssetContext("plain message", null)).toBe("plain message");
  });
});
