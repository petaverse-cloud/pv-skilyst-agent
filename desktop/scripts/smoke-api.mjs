/**
 * Headless check of the shell's own HTTP client (src/api.ts) against a live runtime.
 *
 * The webview and this script run the same module: esbuild bundles it, Node runs it
 * with a minimal `window` shim, and the calls below are the calls App.tsx makes. It
 * exists because the desktop window cannot be driven from CI or from a headless
 * agent: this is the closest honest thing to "type a message and read the answer".
 *
 *   node scripts/smoke-api.mjs <base_url> <token> ["message"]
 */
import { bundleApi } from "./build-api-for-node.mjs";

const [base, token, message = "In one short sentence: what can you do?"] = process.argv.slice(2);
if (!base || !token) {
  console.error("usage: node scripts/smoke-api.mjs <base_url> <token> [message]");
  process.exit(2);
}

const module = await bundleApi(base, token);
const { api, sendMessage, runtime } = module;

const sessions = await api("/sessions");
console.log(`sessions listed: ${sessions.sessions.length}`);

const deltas = [];
const notes = [];
const summary = await sendMessage(
  { message, dry_run: true },
  {
    onDelta: (text) => deltas.push(text),
    onNote: (line) => notes.push(line),
  },
);
console.log(`delta events: ${deltas.length}`);
console.log(`streamed answer: ${deltas.join("").slice(0, 200)}`);
console.log(`notes: ${notes.length}`);
console.log(`run ok: ${summary.ok} | stop_reason: ${summary.stop_reason} | model: ${summary.model}`);
console.log(`session: ${summary.session_id} | turns: ${summary.turns} | usage: ${JSON.stringify(summary.usage)}`);

const detail = await api(`/session/${summary.session_id}`);
console.log(`transcript roles: ${detail.messages.map((m) => m.role).join(",")}`);
console.log(`persisted answer: ${(detail.messages.at(-1)?.content ?? "").slice(0, 200)}`);
console.log(`runtime: ${runtime()?.base_url} pid ${runtime()?.pid} dry_run ${runtime()?.dry_run}`);
