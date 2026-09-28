#!/usr/bin/env python3
"""QA #13 T1c: lock competition + crash takeover (90s liveness).

Phase A (contention, live holder):
  1. probe account (JWT, fingerprint B) holds the lock as user-session.
  2. agent session (serve runtime, fingerprint A) is asked to write the same
     board -> must hit LockHeldError -> note row note_kind=lock with 画板正被占用
     + holder detail, and the assistant must TELL the user (no silent failure).
  3. reads still work while locked (agent can canvas_read_board).
  4. probe unlocks; agent retries the write -> succeeds.

Phase B (crash takeover):
  5. agent (serve runtime) acquires the lock mid-write... simulated by holding
     the lock via a SECOND serve runtime that we then KILL (SIGKILL -- no
     finally, no unlock), then time the takeover: a new applicant (probe JWT)
     must get 409 immediately (holder fresh), and after >=90s of holder
     silence must be granted the lock (200) with takeover.

Usage: python3 tools/qa_a3_t1c.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evidence", "qa-a3")
PORT = 8801
TOKEN = open("/tmp/qa-serve.env").read().strip().split("=", 1)[1]
BASE = f"http://127.0.0.1:{PORT}"

creds = {}
with open(os.path.expanduser("~/.skilyst/env")) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            creds[k] = v
API = creds.get("BEEHIVE_API", "https://beehive-api.verse4.pet")
WF = open("/tmp/qa_t1c_wf.txt").read().strip()
PJ = open("/tmp/qa_probe_jwt.txt").read().strip()

results: dict = {"checks": [], "workflow_id": WF}


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"[evidence] {name}")


def check(name: str, ok: bool, detail: str) -> None:
    results["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


def api(method: str, path: str, jwt: str, body: dict | None = None) -> tuple[int, dict]:
    r = urllib.request.Request(API + path, method=method,
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, {}


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
                         "stream": True, "max_turns": 10}).encode())
    cur = {"name": ""}

    def reader():
        with urllib.request.urlopen(r, timeout=1800) as resp:
            for raw in resp:
                line = raw.decode().strip()
                if line.startswith("event: "):
                    cur["name"] = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        row = json.loads(line[6:])
                    except Exception:
                        continue
                    rows.append({"event": cur["name"], "data": row})
                    if cur["name"] == "note":
                        print("  NOTE:", str(row.get("text"))[:130])

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while t.is_alive():
        t.join(10)
    done = next((x["data"] for x in rows if x.get("event") == "done"), None)
    return (done or {}).get("data") or {}, rows


# ---------------- Phase A: contention with a LIVE holder ---------------------
print("[A1] probe holds the lock")
st, body = api("POST", f"/api/v1/workflows/{WF}/lock", PJ,
               {"holder": {"kind": "user-session", "id": "qa-probe-holder"}})
check("A1 probe acquires lock", st == 200, f"HTTP {st} {json.dumps(body)[:120]}")
save("1c-01-probe-lock.json", {"status": st, "body": body})

print("[A2] agent tries to write the locked board")
t1, rows1 = sse_turn(None,
    f"在画板 {WF}（workflow id {WF}，共享板）上创建一个 script 节点，指令写'锁竞争测试'。"
    f"如果遇到锁冲突请如实告知用户。")
save("1c-02-agent-write-while-locked-sse.json", rows1)
sid = t1.get("session_id")
sess = req("GET", f"/session/{sid}")
msgs = sess.get("messages") or (sess.get("data") or {}).get("messages") or []
save("1c-03-session-messages.json", sess)
lock_notes = [m for m in msgs if m.get("role") == "note" and m.get("note_kind") == "lock"]
answers = [str(m.get("content")) for m in msgs if m.get("role") == "assistant"]
final_answer = answers[-1] if answers else ""
check("A2 lock-held note row (画板正被占用)", len(lock_notes) >= 1,
      f"lock notes={len(lock_notes)}" + (f" first={str(lock_notes[0].get('text'))[:150]}" if lock_notes else ""))
check("A2 note carries holder detail", any("qa-probe-holder" in str(n.get("text")) for n in lock_notes),
      "; ".join(str(n.get("text"))[:120] for n in lock_notes[:2]))
check("A2 agent tells the user (no silent failure)",
      ("占用" in final_answer or "锁" in final_answer or "locked" in final_answer.lower()),
      final_answer[:200])
# node must NOT have been created while locked
st, board = api("GET", f"/api/v1/workflows/{WF}", PJ)
nodes_now = len((board.get("payload") or board).get("nodes") or [])
check("A2 no write landed while locked", nodes_now == 0, f"nodes={nodes_now}")

print("[A3] reads still work while locked (implicit in A2's read_board notes)")
read_ok = any("canvas_read_board" in str(r["data"].get("text")) for r in rows1 if r.get("event") == "note")
check("A3 reads allowed under lock", read_ok, "canvas_read_board in notes")

print("[A4] probe releases; agent retries")
st, body = api("POST", f"/api/v1/workflows/{WF}/unlock", PJ, {"holder_id": "qa-probe-holder"})
check("A4 probe unlock", st == 200, f"HTTP {st} {json.dumps(body)[:80]}")
t2, rows2 = sse_turn(sid,
    f"锁应该已经释放了，请重试：在画板 {WF} 上创建那个 script 节点（指令'锁竞争测试'）。")
save("1c-04-agent-retry-after-unlock-sse.json", rows2)
st, board = api("GET", f"/api/v1/workflows/{WF}", PJ)
nodes_after = (board.get("payload") or board).get("nodes") or []
check("A4 write lands after unlock", len(nodes_after) == 1,
      f"nodes={[n.get('key') for n in nodes_after]}")

# ---------------- Phase B: crash takeover -------------------------------------
print("[B1] second serve runtime acquires the lock, then we SIGKILL it")
PORT2 = 8802
TOKEN2 = f"qa-t1c-crash-{int(time.time())}"
log2 = os.path.join(OUT, "1c-05-crash-serve.jsonl")
proc2 = subprocess.Popen(
    [os.path.join(ROOT, "bin", "skilyst"), "serve", "--live",
     "--skill", "skilyst/canvas-ops", "--port", str(PORT2), "--token", TOKEN2],
    cwd=ROOT, stdout=open(log2, "w"), stderr=subprocess.STDOUT,
    env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")})
for _ in range(200):
    if os.path.exists(log2) and os.path.getsize(log2):
        try:
            ready = json.loads(open(log2).readline())
            if ready.get("event") == "ready":
                break
        except Exception:
            pass
    time.sleep(0.1)
# the agent on runtime 2 does a board write -> acquires the lock as
# agent-session; we kill the process MID-write via a lock+sleep trick: simpler
# and equivalent -- have the agent do a write, then kill before its unlock
# retry window. The write completes fast, so instead we grab the lock DIRECTLY
# from runtime 2's session by sending a message that triggers lock -> and
# SIGKILL the process the moment the lock is held. The canvas write lock is
# held only during the write; to crash WITH the lock held we instead hold the
# lock via a runtime-2 agent turn that we interrupt: use a long instruction
# turn and kill as soon as the server-side lock exists.
# Simpler deterministic approach: kill runtime 2 right after it acquires the
# lock from a write we initiate; the lock is released at write end. To hold it
# across the kill we rely on the tool sequence: ask for a MULTI-step edit
# (create + config + connect) and kill after the first note.
stop = threading.Event()
kill_at = None


def watch_and_kill():
    """Kill proc2 as soon as its agent holds the lock (first canvas note)."""
    global kill_at
    while not stop.is_set():
        time.sleep(0.05)
        st2, b2 = api("POST", f"/api/v1/workflows/{WF}/lock", PJ,
                      {"holder": {"kind": "user-session", "id": "takeover-probe"}})
        if st2 == 409:
            # someone (runtime 2's agent) holds it -> kill NOW, mid-sequence
            proc2.kill()
            kill_at = time.time()
            stop.set()
            return
        elif st2 == 200:
            # we got it ourselves (agent not there yet) -- release and retry
            api("POST", f"/api/v1/workflows/{WF}/unlock", PJ, {"holder_id": "takeover-probe"})


wk = threading.Thread(target=watch_and_kill, daemon=True)
wk.start()

r2 = urllib.request.Request(f"http://127.0.0.1:{PORT2}/message", method="POST",
    headers={"Authorization": f"Bearer {TOKEN2}", "Content-Type": "application/json",
             "Accept": "text/event-stream"},
    data=json.dumps({"message":
        f"在画板 {WF}（workflow id {WF}）上依次完成三步：创建一个 script 节点（指令'崩溃接管测试'），"
        f"再创建一个图片生成节点，最后把两个节点连起来。每步之间不要停。",
        "stream": True, "max_turns": 20}).encode())
crash_rows: list[dict] = []
cur = {"name": ""}


def reader2():
    with urllib.request.urlopen(r2, timeout=1800) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("event: "):
                cur["name"] = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    row = json.loads(line[6:])
                except Exception:
                    continue
                crash_rows.append({"event": cur["name"], "data": row})
                if cur["name"] == "note":
                    print("  NOTE(r2):", str(row.get("text"))[:120])


t2 = threading.Thread(target=reader2, daemon=True)
t2.start()
while t2.is_alive() and not stop.is_set():
    time.sleep(0.2)
proc2.kill()  # ensure dead
proc2.wait(timeout=30)
wk.join(timeout=5)
save("1c-05-crash-serve.jsonl-notes.json",
     {"killed_at": kill_at, "rows_tail": crash_rows[-10:]})
print(f"[B1] runtime 2 killed at {kill_at}")

# lock state now: held by a dead holder (fingerprint A, agent-session)
st, body = api("POST", f"/api/v1/workflows/{WF}/lock", PJ,
               {"holder": {"kind": "user-session", "id": "takeover-probe"}})
t_immediate = time.time()
check("B2 fresh-dead holder: immediate applicant gets 409 (not yet stale)",
      st == 409, f"HTTP {st} at +{t_immediate-(kill_at or t_immediate):.1f}s {json.dumps(body)[:150]}")

print("[B3] waiting out the 90s liveness window for takeover…")
deadline = time.time() + 150
granted_at = None
while time.time() < deadline:
    st, body = api("POST", f"/api/v1/workflows/{WF}/lock", PJ,
                   {"holder": {"kind": "user-session", "id": "takeover-probe"}})
    if st == 200:
        granted_at = time.time()
        break
    time.sleep(5)
elapsed = (granted_at - kill_at) if granted_at else None
check("B3 dead holder's lock taken over by next applicant", granted_at is not None,
      f"granted after {elapsed:.0f}s (kill->grant) HTTP {st}")
check("B3 takeover within 90s+liveness margin", elapsed is not None and 85 <= elapsed <= 130,
      f"elapsed={elapsed}")
save("1c-06-takeover.json", {"killed_at": kill_at, "granted_at": granted_at,
                             "elapsed_s": elapsed, "status": st, "body": body})

# release for cleanliness
api("POST", f"/api/v1/workflows/{WF}/unlock", PJ, {"holder_id": "takeover-probe"})
save("1c-7-results.json", results)
print("[t1c done]")
