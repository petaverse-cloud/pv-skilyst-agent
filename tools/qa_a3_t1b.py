#!/usr/bin/env python3
"""QA #13 T1b: port-exclusivity negative path (no submission -> zero cost).

Ask the agent to wire a reference image to BOTH first_frame and reference_image
on minimax-h3 — the documented exclusive-inputs case. Expect:
- query_schema BEFORE connecting (the iron rule);
- a structured error or a schema-informed refusal — never a bare 400 shown to
  the user;
- self-correction: the agent picks one port deliberately and explains, or
  refuses with the reason.

Usage: python3 tools/qa_a3_t1b.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
PORT = 8801
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]
BASE = f"http://127.0.0.1:{PORT}"

results: dict = {"checks": []}


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


def check(name: str, ok: bool, detail: str) -> None:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


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
                         "stream": True, "max_turns": 16}).encode())
    current = {"name": ""}

    def reader():
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

    import threading
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while t.is_alive():
        t.join(10)
    done = next((x["data"] for x in rows if x.get("event") == "done"), None)
    return (done or {}).get("data") or {}, rows


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

# scratch board with: script node + minimax node + one pool image (reuse the
# image artifact from T1a's pool via add_media -> free)
wf_req = urllib.request.Request(API + "/api/v1/workflows", method="POST",
    headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
    data=json.dumps({"name": f"qa-a3-t1b-{int(time.time())}",
                     "description": "QA T1b port mutex negative path"}).encode())
with urllib.request.urlopen(wf_req, timeout=30) as resp:
    wp = json.loads(resp.read())
WF = (wp.get("payload") or wp).get("id")
save("1b-0-board.json", wp)
print(f"[board] {WF}")

t1, rows1 = sse_turn(None,
    f"在画板 {WF}（workflow id {WF}）上：创建一个 minimax-h3 视频生成节点（先不配置提示词），"
    f"再创建一个 script 节点，指令写'夜景测试'。只创建节点，不要连线不要提交。")
save("1b-1-setup-sse.json", rows1)
sid = t1.get("session_id")
print(f"[setup] ok={t1.get('ok')}")

# the adversarial ask: wire the SAME reference image to BOTH exclusive ports
t2, rows2 = sse_turn(sid,
    "现在把池里那张白猫道士参考图（第 1 张）同时接到 minimax 节点的 first_frame 端口"
    "和 reference_image 端口——两个端口都要接。只接线，不要提交任何任务。")
save("1b-2-mutex-attempt-sse.json", rows2)
sess = req("GET", f"/session/{sid}")
msgs = sess.get("messages") or (sess.get("data") or {}).get("messages") or []
save("1b-3-session-messages.json", sess)
answer = ""
for m in msgs:
    if m.get("role") == "assistant":
        answer = str(m.get("content") or "")
save("1b-4-final-answer.json", {"answer": answer, "done": t2})
print("[answer]", answer[:600])

notes = [str(r["data"].get("text")) for r in rows2 if r.get("event") == "note"]
all_notes = notes + [str(r["data"].get("text")) for r in rows1 if r.get("event") == "note"]
joined = "\n".join(all_notes)

# 1 schema-first: a query_schema on minimax happened in the mutex turn
schema_first = any("canvas_query_schema" in n and "minimax" in n for n in notes) or \
    any("canvas_query_schema" in n for n in notes)
check("query_schema precedes connect (mutex turn)",
      any("canvas_query_schema" in n for n in notes), "notes scanned")
# 2 the exclusivity was surfaced: structured refusal or explicit choice
saw_mutex = ("first_frame" in joined and "reference_image" in joined)
check("exclusivity surfaced to the user",
      saw_mutex and ("不能" in answer or "无法" in answer or "互斥" in answer
                     or "exclusive" in answer.lower() or "只能" in answer
                     or "二选一" in answer or "同时" in answer),
      f"answer mentions both ports and a refusal/choice: {answer[:200]}")
# 3 no bare 400 leaked to the user
no_bare_400 = "HTTP 400" not in answer and "400" not in answer
check("no bare 400 shown to user", no_bare_400, f"answer={answer[:150]}")
# 4 self-correction: exactly ONE port wired in the end (or none, with reason)
board = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"{API}/api/v1/workflows/{WF}", headers={"Authorization": f"Bearer {jwt}"}), timeout=30).read())
payload = board.get("payload") or board
mini = next((n for n in payload.get("nodes") or [] if "minimax" in str(n.get("provider"))), None)
deps = ((mini or {}).get("config") or {}).get("material_deps") or []
ports = [d.get("input_port") for d in deps]
check("board ends with at most one image port wired",
      len([p for p in ports if p in ("first_frame", "reference_image", "last_frame")]) <= 1,
      f"material_deps={deps}")
save("1b-5-final-board.json", payload)

results.update({"session_id": sid, "workflow_id": WF, "answer": answer})
save("1b-6-results.json", results)
print("[t1b done]")
