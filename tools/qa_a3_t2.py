#!/usr/bin/env python3
"""QA #13 T2: dual-host alignment — INDEPENDENT verification (11 items).

Same workflow rendered in BOTH hosts, driven independently:
- web console: http://localhost:3457/studio/agent (next dev, web repo main)
- desktop shell: http://localhost:1420 (vite behind tauri dev)

Both hosts connect to the SAME skilyst serve runtime (port 8801) — that is
the product's real dual-host story (one runtime, two windows).

Checks per ALIGNMENT-CHECKLIST.md — counts DOM nodes/edges, verifies action
cards, locate (focusNode), quote card, media pool parity.
"""
from __future__ import annotations

import json
import os
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
PORT = 8801
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]

creds = {}
with open(os.path.expanduser("~/.skilyst/env")) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            creds[k] = v
API = creds.get("BEEHIVE_API", "https://beehive-api.verse4.pet")

import urllib.request  # noqa: E402

r = urllib.request.Request(API + "/api/v1/auth/login", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"username": creds["BEEHIVE_PLATFORM_USER"],
                     "password": creds["BEEHIVE_PLATFORM_PASS"]}).encode())
lp = json.loads(urllib.request.urlopen(r, timeout=30).read())
payload = lp.get("payload") or lp
tok, user = payload["token"], {"user_uid": payload.get("user_uid"),
                               "username": payload.get("username"),
                               "role": payload.get("role")}

results: dict = {"checks": [], "runtime": {"base": f"http://127.0.0.1:{PORT}", "token": "<redacted>"}}


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


def check(name: str, ok: bool, detail: str) -> None:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


# the T1a board is the shared workload (has script/image/material/minimax nodes,
# action history, pool artifacts)
WF = json.load(open(os.path.join(OUT, "1a-t1a-results.json")))["workflow_id"]
SID = json.load(open(os.path.join(OUT, "1a-t1a-results.json")))["session_id"]

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-proxy-server"])

    # ---------------- web console host ------------------------------------
    ctx = browser.new_context(viewport={"width": 1680, "height": 1050})
    page = ctx.new_page()
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.goto("http://localhost:3457/login", wait_until="domcontentloaded")
    page.evaluate(f"""(() => {{
        localStorage.setItem('beehive_token', {json.dumps(tok)});
        localStorage.setItem('beehive_user', {json.dumps(json.dumps(user))});
    }})()""")
    page.goto("http://localhost:3457/studio/agent", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    # bind the runtime (base + token)
    try:
        page.fill("input[placeholder*='127.0.0.1'], input[placeholder*='runtime'], input[placeholder*='URL']",
                  f"http://127.0.0.1:{PORT}", timeout=4000)
    except Exception:
        inputs = page.locator("input").all()
        # first input = base, second = token (per the bind UI)
        if len(inputs) >= 2:
            inputs[0].fill(f"http://127.0.0.1:{PORT}")
            inputs[1].fill(TOKEN)
    else:
        toks = page.locator("input").all()
        if len(toks) >= 2:
            toks[1].fill(TOKEN)
    # click connect
    for label in ("连接", "Connect", "connect"):
        try:
            page.get_by_role("button", name=label).first.click(timeout=2000)
            break
        except Exception:
            continue
    page.wait_for_timeout(2500)
    connected = "连接成功" in page.content() or page.locator("text=/session|会话/i").count() > 0 or \
        page.locator("[data-testid*=session], .session").count() > 0
    # pick the T1a session to load its conversation + actions
    try:
        page.select_option("select", label=SID, timeout=3000)
    except Exception:
        opts = page.locator("option").all()
        for o in opts:
            if SID in (o.get_attribute("value") or "") or SID in (o.text_content() or ""):
                o.select_option()
                break
    page.wait_for_timeout(3000)
    web_state = {
        "url": page.url,
        "connected": connected,
        "console_errors": console_errors[:5],
    }
    web_shot = os.path.join(OUT, "2-t2-web-workbench.png")
    page.screenshot(path=web_shot, full_page=False)
    # DOM facts: action cards + canvas nodes if a board is opened
    web_state["action_cards"] = page.locator("[data-testid*=action], [class*=action-card]").count()
    web_state["canvas_nodes"] = page.locator(".react-flow__node").count()
    web_state["canvas_edges"] = page.locator(".react-flow__edge").count()
    save("2-t2-web-dom.json", web_state)

    # board selector -> open the T1a workflow
    try:
        page.select_option("[data-testid=board-select]", WF, timeout=5000)
        page.wait_for_timeout(4000)
    except Exception as e:
        # try any select with the wf id
        try:
            opts = page.locator("option").all()
            for o in opts:
                if WF in (o.get_attribute("value") or ""):
                    page.select_option("select", o.get_attribute("value"))
                    page.wait_for_timeout(4000)
                    break
        except Exception:
            print("[warn] board select:", e)
    web_state2 = {
        "canvas_nodes": page.locator(".react-flow__node").count(),
        "canvas_edges": page.locator(".react-flow__edge").count(),
        "media_pool_items": page.locator("[data-testid*=pool] img, [class*=media-pool] img, [class*=mediapool] img").count(),
    }
    page.screenshot(path=os.path.join(OUT, "2-t2-web-board.png"), full_page=False)
    save("2-t2-web-board-dom.json", web_state2)
    ctx.close()

    # ---------------- desktop host ----------------------------------------
    ctx2 = browser.new_context(viewport={"width": 1680, "height": 1050})
    dp = ctx2.new_page()
    dp.goto("http://localhost:1420/", wait_until="domcontentloaded")
    dp.wait_for_timeout(2000)
    # desktop login form (beehive data source) if present
    try:
        dp.fill("[data-testid=canvas-login-user]", creds["BEEHIVE_PLATFORM_USER"], timeout=4000)
        dp.fill("[data-testid=canvas-login-pass]", creds["BEEHIVE_PLATFORM_PASS"])
        dp.click("[data-testid=canvas-login-submit]")
        dp.wait_for_timeout(3000)
    except Exception as e:
        print("[warn] desktop login form:", str(e)[:100])
    # open the workflow from the list
    try:
        dp.get_by_text(WF, exact=False).first.click(timeout=8000)
        dp.wait_for_timeout(4500)
    except Exception as e:
        print("[warn] desktop workflow pick:", str(e)[:100])
    desk_state = {
        "canvas_nodes": dp.locator(".react-flow__node").count(),
        "canvas_edges": dp.locator(".react-flow__edge").count(),
        "media_pool_items": dp.locator("[data-testid*=pool] img, [class*=media-pool] img, [class*=mediapool] img").count(),
    }
    dp.screenshot(path=os.path.join(OUT, "2-t2-desktop-board.png"), full_page=False)
    save("2-t2-desktop-board-dom.json", desk_state)
    ctx2.close()
    browser.close()

# ---------------- alignment verdicts ------------------------------------------
# item 1: canvas rendering parity on the same workflow
check("item1 画板渲染 双端节点数一致",
      web_state2["canvas_nodes"] == desk_state["canvas_nodes"] and web_state2["canvas_nodes"] > 0,
      f"web={web_state2['canvas_nodes']} desktop={desk_state['canvas_nodes']}")
check("item1 画板渲染 双端连线数一致",
      web_state2["canvas_edges"] == desk_state["canvas_edges"],
      f"web={web_state2['canvas_edges']} desktop={desk_state['canvas_edges']}")
# item 4: mediapool parity
check("item4 mediapool 双端一致",
      web_state2["media_pool_items"] == desk_state["media_pool_items"],
      f"web={web_state2['media_pool_items']} desktop={desk_state['media_pool_items']}")
save("2-t2-results.json", results)
print("[t2 screenshots + dom done — interaction items next]")
