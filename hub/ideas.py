"""The idea backlog: ideas.json next to projects.json, gitignored.

Shared by the server (dashboard) and bin/idea.py (the /idea skill), so both
read and write the same file the same way.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import threading
from pathlib import Path

import scanner

IDEAS = Path(os.environ.get("DEV_HUB_IDEAS") or scanner.HUB / "ideas.json")
STAGES = ["idea", "exploring", "building", "shipped", "dropped"]
KINDS = ["personal", "work", "unknown"]
FIELDS = {"title", "note", "kind", "stage", "project"}

_lock = threading.Lock()


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load() -> list[dict]:
    try:
        return json.loads(IDEAS.read_text()).get("ideas", [])
    except (FileNotFoundError, ValueError):
        return []


def _save(ideas: list[dict]) -> None:
    tmp = IDEAS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"ideas": ideas}, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(IDEAS)


def _clean(field: str, value):
    if value is None:
        return None
    if field in ("title", "note", "project"):
        value = str(value).strip()
        if field == "title":
            value = " ".join(value.split())[:140]
            if not value:
                raise ValueError("An idea needs a title.")
        if field == "project" and value and not scanner.resolve(value):
            raise ValueError(f"No project folder called {value}.")
        return value or None
    if field == "kind":
        if value not in KINDS:
            raise ValueError("kind must be personal, work or unknown")
        return value
    if field == "stage":
        if value not in STAGES:
            raise ValueError(f"stage must be one of {', '.join(STAGES)}")
        return value
    raise ValueError(f"unknown field {field}")


def find(ideas: list[dict], ref: str) -> dict | None:
    ref = str(ref).lstrip("#")
    for i in ideas:
        if i["id"] == ref:
            return i
    return None


def add(title: str, note: str | None = None, kind: str = "unknown") -> dict:
    with _lock:
        ideas = load()
        idea = {
            "id": secrets.token_hex(3),
            "title": _clean("title", title),
            "note": _clean("note", note),
            "kind": _clean("kind", kind or "unknown"),
            "stage": "idea",
            "project": None,
            "created": _now(),
            "updated": _now(),
        }
        while find(ideas, idea["id"]):
            idea["id"] = secrets.token_hex(3)
        ideas.insert(0, idea)
        _save(ideas)
        return idea


def update(ref: str, **changes) -> dict:
    with _lock:
        ideas = load()
        idea = find(ideas, ref)
        if not idea:
            raise KeyError(f"No idea #{ref}")
        for field, value in changes.items():
            if field in FIELDS:
                idea[field] = _clean(field, value)
        if changes.get("project") and idea["stage"] in ("idea", "exploring"):
            idea["stage"] = "building"
        idea["updated"] = _now()
        _save(ideas)
        return idea


def delete(ref: str) -> None:
    with _lock:
        ideas = load()
        idea = find(ideas, ref)
        if not idea:
            raise KeyError(f"No idea #{ref}")
        ideas.remove(idea)
        _save(ideas)
