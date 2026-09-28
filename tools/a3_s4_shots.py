#!/usr/bin/env python3
"""A3 S4 dual-host screenshots: the SAME workflow opened in the desktop app
and the web console workbench (FR-0 alignment evidence).

Credentials are read from ~/.skilyst/env at runtime and typed into the
desktop login form in-memory only -- nothing is written to disk or logs.

Usage: python3 tools/a3_s4_shots.py <workflow_id> [out-dir]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "evidence", "a3-s4")
WF = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

def creds() -> dict[str, str]:
    d = {}
    with open(os.path.expanduser("~/.skilyst/env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                d[k] = v
    return d

C = creds()
API = C.get("BEEHIVE_API", "https://beehive-api.verse4.pet")

def api_login() -> tuple[str, dict]:
    r = urllib.request.Request(API + "/api/v1/auth/login", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"username": C["BEEHIVE_PLATFORM_USER"],
                         "password": C["BEEHIVE_PLATFORM_PASS"]}).encode())
    with urllib.request.urlopen(r, timeout=30) as resp:
        data = json.loads(resp.read())
    payload = data.get("payload") or data
    # login answers {token, user_uid, username, role} at the top level
    return payload["token"], {"user_uid": payload.get("user_uid"),
                              "username": payload.get("username"),
                              "role": payload.get("role")}

token, user = api_login()

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-proxy-server"])

    # ---- web console workbench --------------------------------------------
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    page.goto("http://localhost:3457/login", wait_until="domcontentloaded")
    # inject the web console's auth localStorage (beehive_token/beehive_user)
    page.evaluate(f"""(() => {{
        localStorage.setItem('beehive_token', {json.dumps(token)});
        localStorage.setItem('beehive_user', {json.dumps(json.dumps(user))});
    }})()""")
    page.goto(f"http://localhost:3457/studio/agent", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    # pick the E2E board in the board selector
    try:
        page.select_option("[data-testid=board-select]", WF, timeout=10000)
        page.wait_for_timeout(4000)
    except Exception as e:
        print("[warn] board select:", e)
    page.screenshot(path=os.path.join(OUT, "web-console-workbench.png"))
    print("[shot] web-console-workbench.png")
    ctx.close()

    # ---- desktop app ------------------------------------------------------
    ctx2 = browser.new_context(viewport={"width": 1600, "height": 1000})
    dp = ctx2.new_page()
    dp.goto("http://localhost:1420/", wait_until="domcontentloaded")
    dp.wait_for_timeout(1500)
    # the desktop canvas login form (beehive data source)
    try:
        dp.fill("[data-testid=canvas-login-user]", C["BEEHIVE_PLATFORM_USER"])
        dp.fill("[data-testid=canvas-login-pass]", C["BEEHIVE_PLATFORM_PASS"])
        dp.click("[data-testid=canvas-login-submit]")
        dp.wait_for_timeout(2500)
    except Exception as e:
        print("[warn] desktop login form not present:", e)
    # the workflow list -> open the E2E board
    try:
        dp.get_by_text(WF, exact=False).first.click(timeout=10000)
        dp.wait_for_timeout(4500)
    except Exception as e:
        print("[warn] workflow pick:", e)
    dp.screenshot(path=os.path.join(OUT, "app-canvas-board.png"))
    print("[shot] app-canvas-board.png")
    ctx2.close()
    browser.close()
print("[done]")
