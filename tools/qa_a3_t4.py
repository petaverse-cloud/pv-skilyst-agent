#!/usr/bin/env python3
"""QA #13 T4: session-stream boundaries.

T4a pure-chat turn: a message that requires no execution must produce ZERO
action rows on the session transcript.
T4c chat-API compatibility: history() must filter action/note rows out of the
chat roles; GET /session/<id> exposes them for the shell.

Usage: python3 tools/qa_a3_t4.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
PORT = 8801
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]
BASE = f"http://127.0.0.1:{PORT}"


def req(method: str, path: str, body: dict | None = None, timeout: int = 900) -> dict:
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def sse_turn(session_id: str | None, text: str, stream: bool = True) -> tuple[dict, list]:
    rows: list[dict] = []
    if not stream:
        return req("POST", "/message", {"message": text, "session_id": session_id}), []
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


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


results: dict = {"checks": []}


def check(name: str, ok: bool, detail: str) -> None:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


# ---- T4a: pure chat turn -> no action rows ---------------------------------
t0 = sse_turn(None, "你好，请只用文字回答，不要调用任何工具：一句话介绍你能做什么。")
sid = (t0[0] or {}).get("session_id") or ""
save("40-t4a-pure-chat-sse.json", {"session_id": sid, "done": t0[0], "rows": t0[1]})
sess = req("GET", f"/session/{sid}")
messages = sess.get("messages") or (sess.get("data") or {}).get("messages") or []
roles = [m.get("role") for m in messages]
save("41-t4a-session-messages.json", sess)
check("T4a no action rows after pure chat", "action" not in roles,
      f"roles={roles}")

# ---- T4c: chat-API compatibility -------------------------------------------
# The session above now has user/assistant rows only. Now run one turn WITH a
# canvas action on a scratch board, then verify history() filters it.
import subprocess
sys.path.insert(0, os.path.join(ROOT, "src"))
creds = {}
with open(os.path.expanduser("~/.skilyst/env")) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            creds[k] = v
API = creds.get("BEEHIVE_API", "https://beehive-api.verse4.pet")
login = urllib.request.Request(API + "/api/v1/auth/login", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"username": creds["BEEHIVE_PLATFORM_USER"],
                     "password": creds["BEEHIVE_PLATFORM_PASS"]}).encode())
with urllib.request.urlopen(login, timeout=30) as resp:
    lp = json.loads(resp.read())
jwt = (lp.get("payload") or lp)["token"]
wf_req = urllib.request.Request(API + "/api/v1/workflows", method="POST",
    headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
    data=json.dumps({"name": f"qa-a3-t4-{int(time.time())}",
                     "description": "QA T4 session boundary scratch board"}).encode())
with urllib.request.urlopen(wf_req, timeout=30) as resp:
    wp = json.loads(resp.read())
WF = (wp.get("payload") or wp).get("id")
print(f"[board] {WF}")

# a canvas action turn on the same session
t1 = sse_turn(sid, f"在画板 {WF}（workflow id {WF}）上创建一个 script 节点，"
                   f"指令写'一只猫在月光下散步，10 秒'。完成后简述节点状态。")
save("42-t4c-action-turn-sse.json", t1[1])
sess2 = req("GET", f"/session/{sid}")
messages2 = sess2.get("messages") or (sess2.get("data") or {}).get("messages") or []
roles2 = [m.get("role") for m in messages2]
save("43-t4c-session-messages.json", sess2)

action_rows = [m for m in messages2 if m.get("role") == "action"]
check("T4c action row exists after canvas turn", len(action_rows) >= 1,
      f"action rows={len(action_rows)}")
if action_rows:
    a = action_rows[0]
    check("T4c action row origin=agent", a.get("origin") == "agent",
          f"origin={a.get('origin')}")
    check("T4c action row has board_delta", bool(a.get("board_delta")),
          f"board_delta={json.dumps(a.get('board_delta'))[:120]}")

# history filtering: the runner's SessionStore.history() is what feeds the LLM;
# serve exposes the chat-compat view through /session/<id>.history if present.
try:
    hist = req("GET", f"/session/{sid}/history")
    hmsgs = hist.get("history") or (hist.get("data") or {}).get("history") or []
    hroles = [m.get("role") for m in hmsgs]
    save("44-t4c-history.json", hist)
    check("T4c history filters action/note", "action" not in hroles and "note" not in hroles,
          f"roles={hroles}")
except Exception as exc:
    # No dedicated history route: derive from the store directly instead.
    check("T4c history endpoint", False, f"GET /session/{sid}/history: {exc}")

results["session_id"] = sid
results["workflow_id"] = WF
save("45-t4-results.json", results)
print("[t4 done]")
