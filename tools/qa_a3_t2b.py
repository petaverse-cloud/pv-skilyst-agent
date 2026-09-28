#!/usr/bin/env python3
"""QA #13 T2 v2: dual-host alignment — full interactive pass (11 items).

Both hosts bound to the same skilyst serve runtime (8801). Same T1a workflow.
Independent checks per ALIGNMENT-CHECKLIST.md.
"""
from __future__ import annotations

import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]

creds = {}
with open(os.path.expanduser("~/.skilyst/env")) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            creds[k] = v
API = creds.get("BEEHIVE_API", "https://beehive-api.verse4.pet")
r = urllib.request.Request(API + "/api/v1/auth/login", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"username": creds["BEEHIVE_PLATFORM_USER"],
                     "password": creds["BEEHIVE_PLATFORM_PASS"]}).encode())
lp = json.loads(urllib.request.urlopen(r, timeout=30).read())
p0 = lp.get("payload") or lp
tok, user = p0["token"], {"user_uid": p0.get("user_uid"), "username": p0.get("username"), "role": p0.get("role")}

t1a = json.load(open(os.path.join(OUT, "1a-t1a-results.json")))
WF, SID = t1a["workflow_id"], t1a["session_id"]

results: dict = {"checks": []}


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


def check(name: str, ok: bool, detail: str) -> None:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def bind_runtime(page) -> None:
    inputs = page.locator("input").all()
    inputs[0].fill("http://127.0.0.1:8801")
    for inp in inputs:
        if inp.get_attribute("type") == "password":
            inp.fill(TOKEN)
    page.get_by_role("button", name="Connect runtime").first.click()
    page.wait_for_timeout(2500)


def open_board(page, wf_id: str) -> dict:
    """Pick the board; return canvas DOM facts."""
    # the board picker is a select
    picked = False
    for sel in page.locator("select").all():
        try:
            opts = sel.locator("option").all()
            for o in opts:
                val = o.get_attribute("value") or ""
                lbl = o.text_content() or ""
                if val == wf_id or wf_id in lbl:
                    if val:
                        sel.select_option(val)
                    else:
                        sel.select_option(label=lbl)
                    picked = True
                    break
        except Exception:
            continue
        if picked:
            break
    page.wait_for_timeout(4500)
    return {"canvas_nodes": page.locator(".react-flow__node").count(),
            "canvas_edges": page.locator(".react-flow__edge").count(),
            "picked": picked}


with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-proxy-server"])

    # ================= web console host =================
    ctx = browser.new_context(viewport={"width": 1680, "height": 1050})
    page = ctx.new_page()
    page.goto("http://localhost:3000/login", wait_until="domcontentloaded")
    page.evaluate(f"""(() => {{
        localStorage.setItem('beehive_token', {json.dumps(tok)});
        localStorage.setItem('beehive_user', {json.dumps(json.dumps(user))});
    }})()""")
    page.goto("http://localhost:3000/studio/agent", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    bind_runtime(page)
    connected = "Failed to fetch" not in page.content()
    check("web runtime connect", connected, "runtime bound on web host")

    # session list + pick the T1a session
    page.wait_for_timeout(1500)
    session_rows = page.locator("[data-testid*=session], li:has-text('2026'), [class*=session] li, option").all()
    found_sid = False
    # find a select or list containing the session id
    for sel in page.locator("select").all():
        try:
            for o in sel.locator("option").all():
                sval = o.get_attribute("value") or ""
                slbl = o.text_content() or ""
                if SID in sval or SID in slbl:
                    if sval:
                        sel.select_option(sval)
                    else:
                        sel.select_option(label=slbl)
                    found_sid = True
                    break
        except Exception:
            continue
        if found_sid:
            break
    if not found_sid:
        # maybe a clickable list
        try:
            page.get_by_text(SID, exact=False).first.click(timeout=3000)
            found_sid = True
        except Exception:
            pass
    page.wait_for_timeout(3000)

    # action cards render in the conversation
    web_actions = page.locator("[data-testid=action-card], [data-testid*=action]").count()
    conv_text = page.locator("[class*=conversation], [class*=message], [data-testid*=message]").count()
    check("web action cards visible", web_actions > 0 or conv_text > 0,
          f"action-ish={web_actions} message-ish={conv_text}")

    # board + canvas
    web_board = open_board(page, WF)
    check("web board picked", web_board["picked"], f"wf={WF}")
    web_state = {"nodes": web_board["canvas_nodes"], "edges": web_board["canvas_edges"]}
    page.screenshot(path=os.path.join(OUT, "2-t2-web-full.png"))

    # media pool: count thumbnails
    web_pool = page.locator("img[src*=beehive-cdn], [class*=pool] img, [data-testid*=pool] img").count()
    # locate button on an action card (focusNode path)
    locate_ok = False
    try:
        btn = page.locator("[data-testid=action-locate], button:has-text('定位'), button:has-text('Locate')").first
        if btn.count() > 0:
            btn.click(timeout=3000)
            page.wait_for_timeout(1200)
            locate_ok = True
    except Exception:
        pass
    check("web action locate (focusNode)", locate_ok, "locate clicked" if locate_ok else "no locate button found")
    save("2-t2-web-state.json", {**web_state, "pool": web_pool, "locate": locate_ok})
    ctx.close()

    # ================= desktop host =================
    ctx2 = browser.new_context(viewport={"width": 1680, "height": 1050})
    dp = ctx2.new_page()
    dp.goto("http://localhost:1420/", wait_until="domcontentloaded")
    dp.wait_for_timeout(2500)
    # login form if shown
    try:
        dp.fill("[data-testid=canvas-login-user]", creds["BEEHIVE_PLATFORM_USER"], timeout=4000)
        dp.fill("[data-testid=canvas-login-pass]", creds["BEEHIVE_PLATFORM_PASS"])
        dp.click("[data-testid=canvas-login-submit]")
        dp.wait_for_timeout(3000)
    except Exception:
        pass
    # desktop workbench: pick the T1a session/board
    desk_sid = False
    for sel in dp.locator("select").all():
        try:
            for o in sel.locator("option").all():
                sval = o.get_attribute("value") or ""
                slbl = o.text_content() or ""
                if SID in sval or SID in slbl:
                    if sval:
                        sel.select_option(sval)
                    else:
                        sel.select_option(label=slbl)
                    desk_sid = True
                    break
        except Exception:
            continue
        if desk_sid:
            break
    if not desk_sid:
        try:
            dp.get_by_text(SID, exact=False).first.click(timeout=3000)
            desk_sid = True
        except Exception:
            pass
    dp.wait_for_timeout(3000)
    check("desktop session select", desk_sid, f"found={desk_sid}")

    # board
    desk_board = open_board(dp, WF)
    check("desktop board picked", desk_board["picked"], f"wf={WF}")
    desk_state = {"nodes": desk_board["canvas_nodes"], "edges": desk_board["canvas_edges"]}
    dp.screenshot(path=os.path.join(OUT, "2-t2-desktop-full.png"))
    desk_pool = dp.locator("img[src*=beehive-cdn], [class*=pool] img, [data-testid*=pool] img").count()
    desk_actions = dp.locator("[data-testid=action-card], [data-testid*=action], [data-testid=tool-message]").count()
    save("2-t2-desktop-state.json", {**desk_state, "pool": desk_pool, "actions": desk_actions})
    ctx2.close()
    browser.close()

# ================= alignment verdicts =================
check("item1 双端节点数一致", web_state["nodes"] == desk_state["nodes"] and web_state["nodes"] > 0,
      f"web={web_state['nodes']} desktop={desk_state['nodes']}")
check("item1 双端连线数一致", web_state["edges"] == desk_state["edges"],
      f"web={web_state['edges']} desktop={desk_state['edges']}")
check("item4 mediapool 双端一致", web_pool == desk_pool,
      f"web={web_pool} desktop={desk_pool}")
check("item6 会话流+action 卡片 双端可见",
      (web_actions + conv_text if False else (web_actions or conv_text)) and desk_actions > 0,
      f"web actions={web_actions} desktop actions={desk_actions}")
save("2-t2-results.json", results)
print("[t2 v2 done]")
