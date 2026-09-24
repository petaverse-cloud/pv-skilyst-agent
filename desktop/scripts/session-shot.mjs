/**
 * Open one stored session in the shell and photograph what it renders.
 *
 * Used for the markdown before/after evidence: point it at the same dev server and the
 * same session id twice (once from `main`, once from the branch) and the two PNGs are
 * comparable. It also prints what the DOM actually contains -- element counts, not
 * impressions -- so "markdown renders" is a measurement rather than a claim.
 *
 *   PLAYWRIGHT_CORE_ROOT=~/pv-pawly-web node scripts/session-shot.mjs \
 *     http://localhost:1420 evidence/feedback-round1/after-table.png 20260924-152836
 *
 * Needs playwright-core from any project that has it (see desktop/README.md).
 */
import { createRequire } from "node:module";

const [url, out, query = ""] = process.argv.slice(2);
if (!url || !out) {
  console.error("usage: node scripts/session-shot.mjs <dev-url> <out.png> [session-title-substring]");
  process.exit(2);
}

const root = process.env.PLAYWRIGHT_CORE_ROOT;
const require = createRequire(root ? `${root.replace(/\/$/, "")}/` : import.meta.url);
const { chromium } = require("playwright-core");

const executablePath =
  process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch({ executablePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const problems = [];
page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
page.on("console", (entry) => {
  if (entry.type() === "error") problems.push(`console: ${entry.text()}`);
});

await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForSelector("textarea[data-testid='composer']", { timeout: 30000 });
await page.waitForSelector(".mantine-NavLink-root", { timeout: 30000 });

const rows = page.locator(".mantine-NavLink-root");
const count = await rows.count();
let index = 0;
if (query) {
  index = -1;
  for (let i = 0; i < count; i += 1) {
    const text = (await rows.nth(i).innerText()).replace(/\s+/g, " ");
    if (text.includes(query)) {
      index = i;
      break;
    }
  }
  if (index < 0) {
    console.error(`FAIL: no session row matching ${JSON.stringify(query)} (${count} rows)`);
    await browser.close();
    process.exit(1);
  }
}
console.log(`sessions in the sidebar: ${count}; opening row ${index}`);
await rows.nth(index).click();
await page.waitForSelector("[data-testid='message-assistant']", { timeout: 30000 });
await page.waitForTimeout(400); // let the auto-scroll settle

const probe = await page.evaluate(() => {
  // The transcript marker is new in this branch; fall back so the same script can
  // photograph the old UI for the before/after pair.
  const scope = document.querySelector("[data-testid='transcript']") ?? document.body;
  const viewport =
    scope.querySelector(".mantine-ScrollArea-viewport") ??
    document.querySelector(".mantine-ScrollArea-viewport");
  const assistant = Array.from(document.querySelectorAll("[data-testid='message-assistant']"));
  const rendered = assistant.map((node) => node.innerText);
  return {
    assistantBubbles: assistant.length,
    markdownRoots: scope.querySelectorAll(".md").length,
    headings: scope.querySelectorAll(".md h1, .md h2, .md h3, .md h4").length,
    lists: scope.querySelectorAll(".md ul, .md ol").length,
    listItems: scope.querySelectorAll(".md li").length,
    tables: scope.querySelectorAll(".md table").length,
    tableCells: scope.querySelectorAll(".md td").length,
    codeBlocks: scope.querySelectorAll(".md .md-pre").length,
    highlighted: scope.querySelectorAll(".md .md-pre .token").length,
    inlineCode: scope.querySelectorAll(".md .md-inline-code").length,
    bold: scope.querySelectorAll(".md strong").length,
    literalFences: rendered.filter((text) => text.includes("```")).length,
    scrolledToBottom: viewport
      ? viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 4
      : null,
    transcriptText: rendered.join("\n").slice(0, 600),
  };
});

console.log(JSON.stringify(probe, null, 2));
await page.screenshot({ path: out, fullPage: true });
console.log(`screenshot: ${out}`);
console.log(`page problems: ${problems.length ? problems.join(" | ") : "none"}`);
await browser.close();

const ok = probe.assistantBubbles > 0 && problems.length === 0;
process.exit(ok ? 0 : 1);
