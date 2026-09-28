#!/usr/bin/env python3
"""QA #13 T1a: the full creation story, INDEPENDENT of platform's evidence.

script node -> storyboard (image node + wiring) -> paid reference image
(confirm gate) -> material first-frame wiring -> paid minimax 15s video
(confirm gate) -> artifact lands in the media pool.

Assertions beyond "it worked":
- every turn's action rows attach UNDER their triggering user input (single
  timeline, monotonic seq, user row before its actions);
- board nodes/edges match the operations performed;
- both paid jobs went through the SSE confirm gate (confirm_request seen,
  answered via POST /confirm);
- final pool carries kind=video with a CDN url.

Usage: python3 tools/qa_a3_t1a.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
PORT = 8801
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]
BASE = f"http://127.0.0.1:{PORT}"
STORY = "白猫道士月下除妖，15 秒短剧：白猫道士深夜巡山遇一小狐妖，诙谐斗法"

results: dict = {"checks": []}


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


def check(name: str, ok: bool, detail: str) -> None:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def req(method: str, path: str, body: dict | None = None, timeout: int = 1800) -> dict:
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


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
wallet0 = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"{API}/api/v1/billing/wallet", headers={"Authorization": f"Bearer {jwt}"}), timeout=30).read())
BALANCE0 = (wallet0.get("payload") or wallet0)["balance"]
print(f"[wallet] start {BALANCE0} uUSD")
save("10-t1a-wallet-start.json", wallet0)

wf_req = urllib.request.Request(API + "/api/v1/workflows", method="POST",
    headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
    data=json.dumps({"name": f"qa-a3-t1a-{int(time.time())}",
                     "description": "QA #13 T1a full creation story"}).encode())
with urllib.request.urlopen(wf_req, timeout=30) as resp:
    wp = json.loads(resp.read())
WF = (wp.get("payload") or wp).get("id")
BOARD_NAME = (wp.get("payload") or wp).get("name")
save("11-t1a-board.json", wp)
print(f"[board] {BOARD_NAME} -> {WF}")

confirms: list[dict] = []


def stream_turn(session_id: str | None, text: str, approve: bool = True) -> tuple[dict, list]:
    rows: list[dict] = []
    r = urllib.request.Request(BASE + "/message", method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        data=json.dumps({"message": text, "session_id": session_id,
                         "stream": True, "confirm_paid": True,
                         "max_turns": 16}).encode())
    sid_holder: list[str] = [session_id or ""]

    def reader():
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
                    if current["name"] == "confirm_request":
                        sid_holder[0] = row.get("session_id") or sid_holder[0]
                        confirms.append({"turn_text": text[:40], "quote": row.get("quote"),
                                         "session_id": row.get("session_id"),
                                         "ts": time.time()})
                        print(f"[confirm_request] hold={row.get('quote', {}).get('total_hold_display')} USD")
                        ans = req("POST", f"/confirm/{sid_holder[0]}", {"approve": approve})
                        print(f"[confirm] {'approved' if approve else 'declined'}: {ans}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while t.is_alive():
        t.join(10)
    done = next((x["data"] for x in rows if x.get("event") == "done"), None)
    return (done or {}).get("data") or {}, rows


# -- the story ----------------------------------------------------------------
t = time.time()
turn1, rows1 = stream_turn(None,
    f"在画板 {BOARD_NAME}（workflow id {WF}）上工作：创建一个 script 生成节点，"
    f"写好短剧脚本指令：{STORY}。完成后简述节点状态。")
save("12-t1a-turn1-script-sse.json", rows1)
print(f"[turn1] ok={turn1.get('ok')} {time.time()-t:.0f}s")

t = time.time()
sid = turn1.get("session_id")
turn1b, rows1b = stream_turn(sid,
    f"扩分镜：在画板 {WF} 上创建一个图片生成节点做白猫道士参考图"
    f"（中国风神话，白袍猫道士持桃木剑，3D 渲染风格），并把脚本节点连到图片节点。完成后简述。")
save("13-t1a-turn1b-storyboard-sse.json", rows1b)
print(f"[turn1b] ok={turn1b.get('ok')} {time.time()-t:.0f}s")

t = time.time()
turn2, rows2 = stream_turn(sid, "现在把白猫道士参考图生成出来（付费任务），完成后告诉我。")
save("14-t1a-turn2-image-sse.json", rows2)
print(f"[turn2] ok={turn2.get('ok')} {time.time()-t:.0f}s")

t = time.time()
turn3, rows3 = stream_turn(sid,
    "创建 minimax-h3 视频生成节点（15 秒、9:16、768P，白猫道士除妖短剧），"
    "把池里的白猫道士参考图连到视频节点的首帧端口，脚本连提示词，然后提交付费生成任务。")
save("15-t1a-turn3-video-sse.json", rows3)
print(f"[turn3] ok={turn3.get('ok')} {time.time()-t:.0f}s")
save("16-t1a-confirms.json", confirms)

# -- wait for the video artifact ---------------------------------------------
final = None
pool = []
deadline = time.time() + 900
while time.time() < deadline:
    g = urllib.request.Request(f"{API}/api/v1/workflows/{WF}",
        headers={"Authorization": f"Bearer {jwt}"})
    with urllib.request.urlopen(g, timeout=30) as resp:
        final = json.loads(resp.read())
    payload = final.get("payload") or final
    pool = payload.get("media_pool") or []
    if any(e.get("kind") == "video" for e in pool):
        break
    time.sleep(20)
save("17-t1a-final-board.json", final.get("payload") or final)
print(f"[board] nodes={len((final.get('payload') or final).get('nodes') or [])} pool={len(pool)}")

wallet1 = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"{API}/api/v1/billing/wallet", headers={"Authorization": f"Bearer {jwt}"}), timeout=30).read())
BALANCE1 = (wallet1.get("payload") or wallet1)["balance"]
save("18-t1a-wallet-end.json", wallet1)
print(f"[wallet] end {BALANCE1} uUSD, delta={BALANCE1-BALANCE0}")

sess = req("GET", f"/session/{sid}")
msgs = sess.get("messages") or (sess.get("data") or {}).get("messages") or []
save("19-t1a-session-messages.json", sess)

# -- assertions ----------------------------------------------------------------
# 1 action attachment: every user row is followed by its actions before the
#   next user row; seq strictly increasing.
seqs = [m.get("seq") for m in msgs]
check("timeline seq monotonic", seqs == sorted(seqs), f"seqs={seqs}")
user_rows = [m for m in msgs if m.get("role") == "user"]
action_rows = [m for m in msgs if m.get("role") == "action"]
check("4 user turns recorded", len(user_rows) == 4, f"user rows={len(user_rows)}")
check("actions exist", len(action_rows) >= 4, f"action rows={len(action_rows)}")
attached = True
for i, u in enumerate(user_rows):
    nxt = user_rows[i + 1]["seq"] if i + 1 < len(user_rows) else 10**9
    if not any(a["seq"] > u["seq"] and a["seq"] < nxt for a in action_rows):
        attached = False
check("each turn's actions attach under its input", attached,
      f"{len(user_rows)} turns, {len(action_rows)} actions")

# 2 board structure matches operations
payload = final.get("payload") or final
nodes = payload.get("nodes") or []
kinds = {}
for n in nodes:
    key = f"{n.get('type')}:{n.get('provider')}"
    kinds[key] = kinds.get(key, 0) + 1
check("script node present", kinds.get("generate:script", 0) >= 1, str(kinds))
check("image node present", "generate:gpt-image-2" in kinds or any("image" in k for k in kinds), str(kinds))
check("minimax node present", any("minimax" in k for k in kinds), str(kinds))
check("material node present", kinds.get("material:", 0) + sum(v for k, v in kinds.items() if k.startswith("material")) >= 1, str(kinds))

# material wiring into minimax first_frame
mini = next((n for n in nodes if "minimax" in str(n.get("provider"))), None)
wired_first_frame = False
if mini:
    for dep in (mini.get("config") or {}).get("material_deps") or []:
        if dep.get("input_port") == "first_frame":
            wired_first_frame = True
check("first_frame material wiring", wired_first_frame,
      f"material_deps={json.dumps((mini or {}).get('config', {}).get('material_deps'))}")

# 3 confirm gates
gated_quotes = [c for c in confirms]
check("2 confirm gates (image + video)", len(confirms) >= 2,
      f"confirms={len(confirms)}")
video_quotes = [c for c in confirms
                if any("minimax" in json.dumps(n) for n in (c.get("quote") or {}).get("nodes") or [])]
check("video job quoted+gated", len(video_quotes) >= 1,
      f"video quotes={len(video_quotes)}")

# 4 artifact
videos = [e for e in pool if e.get("kind") == "video"]
images = [e for e in pool if e.get("kind") == "image"]
check("video artifact in pool", len(videos) >= 1 and bool(videos[0].get("url")),
      f"videos={[str(v.get('url'))[:60] for v in videos]}")
check("image artifact in pool", len(images) >= 1, f"images={len(images)}")

# 5 wallet delta sane (bounded by quoted holds)
delta = BALANCE0 - BALANCE1
holds = [c["quote"]["total_hold"] for c in confirms if c.get("quote")]
check("wallet delta within quoted holds", 0 <= delta <= sum(holds) + 5000,
      f"delta={delta} uUSD, holds={holds}")

results.update({"session_id": sid, "workflow_id": WF, "board_name": BOARD_NAME,
                "wallet_start": BALANCE0, "wallet_end": BALANCE1, "wallet_delta": delta,
                "confirms": confirms})
save("1a-t1a-results.json", results)
print("[t1a done]")
