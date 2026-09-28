#!/usr/bin/env python3
"""A3 S4 E2E: the full creation story against a real `skilyst serve`.

Drives one conversation end-to-end on the dev API (e2e-platform wallet):
create script node -> storyboard expansion -> paid reference image ->
material wiring -> paid 15s minimax video -> board preview. The paid
submissions run through the S4 confirm gate: the SSE stream carries
`confirm_request` with the quote, and this driver answers POST /confirm
(approve) -- exactly what the desktop/web shells do.

Usage: python3 tools/a3_s4_e2e.py [out-dir]   (defaults to evidence/a3-s4)
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "evidence", "a3-s4")
os.makedirs(OUT, exist_ok=True)

PORT = 8791
TOKEN = "e2e-" + secrets.token_urlsafe(12)
BASE = f"http://127.0.0.1:{PORT}"
STORY = "钟馗夜巡，15 秒短剧：钟馗深夜巡城遇一小鬼，幽默打斗"

def log(name: str, obj) -> None:
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        if isinstance(obj, (dict, list)):
            json.dump(obj, f, indent=2, ensure_ascii=False)
        else:
            f.write(str(obj))
    print(f"[evidence] {name}")

# -- out-of-band board creation (the canvas skill operates on an existing  ----
# board; create_workflow is deliberately NOT an agent tool). Credentials come
# from ~/.skilyst/env at runtime only -- nothing is written to disk or logs.
def env_creds() -> dict[str, str]:
    creds = {}
    with open(os.path.expanduser("~/.skilyst/env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                creds[k] = v
    return creds

creds = env_creds()
API = creds.get("BEEHIVE_API", "https://beehive-api.verse4.pet")
login = urllib.request.Request(API + "/api/v1/auth/login", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"username": creds["BEEHIVE_PLATFORM_USER"],
                     "password": creds["BEEHIVE_PLATFORM_PASS"]}).encode())
with urllib.request.urlopen(login, timeout=30) as resp:
    login_data = json.loads(resp.read())
login_payload = login_data.get("payload") or login_data
jwt = login_payload.get("token")

BOARD_NAME = f"a3-s4-e2e-{int(time.time())}"
wf_req = urllib.request.Request(API + "/api/v1/workflows", method="POST",
    headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
    data=json.dumps({"name": BOARD_NAME, "description": "A3 S4 E2E creation story"}).encode())
with urllib.request.urlopen(wf_req, timeout=30) as resp:
    wf_data = json.loads(resp.read())
wf_payload = wf_data.get("payload") or wf_data
WORKFLOW_ID = wf_payload.get("id") or wf_payload.get("workflow", {}).get("id")
print(f"[board] created {BOARD_NAME} -> {WORKFLOW_ID}")
log("00-board-created.json", {"name": BOARD_NAME, "id": WORKFLOW_ID})

def req(method: str, path: str, body: dict | None = None, timeout: int = 900):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())

# -- start the runtime -------------------------------------------------------
ready = os.path.join(OUT, "serve-ready.jsonl")
proc = subprocess.Popen(
    [os.path.join(ROOT, "bin", "skilyst"), "serve", "--live",
     "--skill", "skilyst/canvas-ops", "--port", str(PORT), "--token", TOKEN],
    cwd=ROOT, stdout=open(ready, "w"), stderr=subprocess.STDOUT,
    env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")})
try:
    for _ in range(200):
        if os.path.getsize(ready):
            break
        time.sleep(0.1)
    else:
        raise SystemExit("serve never reported ready")
    for _ in range(100):
        try:
            req("GET", "/health", timeout=5)
            break
        except Exception:
            time.sleep(0.2)

    # -- one SSE conversation with the confirm gate ----------------------------
    events: list[dict] = []
    confirm_event = threading.Event()
    confirm_answered = threading.Event()

    def stream_turn(session_id: str | None, text: str, approve: bool = True) -> dict:
        """POST /message?stream=1, collect SSE rows, answer confirm_request."""
        rows: list[dict] = []
        r = urllib.request.Request(BASE + "/message", method="POST",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                     "Accept": "text/event-stream"},
            data=json.dumps({"message": text, "session_id": session_id,
                             "stream": True, "confirm_paid": True,
                             "max_turns": 16}).encode())
        sid_holder: list[str] = [session_id or ""]

        def reader():
            nonlocal rows
            current_event = {"name": ""}
            with urllib.request.urlopen(r, timeout=1800) as resp:
                for raw in resp:
                    line = raw.decode().strip()
                    if line.startswith("event: "):
                        current_event["name"] = line[7:].strip()
                    elif line.startswith("data: "):
                        try:
                            row = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        rows.append({"event": current_event["name"], "data": row})
                        ev = current_event["name"]
                        if ev == "confirm_request":
                            sid_holder[0] = row.get("session_id") or sid_holder[0]
                            quote = row.get("quote") or {}
                            print(f"[confirm_request] hold={quote.get('total_hold_display')} USD")
                            log(f"quote-{int(time.time())}.json", row)
                            if approve:
                                ans = req("POST", f"/confirm/{sid_holder[0]}", {"approve": True})
                                print(f"[confirm] approved: {ans}")
                            else:
                                ans = req("POST", f"/confirm/{sid_holder[0]}", {"approve": False})
                                print(f"[confirm] declined: {ans}")
                            confirm_answered.set()
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        while t.is_alive():
            t.join(10)
        done = next((x["data"] for x in rows if x.get("event") == "done"), None)
        return {"rows": rows, "done": done}

    # Turn 1: script node on the pre-created board.
    print("[turn 1] script node")
    t1 = stream_turn(None,
        f"在画板 {BOARD_NAME}（workflow id {WORKFLOW_ID}）上工作：创建一个 script 生成节点，"
        f"写好短剧脚本指令：{STORY}。完成后简述节点状态。")
    log("01-turn1-sse.json", t1["rows"])
    sid = (t1["done"] or {}).get("session_id") or ""
    print(f"[turn 1] session={sid} ok={(t1['done'] or {}).get('ok')}")

    # Turn 1b: storyboard expansion -- image node + wiring.
    print("[turn 1b] storyboard: image node + wiring")
    t1b = stream_turn(sid,
        f"扩分镜：在画板 {WORKFLOW_ID} 上创建一个图片生成节点做钟馗参考图"
        f"（中国风神话，红袍钟馗，3D 渲染风格），并把脚本节点连到图片节点。完成后简述。")
    log("01b-turn1b-sse.json", t1b["rows"])
    print(f"[turn 1b] ok={(t1b['done'] or {}).get('ok')}")

    # Turn 2: paid reference image through the confirm gate.
    print("[turn 2] paid reference image (confirm gate)")
    t2 = stream_turn(sid, "现在把钟馗参考图生成出来（付费任务）。")
    log("02-turn2-image-sse.json", t2["rows"])
    print(f"[turn 2] ok={(t2['done'] or {}).get('ok')}")

    # Turn 3: minimax video node + wiring + paid 15s job.
    print("[turn 3] minimax wiring + paid 15s video (confirm gate)")
    t3 = stream_turn(sid,
        "创建 minimax-h3 视频生成节点（15 秒、9:16、768P，钟馗夜巡短剧），"
        "把池里的钟馗参考图连到视频节点的首帧端口，脚本连提示词，然后提交付费生成任务。")
    log("03-turn3-video-sse.json", t3["rows"])
    print(f"[turn 3] ok={(t3['done'] or {}).get('ok')}")

    # -- wait for the async minimax job to land its artifact in the pool -------
    print("[poll] waiting for the video to land in the media pool…")
    pool: list = []
    deadline = time.time() + 900
    while time.time() < deadline:
        wf_get = urllib.request.Request(f"{API}/api/v1/workflows/{WORKFLOW_ID}",
            headers={"Authorization": f"Bearer {jwt}"})
        with urllib.request.urlopen(wf_get, timeout=30) as resp:
            final = json.loads(resp.read())
        payload = final.get("payload") or final
        pool = payload.get("media_pool") or []
        if any(e.get("kind") == "video" for e in pool):
            break
        time.sleep(20)
    log("05-final-board.json", final.get("payload") or final)
    print(f"[board] nodes={len((final.get('payload') or final).get('nodes') or [])} pool={len(pool)}")
    for e in pool:
        print(f"  pool: {e.get('kind')} {e.get('name')} -> {str(e.get('url'))[:80]}")
finally:
    try:
        req("POST", "/shutdown", {}, timeout=10)
    except Exception:
        pass
    proc.wait(timeout=30)
print("[done]")
