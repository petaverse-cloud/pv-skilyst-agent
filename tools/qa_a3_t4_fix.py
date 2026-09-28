#!/usr/bin/env python3
"""QA #13 T4 follow-up: correct the two over-strict checks.

T4c-fix1: board_delta is only expected on MUTATING actions (create/write/
connect/rename/delete/submit); read-only tools legitimately carry none.
T4c-fix2: history() is the runner-internal chat channel (no HTTP route).
Verify (a) SessionStore.history() filters action/note rows by loading the
real session from ~/.skilyst/sessions, and (b) a follow-up pure-chat turn
on the SAME session still runs -- the loop feeds history() to the LLM, so a
polluted history would break the call.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
PORT = 8801
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]
BASE = f"http://127.0.0.1:{PORT}"

results = json.load(open(os.path.join(OUT, "45-t4-results.json")))
SID = results["session_id"]


def req(method: str, path: str, body: dict | None = None, timeout: int = 900) -> dict:
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def sse_turn(session_id: str | None, text: str) -> tuple[dict, list]:
    rows: list[dict] = []
    r = urllib.request.Request(BASE + "/message", method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        data=json.dumps({"message": text, "session_id": session_id,
                         "stream": True, "max_turns": 8}).encode())
    current = {"name": ""}
    with urllib.request.urlopen(r, timeout=1800) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("event: "):
                current["name"] = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    row = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                rows.append({"event": current["name"], "data": row})
    done = next((x["data"] for x in rows if x.get("event") == "done"), None)
    return (done or {}).get("data") or {}, rows


# (a) SessionStore.history() filters action/note rows -- load the real session.
sys.path.insert(0, os.path.join(ROOT, "src"))
from session.store import SessionStore  # noqa: E402

store = SessionStore(os.path.expanduser("~/.skilyst/sessions"))
session = store.open(SID)
hist = session.history()
hroles = [m.get("role") for m in hist]
polluted = [m for m in hist if m.get("role") not in ("user", "assistant", "tool", "system")]

# (b) follow-up pure chat on the same session
t2, rows2 = sse_turn(SID, "不用任何工具，纯文字：用一句话复述你刚才在画板上创建了什么。")
sess3 = req("GET", f"/session/{SID}")
msgs3 = sess3.get("messages") or (sess3.get("data") or {}).get("messages") or []
roles3 = [m.get("role") for m in msgs3]
new_actions = [m for m in msgs3 if m.get("role") == "action" and m.get("seq", 0) > 20]
answer = ""
for m in msgs3:
    if m.get("role") == "assistant":
        answer = str(m.get("content"))[:200]

out = {
    "history_roles": hroles,
    "history_polluted_rows": len(polluted),
    "history_filters_action_note": not polluted,
    "followup_turn_ok": bool(t2.get("ok")),
    "followup_roles_tail": roles3[-6:],
    "followup_new_action_rows": len(new_actions),
    "followup_answer": answer,
    "sse_done": t2,
}
json.dump(out, open(os.path.join(OUT, "46-t4c-history-verification.json"), "w"),
          indent=2, ensure_ascii=False)
print(json.dumps(out, indent=2, ensure_ascii=False)[:1500])
