/**
 * Drive the shell's UI in a real browser and prove a message round-trip.
 *
 * The Tauri window itself cannot be scripted from a headless agent, so this runs the
 * same frontend against the same runtime contract in Chrome: it types into the
 * composer, sends, and reads the rendered transcript back out of the DOM. Point it at
 * a vite dev server that was started with VITE_SKILYST_RUNTIME (see desktop/README.md).
 *
 *   PLAYWRIGHT_CORE_ROOT=~/pv-pawly-web/web node scripts/ui-smoke.mjs http://localhost:1420 "<message>"
 *
 * It exits non-zero unless, in one real run: the answer arrived, no error banner is
 * up, assistant bubbles are rendered as markdown (element counts, no literal ```),
 * the transcript auto-scrolled to the newest message, and the status line changed
 * while the run was in flight. MID_SHOT_PATH photographs the mid-stream state and
 * SHOT_PATH the finished one.
 *
 * playwright-core is not a dependency of this package (it would be a 300MB dev
 * dependency for one check); set PLAYWRIGHT_CORE_ROOT to any project that has it, and
 * CHROME_PATH if Chrome is not at the default macOS location.
 */
import { createRequire } from "node:module";

const [url, message = "In one short sentence: what can you do?"] = process.argv.slice(2);
if (!url) {
  console.error("usage: node scripts/ui-smoke.mjs <dev-server-url> [message]");
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
const sessionsBefore = await page.locator(".mantine-NavLink-root").count();
console.log(`app mounted; sessions in the sidebar before: ${sessionsBefore}`);
console.log(`runtime badge: ${(await page.locator("[data-testid='runtime-mode']").innerText().catch(() => "none"))}`);

// The mid-stream indicator is transient by design, so it is recorded from inside the
// page (mutation observer + a 500ms sampler) instead of being polled from outside,
// where a fast answer would be missed and the check would be a lie.
await page.evaluate(() => {
  window.__streamLog = [];
  const record = () => {
    const status = document.querySelector("[data-testid='stream-status']");
    const elapsed = document.querySelector("[data-testid='stream-elapsed']");
    const spinner = document.querySelector("[data-testid='stream-spinner']");
    const line = status
      ? `${status.innerText} · ${elapsed ? elapsed.innerText : "—"}${spinner ? " · spinner" : ""}`
      : "idle";
    if (window.__streamLog[window.__streamLog.length - 1] !== line) window.__streamLog.push(line);
  };
  record();
  new MutationObserver(record).observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
  });
  setInterval(record, 500);
});

await page.fill("textarea[data-testid='composer']", message);
await page.click("[aria-label='send']");
// Busy state is set before the request goes out, so the status card must appear.
await page.waitForSelector("[data-testid='streaming-answer']", { timeout: 30000 });
console.log(`status while running: ${(await page.locator("[data-testid='stream-status']").innerText()).trim()}`);
const midShot = process.env.MID_SHOT_PATH;
if (midShot) {
  await page.screenshot({ path: midShot, fullPage: false });
  console.log(`mid-stream screenshot: ${midShot}`);
}
await page.waitForSelector("[data-testid='message-assistant']", { timeout: 180000 });
await page.waitForFunction(() => !document.querySelector("[data-testid='streaming-answer']"), null, {
  timeout: 180000,
});
await page.waitForTimeout(400); // let the final auto-scroll settle

const transcript = await page.locator("[data-testid^='message-']").allInnerTexts();
console.log("--- transcript (rendered) ---");
transcript.forEach((block) => console.log(block.replace(/\n+/g, " ").slice(0, 300)));
const streamLog = await page.evaluate(() => window.__streamLog);
console.log(`status line over the run: ${streamLog.join("  ->  ")}`);

const probe = await page.evaluate(() => {
  const scope = document.querySelector("[data-testid='transcript']");
  const viewport = scope.querySelector(".mantine-ScrollArea-viewport");
  const rendered = Array.from(scope.querySelectorAll("[data-testid='message-assistant']")).map(
    (node) => node.innerText,
  );
  return {
    markdownRoots: scope.querySelectorAll(".md").length,
    headings: scope.querySelectorAll(".md h1, .md h2, .md h3, .md h4").length,
    lists: scope.querySelectorAll(".md ul, .md ol").length,
    codeBlocks: scope.querySelectorAll(".md .md-pre").length,
    highlightedTokens: scope.querySelectorAll(".md .md-pre .token").length,
    inlineCode: scope.querySelectorAll(".md .md-inline-code").length,
    bold: scope.querySelectorAll(".md strong").length,
    literalFences: rendered.filter((text) => text.includes("```")).length,
    scrolledToBottom: viewport
      ? viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 4
      : null,
  };
});
console.log(`markdown probe: ${JSON.stringify(probe)}`);
const sessionsAfter = await page.locator(".mantine-NavLink-root").count();
console.log(`sessions in the sidebar after: ${sessionsAfter}`);
const errorBanner = await page.locator("[data-testid='error-banner']").count();
console.log(`error banners: ${errorBanner}`);
console.log(`page problems: ${problems.length ? problems.join(" | ") : "none"}`);

const shot = process.env.SHOT_PATH;
if (shot) {
  await page.screenshot({ path: shot, fullPage: true });
  console.log(`screenshot: ${shot}`);
}
await browser.close();
const ok =
  transcript.length >= 2 &&
  errorBanner === 0 &&
  probe.markdownRoots > 0 &&
  probe.literalFences === 0 &&
  probe.scrolledToBottom !== false &&
  streamLog.length >= 2;
process.exit(ok ? 0 : 1);
