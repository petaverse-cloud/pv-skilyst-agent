/**
 * Drive the shell's UI in a real browser and prove a message round-trip.
 *
 * The Tauri window itself cannot be scripted from a headless agent, so this runs the
 * same frontend against the same runtime contract in Chrome: it types into the
 * composer, sends, and reads the rendered transcript back out of the DOM. Point it at
 * a vite dev server that was started with VITE_SKILYST_RUNTIME (see desktop/README.md).
 *
 *   PLAYWRIGHT_CORE_ROOT=~/pv-pawly-web/web node scripts/ui-smoke.mjs http://localhost:1421 "<message>"
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

await page.fill("textarea[data-testid='composer']", message);
await page.click("[aria-label='send']");
await page.waitForSelector("[data-testid='message-assistant']", { timeout: 180000 });
await page.waitForFunction(() => !document.querySelector("[data-testid='streaming-answer']"), null, {
  timeout: 180000,
});

const transcript = await page.locator("[data-testid^='message-']").allInnerTexts();
console.log("--- transcript (rendered) ---");
transcript.forEach((block) => console.log(block.replace(/\n+/g, " ").slice(0, 300)));
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
process.exit(transcript.length >= 2 && errorBanner === 0 ? 0 : 1);
