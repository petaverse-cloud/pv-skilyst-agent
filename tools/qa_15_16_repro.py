#!/usr/bin/env python3
"""QA #15/#16 fix verification against the real dev API (platform side).

Reproduces the exact #15 scenario (agent builds a material node with the
drift-spelled `entry_id` config field, then asks for a first_frame wiring)
plus the #16 cost/origin observations, through a live `skilyst serve` the
same way qa's tools/qa_a3_t1a.py drove it.

Checks (all must PASS on a fixed runtime):
  1. #15 wiring semantics: after the connect turn, the consumer's blueprint
     config carries material_deps=[{key, input_port}] with the requested
     port -- NOT a silently degraded plain depends_on edge.
  2. #15 normalization is loud: the action row for the wiring reports the
     entry_id -> pool_entry_id normalization (board_delta.normalized_material_field).
  3. #15 negative: wiring with an explicit port from a NON-material source is
     refused (structured error surfaces to the model/user, board untouched).
  4. #16A cost: a paid submission's action row carries cost.estimate_usd
     (from the wire's total_estimate / hold fallback) -- the badge amount qa
     saw missing ($-less paid cards).
  5. #16B origin: any note row (e.g. lock note) carries origin=agent (R3
     contract agent|user, never system).

Usage:
  python3 tools/qa_15_16_repro.py [--port 8811] [--skip-paid]

Credentials: ~/.skilyst/env (never committed). Evidence lands in
evidence/qa-15-16-fix/ (redacted of tokens).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-15-16-fix")
TOKEN_FILE = "/tmp/qa-15-16-serve.env"

results: dict = {"checks": []}


def save(name: str, obj) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


def check(name: str, ok: bool, detail: str) -> bool:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return bool(ok)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8811)
    parser.add_argument("--skip-paid", action="store_true",
                        help="skip check 4 (the $0.0? paid submission)")
    args = parser.parse_args()

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
        jwt = (json.loads(resp.read()).get("payload") or {})["token"]

    # a scratch board with one image in the pool (reuse qa's CDN artifact,
    # free: add_media is not a paid call)
    wf_req = urllib.request.Request(API + "/api/v1/workflows", method="POST",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        data=json.dumps({"name": f"qa-15-16-fix-{int(time.time())}",
                         "description": "#15/#16 fix verification board"}).encode())
    with urllib.request.urlopen(wf_req, timeout=30) as resp:
        wp = json.loads(resp.read())
    WF = (wp.get("payload") or wp).get("id")
    print(f"[board] {WF}")

    image_url = ("https://beehive-cdn.verse4.pet/beehive-temp/beehive/"
                 "job-1790601545790-53c28591cf336fa8/"
                 "node-job-1790601545790-53c28591cf336fa8-generate-gpt-image-2-2-image.png")
    # probe the artifact URL; fall back to adding any reachable pool image
    add = urllib.request.Request(f"{API}/api/v1/workflows/{WF}/media-pool", method="POST",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        data=json.dumps({"url": image_url, "name": "qa-15-ref", "kind": "image",
                         "mime_type": "image/png", "origin": {"kind": "upload"}}).encode())
    with urllib.request.urlopen(add, timeout=60) as resp:
        pool = (json.loads(resp.read()).get("payload") or {}).get("media_pool") or []
    if not pool:
        print("[fatal] could not seed a pool image")
        return 2
    print(f"[pool] {len(pool)} entry(ies)")

    # start serve
    token = f"qa15-{int(time.time())}"
    with open(TOKEN_FILE, "w") as f:
        f.write(f"TOKEN={token}")
    os.chmod(TOKEN_FILE, 0o600)
    serve = subprocess.Popen(
        [sys.executable, "-m", "cli", "serve", "--port", str(args.port), "--token", token,
         "--skill", "skilyst/canvas-ops"] + ([] if args.skip_paid else ["--live"]),
        cwd=os.path.join(ROOT, "src"), env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    BASE = f"http://127.0.0.1:{args.port}"
    ready = False
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = urllib.request.Request(BASE + "/health",
                                       headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(r, timeout=3) as resp:
                json.loads(resp.read())
                ready = True
                break
        except Exception:
            time.sleep(0.5)
    if not ready:
        serve.kill()
        print("[fatal] serve never became ready")
        return 2
    print(f"[serve] ready on {BASE}")

    def req(method: str, path: str, body: dict | None = None, timeout: int = 600) -> dict:
        r = urllib.request.Request(BASE + path, method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read())

    @staticmethod
    def _rows(session_payload: dict) -> list[dict]:
        """GET /session/{id} answers {ok, data:{messages}} -- the rows live
        under data."""
        return (session_payload.get("messages")
                or (session_payload.get("data") or {}).get("messages") or [])

    confirms: list[dict] = []

    def stream_turn(session_id, text, approve=True):
        rows: list[dict] = []
        r = urllib.request.Request(BASE + "/message", method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "text/event-stream"},
            data=json.dumps({"message": text, "session_id": session_id,
                             "stream": True, "confirm_paid": True,
                             "max_turns": 16, "skill": "skilyst/canvas-ops"}).encode())
        sid_holder = [session_id or ""]

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
                            confirms.append({"turn": text[:40], "quote": row.get("quote")})
                            ans = req("POST", f"/confirm/{sid_holder[0]}", {"approve": approve})
                            print(f"[confirm] {'approved' if approve else 'declined'}")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        while t.is_alive():
            t.join(10)
        done = next((x["data"] for x in rows if x.get("event") == "done"), None)
        return (done or {}).get("data") or {}, rows

    ok = True
    sid = None
    try:
        # -- turn 1: the #15 reproduction -----------------------------------
        # deliberately ask for the drift-spelled field: "用 entry_id 字段" --
        # the model writes entry_id exactly as in the qa incident.
        t1, rows1 = stream_turn(None,
            f"在画板 {WF} 上工作。创建一个 material 节点，config 用 entry_id 字段指向池里第 1 张"
            f"（就用字段名 entry_id，不要用 pool_entry_id），kind 是 image。"
            f"再创建一个 generate minimax-h3 视频节点（提示词：夜色中一只白猫道士巡山）。"
            f"然后把 material 节点接到 minimax 节点的 first_frame 端口。只建节点和接线，不要提交任务。")
        save("1-turn1-sse.json", rows1)
        sid = t1.get("session_id")
        print(f"[turn1] ok={t1.get('ok')}")

        board = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"{API}/api/v1/workflows/{WF}", headers={"Authorization": f"Bearer {jwt}"}),
            timeout=30).read())
        payload = board.get("payload") or board
        save("2-board-after-wiring.json", payload)
        nodes = payload.get("nodes") or []
        material = next((n for n in nodes if n.get("type") == "material"), None)
        mini = next((n for n in nodes if "minimax" in str(n.get("provider"))), None)

        # check 1: material_deps landed with the port
        deps = ((mini or {}).get("config") or {}).get("material_deps") or []
        wired = any(d.get("input_port") == "first_frame" for d in deps)
        ok &= check("#15 material_deps carries input_port=first_frame", wired,
                    f"material_deps={json.dumps(deps)}")

        # the material node itself: normalized to pool_entry_id?
        mat_config = (material or {}).get("config") or {}
        ok &= check("#15 material pointer normalized to pool_entry_id",
                    bool(mat_config.get("pool_entry_id")),
                    f"config keys={sorted(mat_config)}")

        # check 2: the normalization was reported on the action row
        sess = req("GET", f"/session/{sid}")
        msgs = _rows(sess)
        save("3-session-messages.json", sess)
        wiring_actions = [m for m in msgs if m.get("role") == "action"
                          and m.get("tool") == "canvas_connect_ports"]
        reported = any((m.get("board_delta") or {}).get("normalized_material_field")
                       for m in wiring_actions)
        ok &= check("#15 normalization reported on the action row",
                    len(wiring_actions) >= 1 and reported,
                    f"wiring action rows={len(wiring_actions)}, reported={reported}")

        # check 3: negative -- explicit port on a non-material source refused
        t2, rows2 = stream_turn(sid,
            "现在把一个 generate script 节点（指令随便写个测试脚本）接到 minimax 节点的 "
            "first_frame 端口（就接这个端口，不要只连普通边）。只接线不要提交。")
        save("4-turn2-sse.json", rows2)
        board2 = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"{API}/api/v1/workflows/{WF}", headers={"Authorization": f"Bearer {jwt}"}),
            timeout=30).read())
        payload2 = board2.get("payload") or board2
        save("5-board-after-negative.json", payload2)
        mini2 = next((n for n in payload2.get("nodes") or []
                      if "minimax" in str(n.get("provider"))), None)
        deps2 = ((mini2 or {}).get("config") or {}).get("material_deps") or []
        script_deps = [d for d in deps2 if "script" in str(d.get("key"))]
        ok &= check("#15 non-material port wiring refused (no script entry in material_deps)",
                    not script_deps,
                    f"material_deps={json.dumps(deps2)}")
        # the refusal must be visible in the turn output (structured, not silent)
        answer2 = ""
        sess2 = req("GET", f"/session/{sid}")
        for m in _rows(sess2):
            if m.get("role") == "assistant":
                answer2 = str(m.get("content") or "")
        refused_visible = ("material" in answer2.lower() or "端口" in answer2
                           or "input_port" in answer2 or "不能" in answer2 or "无法" in answer2)
        ok &= check("#15 refusal surfaced to the user (structured, not silent)",
                    refused_visible, f"answer head: {answer2[:200]}")

        # check 4: #16A cost on a paid submission (cheapest: 6s 768P)
        if not args.skip_paid:
            t3, rows3 = stream_turn(sid,
                "把 minimax 节点改成 6 秒、768P、16:9，然后提交这个付费视频任务。")
            save("6-turn3-paid-sse.json", rows3)
            save("7-confirms.json", confirms)
            sess3 = req("GET", f"/session/{sid}")
            save("8-session-messages-final.json", sess3)
            submit_actions = [m for m in _rows(sess3)
                              if m.get("role") == "action" and m.get("tool")
                              in ("canvas_submit_node_job", "canvas_run_workflow")]
            with_cost = [m for m in submit_actions if (m.get("cost") or {}).get("estimate_usd")]
            ok &= check("#16A paid action row carries cost.estimate_usd",
                        len(with_cost) >= 1,
                        f"submit actions={len(submit_actions)}, with cost={len(with_cost)}, "
                        f"costs={[m.get('cost') for m in submit_actions]}")
            if confirms:
                quote = confirms[-1].get("quote") or {}
                ok &= check("#16A confirm quote shows the estimate amount",
                            quote.get("total_estimate_usd") is not None
                            or quote.get("total_hold") is not None,
                            f"quote={json.dumps(quote)[:200]}")

        # check 5: #16B note origin (any note rows in this session)
        note_rows = [m for m in _rows(req("GET", f"/session/{sid}"))
                     if m.get("role") == "note"]
        origins = sorted({m.get("origin") for m in note_rows})
        ok &= check("#16B note rows carry contract origins (agent|user, never system)",
                    all(o in ("agent", "user") for o in origins),
                    f"note origins={origins} over {len(note_rows)} rows")

    finally:
        serve.kill()
        try:
            os.remove(TOKEN_FILE)
        except OSError:
            pass

    results["session_id"] = sid
    results["workflow_id"] = WF
    save("9-results.json", results)
    passed = sum(1 for c in results["checks"] if c["pass"])
    print(f"\n{passed}/{len(results['checks'])} checks passed")
    return 0 if passed == len(results["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
