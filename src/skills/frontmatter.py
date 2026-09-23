"""Minimal YAML frontmatter parser for SKILL.md (community-compatible subset).

The community spec (agentskills.io) puts `name`/`description` (+ optional
`license`, `compatibility`, `metadata` string->string map, `allowed-tools`) in
the YAML frontmatter. Community packages in the wild also carry host dialects
(Claude Code: `tags`, `user-invocable`, `version`) -- unknown keys MUST be
tolerated, never rejected.

Scope: line-based parser covering the YAML the community actually emits --
plain/quoted scalars, folded (`>`) and literal (`|`) block scalars, one nested
mapping level with string values, and scalar lists. No anchors, no multi-doc,
no flow collections beyond simple inline lists.
"""
from __future__ import annotations

import re

KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s*(.*))?$")
KNOWN_FRONTMATTER = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")


class FrontmatterError(ValueError):
    pass


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, body). Raises if there is no frontmatter block."""
    if not text.startswith("---"):
        raise FrontmatterError("SKILL.md has no YAML frontmatter block (must start with ---)")
    lines = text.splitlines()
    if lines[0].strip() != "---":
        raise FrontmatterError("frontmatter must start on line 1 with ---")
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise FrontmatterError("frontmatter block is not closed with ---")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        quote, body = value[0], value[1:-1]
        if quote == '"':
            body = body.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
        else:
            body = body.replace("''", "'")
        return body
    return value


def _strip_inline_comment(value: str) -> str:
    if value.startswith(("\"", "'")):
        return value
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


def _inline_list(value: str) -> list[str] | None:
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_unquote(p) for p in inner.split(",")]
    return None


def parse_frontmatter(text: str) -> dict:
    """Parse the frontmatter mapping into a dict (values are str/list/dict)."""
    fm, _ = split_frontmatter(text)
    return parse_yaml_subset(fm)


def parse_yaml_subset(fm: str) -> dict:
    out: dict = {}
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent != 0:
            # stray indented line outside a block we consumed -- tolerate it
            continue
        m = KEY_RE.match(raw.strip())
        if not m:
            raise FrontmatterError(f"unparseable frontmatter line: {raw!r}")
        key, rest = m.group(1), (m.group(2) or "").strip()

        if rest in (">", ">-", ">+", "|", "|-", "|+"):
            folded = rest.startswith(">")
            chunk: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= indent:
                    break
                chunk.append(nxt)
                i += 1
            body = "\n".join(c[indent + 2:] if len(c) > indent + 2 else "" for c in chunk)
            if folded:
                body = re.sub(r"\n(?=[^\s])", " ", body)
            out[key] = body.strip()
            continue

        if rest == "":
            # nested mapping (metadata:) or nested list
            items: dict = {}
            seq: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                nindent = len(nxt) - len(nxt.lstrip(" "))
                if nindent <= indent:
                    break
                stripped = nxt.strip()
                i += 1
                if stripped.startswith("- "):
                    seq.append(_unquote(_strip_inline_comment(stripped[2:])))
                    continue
                nm = KEY_RE.match(stripped)
                if not nm:
                    raise FrontmatterError(f"unparseable nested line: {nxt!r}")
                items[nm.group(1)] = _unquote(_strip_inline_comment(nm.group(2) or ""))
            if seq and not items:
                out[key] = seq
            elif items and not seq:
                out[key] = items
            elif not items and not seq:
                out[key] = ""
            else:
                raise FrontmatterError(f"mixed list/mapping under {key!r} is not supported")
            continue

        inline = _inline_list(rest)
        out[key] = inline if inline is not None else _unquote(_strip_inline_comment(rest))
    return out
