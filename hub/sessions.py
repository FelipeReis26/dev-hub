"""Claude Code sessions, matched to projects in ~/Desktop/Claude.

Two sources:

* The desktop app's session files (~/Library/Application Support/Claude/
  claude-code-sessions/<account>/<org>/local_*.json). These carry the title,
  folder, last activity and the app's own one-line status for the last turn
  ("postTurnSummary"), which is the "where you left off" line.
* Terminal sessions: transcripts in ~/.claude/projects/*/<uuid>.jsonl that no
  desktop session owns.

Transcripts are only ever read for their "cwd", "customTitle" and
"lastPrompt" fields, by regex over the last part of the file. Message content
is never parsed.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter
from pathlib import Path

import scanner

APP_SESSIONS = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
CLI_PROJECTS = Path.home() / ".claude" / "projects"
TAIL_BYTES = 768 * 1024

CWD_RE = re.compile(rb'"cwd":"((?:[^"\\]|\\.)*)"')
TITLE_RE = re.compile(rb'"customTitle":"((?:[^"\\]|\\.)*)"')
PROMPT_RE = re.compile(rb'"lastPrompt":"((?:[^"\\]|\\.)*)"')

_cache: dict[str, tuple[float, object]] = {}


def _cached(path: str, fn):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    value = fn(path)
    _cache[path] = (mtime, value)
    return value


def _json_str(raw: bytes) -> str:
    try:
        return json.loads(b'"' + raw + b'"')
    except ValueError:
        return raw.decode("utf-8", "replace")


def _tail(path: str) -> bytes:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        return f.read()


def _transcript_facts(path: str) -> dict:
    data = _tail(path)
    cwds = Counter(_json_str(m.group(1)) for m in CWD_RE.finditer(data))
    titles = TITLE_RE.findall(data)
    prompts = PROMPT_RE.findall(data)
    prompt = " ".join(_json_str(prompts[-1]).split()) if prompts else None
    if prompt and len(prompt) > 90:
        prompt = prompt[:87].rsplit(" ", 1)[0] + "…"
    return {"cwds": cwds, "title": _json_str(titles[-1]) if titles else None, "prompt": prompt}


def transcript_facts(path: str) -> dict | None:
    return _cached(path, _transcript_facts)


def project_for(path: str | None, names: set[str]) -> str | None:
    """Which top-level project folder a path is inside, following the old
    ~/Desktop/<name> symlinks."""
    if not path:
        return None
    try:
        real = Path(os.path.realpath(path))
        rel = real.relative_to(Path(os.path.realpath(scanner.ROOT)))
    except (ValueError, OSError):
        return None
    if not rel.parts:
        return None
    return rel.parts[0] if rel.parts[0] in names else None


def _title_match(title: str | None, names: set[str]) -> str | None:
    if not title:
        return None
    words = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "
    best = None
    for name in names:
        key = " " + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() + " "
        if len(key.strip()) >= 3 and key in words and (best is None or len(name) > len(best)):
            best = name
    return best


def _best_cwd_project(cwds: Counter, names: set[str]) -> str | None:
    tally: Counter = Counter()
    for cwd, n in cwds.items():
        proj = project_for(cwd, names)
        if proj:
            tally[proj] += n
    return tally.most_common(1)[0][0] if tally else None


def _cli_transcript(cli_id: str | None) -> str | None:
    if not cli_id or not re.fullmatch(r"[0-9a-f-]{36}", cli_id):
        return None
    hits = glob.glob(str(CLI_PROJECTS / "*" / f"{cli_id}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def _desktop_sessions() -> list[dict]:
    out = []
    for f in glob.glob(str(APP_SESSIONS / "*" / "*" / "local_*.json")):
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        summary = d.get("postTurnSummary") or {}
        out.append({
            "id": d.get("sessionId"),
            "cli_id": d.get("cliSessionId"),
            "source": "app",
            "title": d.get("title") or "Untitled session",
            "cwd": d.get("cwd") or d.get("originCwd"),
            "last": (d.get("lastActivityAt") or d.get("createdAt") or 0) / 1000,
            "archived": bool(d.get("isArchived")),
            "scheduled": bool(d.get("scheduledTaskId")),
            "status": summary.get("status_category"),
            "detail": summary.get("status_detail"),
            "needs": summary.get("needs_action") or None,
            "waiting": bool(d.get("pendingToolPermissions")) or summary.get("status_category") == "blocked",
        })
    return out


def _terminal_sessions(known_cli: set[str], app_titles: set[str]) -> list[dict]:
    out, seen = [], set()
    files = sorted(glob.glob(str(CLI_PROJECTS / "*" / "*.jsonl")), key=os.path.getmtime, reverse=True)
    for f in files:
        sid = Path(f).stem
        if sid in known_cli or sid in seen or not re.fullmatch(r"[0-9a-f-]{36}", sid):
            continue  # the old ~/Desktop/<name> symlink paths give some transcripts two copies
        seen.add(sid)
        if "-Desktop-" not in Path(f).parent.name:
            continue  # temp dirs, scratch workspaces, home
        facts = transcript_facts(f) or {}
        if facts.get("title") and facts["title"] in app_titles:
            continue  # an app session's earlier transcript (the app renames it)
        out.append({
            "id": sid,
            "cli_id": sid,
            "source": "terminal",
            "title": facts.get("title") or facts.get("prompt") or "Terminal session",
            "cwd": (facts.get("cwds") or Counter()).most_common(1)[0][0] if facts.get("cwds") else None,
            "cwds": facts.get("cwds"),
            "last": os.path.getmtime(f),
            "archived": False,
            "scheduled": False,
            "status": None, "detail": None, "needs": None, "waiting": False,
        })
    return out


def all_sessions(names: set[str]) -> list[dict]:
    desktop = _desktop_sessions()
    known_cli = {s["cli_id"] for s in desktop if s.get("cli_id")}
    sessions = desktop + _terminal_sessions(known_cli, {s["title"] for s in desktop})
    for s in sessions:
        # Where the work actually happened beats where the session started:
        # plenty start in ~/Desktop/Claude or GILD and cd into a project.
        cwds = s.pop("cwds", None)
        if cwds is None and s["source"] == "app":
            t = _cli_transcript(s["cli_id"])
            cwds = (transcript_facts(t) or {}).get("cwds") if t else None
        proj, how = _best_cwd_project(cwds or Counter(), names), "worked in"
        folder = project_for(s["cwd"], names)
        if folder and (not proj or proj == folder):
            proj, how = folder, "folder"
        if not proj:
            proj = _title_match(s["title"], names)
            how = "title"
        s.pop("cwds", None)
        s["project"] = proj
        s["matched_by"] = how if proj else None
    sessions.sort(key=lambda s: s["last"], reverse=True)
    return sessions


if __name__ == "__main__":
    names = {p.name for p in scanner.project_dirs()}
    for s in all_sessions(names):
        print(f"{s['source']:8} {str(s['project']):22} {str(s['matched_by']):9} "
              f"{'W' if s['waiting'] else ' '} {s['title'][:40]:40} {(s['detail'] or '')[:50]}")
