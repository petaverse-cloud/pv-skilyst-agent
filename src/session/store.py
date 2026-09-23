"""Session store: one directory per conversation, append-only transcript.

Layout (``~/.skilyst/sessions`` by default):

    <session-id>/
      session.json     metadata (model, workspace, skills, usage, status)
      messages.jsonl   append-only OpenAI-shaped transcript
      trace.jsonl      loop trace: tool executions, route attempts, refusals
      artifacts.json   produced artifacts (job id, URL, verification result)

Why append-only JSONL rather than a database: a session is a transcript, the
crash-recovery property we need is "whatever was said is still on disk", and a
half-written last line is recoverable by ignoring it. Metadata is rewritten
atomically (tmp + rename) because it is small and always superseded.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MESSAGES = "messages.jsonl"
TRACE = "trace.jsonl"
META = "session.json"
ARTIFACTS = "artifacts.json"


def new_session_id(now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                      # a torn final line from a hard crash: drop it, keep the rest
    return out


def _write_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Session:
    session_id: str
    path: Path
    meta: dict = field(default_factory=dict)

    # -- transcript ---------------------------------------------------------
    @property
    def messages(self) -> list[dict]:
        return _read_jsonl(self.path / MESSAGES)

    @property
    def trace(self) -> list[dict]:
        return _read_jsonl(self.path / TRACE)

    def history(self) -> list[dict]:
        """Transcript in the shape the chat API expects (internal keys stripped)."""
        out = []
        for row in self.messages:
            msg = {k: v for k, v in row.items()
                   if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
            if msg.get("role") == "tool":
                msg.setdefault("name", row.get("name", "tool"))
            out.append(msg)
        return out

    def append_message(self, role: str, content: str = "", **extra) -> dict:
        row = {"seq": self.meta.get("message_count", 0), "ts": time.time(), "role": role,
               "content": content}
        row.update({k: v for k, v in extra.items() if v is not None})
        _append_jsonl(self.path / MESSAGES, row)
        self.meta["message_count"] = row["seq"] + 1
        self.touch()
        return row

    def append_trace(self, kind: str, **fields) -> dict:
        row = {"ts": time.time(), "kind": kind}
        row.update(fields)
        _append_jsonl(self.path / TRACE, row)
        return row

    def record_artifact(self, artifact: dict) -> None:
        rows = self.artifacts
        rows.append({**artifact, "ts": time.time()})
        _write_atomic(self.path / ARTIFACTS, {"artifacts": rows})
        self.meta["artifacts"] = len(rows)
        self.touch()

    @property
    def artifacts(self) -> list[dict]:
        path = self.path / ARTIFACTS
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("artifacts", [])

    # -- metadata -----------------------------------------------------------
    def update(self, **fields) -> None:
        self.meta.update(fields)
        self.touch()

    def add_usage(self, usage: dict) -> None:
        total = self.meta.setdefault("usage", {"prompt_tokens": 0, "completion_tokens": 0,
                                               "total_tokens": 0, "calls": 0})
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            total[key] = int(total.get(key, 0)) + int(usage.get(key) or 0)
        models = set(total.get("models_used") or [])
        models.update(usage.get("models_used") or [])
        total["models_used"] = sorted(models)
        self.touch()

    def touch(self) -> None:
        self.meta["updated_at"] = time.time()
        _write_atomic(self.path / META, self.meta)

    def summary(self) -> dict:
        return {"session_id": self.session_id, "title": self.meta.get("title", ""),
                "status": self.meta.get("status", "open"), "model": self.meta.get("model", ""),
                "messages": self.meta.get("message_count", 0),
                "artifacts": len(self.artifacts),
                "created_at": self.meta.get("created_at"), "updated_at": self.meta.get("updated_at")}


class SessionStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, title: str = "", model: str = "", workspace: str = "",
               skills: list[str] | None = None, session_id: str | None = None) -> Session:
        sid = session_id or new_session_id()
        path = self.root / sid
        path.mkdir(parents=True, exist_ok=True)
        meta = {"session_id": sid, "title": title, "created_at": time.time(), "updated_at": time.time(),
                "model": model, "workspace": workspace, "skills": skills or [], "status": "open",
                "message_count": 0, "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                                              "total_tokens": 0, "calls": 0}}
        session = Session(session_id=sid, path=path, meta=meta)
        session.touch()
        return session

    def open(self, session_id: str) -> Session:
        path = self.root / session_id
        if not (path / META).is_file():
            raise FileNotFoundError(f"session {session_id!r} not found under {self.root}")
        return Session(session_id=session_id, path=path,
                       meta=json.loads((path / META).read_text(encoding="utf-8")))

    def list(self) -> list[dict]:
        rows = []
        for child in sorted(self.root.iterdir()):
            if (child / META).is_file():
                meta = json.loads((child / META).read_text(encoding="utf-8"))
                rows.append({"session_id": child.name, "title": meta.get("title", ""),
                             "status": meta.get("status", "open"), "model": meta.get("model", ""),
                             "messages": meta.get("message_count", 0),
                             "artifacts": len(_read_jsonl(child / ARTIFACTS) and
                                              json.loads((child / ARTIFACTS).read_text())["artifacts"]),
                             "updated_at": meta.get("updated_at")})
        return sorted(rows, key=lambda r: r.get("updated_at") or 0, reverse=True)

    def latest(self) -> Session | None:
        rows = self.list()
        return self.open(rows[0]["session_id"]) if rows else None
