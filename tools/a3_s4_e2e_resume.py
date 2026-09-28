#!/usr/bin/env python3
"""A3 S4 E2E recovery turn: the first minimax submission failed (prompt is
required -- single-node execution mode does not inject upstream script text).
Reuses the SAME session; the agent reads the script node output, writes the
distilled prompt into the minimax node config, and resubmits through the
confirm gate. Then polls until the video lands in the media pool.

Usage: python3 tools/a3_s4_e2e_resume.py <session_id> <workflow_id>
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = os.path.join(ROOT, "evidence", "a3-s4")
SID = sys.argv[1]
WF = sys.argv[2]

PORT = 8791
TOKEN = "e2e-" + secrets.token_urlsafe(12)
BASE = f"http://127.0.0.1:{PORT}"

def log(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        if isinstance(obj, (dict, list)):
            json.dump(obj, f, indent=2, ensure_ascii=False)
        else:
            f.write(str(obj))
    print(f"[evidence] {name}")

def req(method: str, path: str, body: dict | None = None, timeout: int = 900):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())

def creds() -> dict[str, str]:
    d = {}
    with open(os.path.expanduser("~/.skilyst/env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1); d[k] = v
    return d

C = creds()
API = C.get("BEEHIVE_API", "https://beehive-api.verse4.pet")

def core_login() -> str:
    r = urllib.request.Request(API + "/api/v1/auth/login", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"username": C["BEEHIVE_PLATFORM_USER"],
                         "password": C["BEEHIVE_PLATFORM_PASS"]}).encode())
    with urllib.request.urlopen(r, timeout=30) as resp:
        return (json.loads(resp.read()).get("payload"))["token"]

jwt = core_login()
ready = os.path.join(OUT, "serve-resume-ready.jsonl")
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
    for _ in range(100):
        try:
            req("GET", "/health", timeout=5); break
        except Exception:
            time.sleep(0.2)

    rows: list[dict] = []
    r = urllib.request.Request(BASE + "/message", method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        data=json.dumps({"message":
            f"刚才 minimax 视频任务失败了（prompt is required）。请自纠：读取 script 节点"
            f"（generate-script-1）的输出，把精炼后的视频提示词写进 minimax 节点"
            f"（generate-minimax-h3-3）的 config.prompt，然后重新提交这个付费任务。",
            "session_id": SID, "stream": True, "confirm_paid": True, "max_turns": 16}).encode())

    def reader():
        cur = {"name": ""}
        with urllib.request.urlopen(r, timeout=1800) as resp:
            for raw in resp:
                line = raw.decode().strip()
                if line.startswith("event: "):
                    cur["name"] = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        row = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    rows.append({"event": cur["name"], "data": row})
                    if cur["name"] == "confirm_request":
                        qsid = row.get("session_id") or SID
                        print(f"[confirm_request] hold={(row.get('quote') or {}).get('total_hold_display')} USD")
                        log(f"quote-resume-{int(time.time())}.json", row)
                        ans = req("POST", f"/confirm/{qsid}", {"approve": True})
                        print(f"[confirm] approved: {ans}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while t.is_alive():
        t.join(10)
    log("03b-turn3b-resume-sse.json", rows)
    done = next((x["data"] for x in rows if x.get("event") == "done"), None)
    print("[turn 3b] ok=", (done or {}).get("ok"))

    print("[poll] waiting for the video to land in the media pool…")
    pool: list = []
    final = None
    deadline = time.time() + 1200
    while time.time() < deadline:
        g = urllib.request.Request(f"{API}/api/v1/workflows/{WF}",
            headers={"Authorization": f"Bearer {jwt}"})
        with urllib.request.urlopen(g, timeout=30) as resp:
            final = json.loads(resp.read()).get("payload")
        pool = final.get("media_pool") or []
        if any(e.get("kind") == "video" for e in pool):
            break
        time.sleep(20)
    log("05-final-board.json", final)
    print(f"[board] nodes={len(final.get('nodes') or [])} pool={len(pool)}")
    for e in pool:
        print(f"  pool: {e.get('kind')} {e.get('name')} -> {str(e.get('url'))[:90]}")
finally:
    try:
        req("POST", "/shutdown", {}, timeout=10)
    except Exception:
        pass
    proc.wait(timeout=30)
print("[done]")
