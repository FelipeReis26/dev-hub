#!/usr/bin/env python3
"""The idea backlog from the command line (used by the /idea skill).

    idea.py add "Title" [--note "more"] [--kind personal|work]
    idea.py list [--all] [--json]
    idea.py move <id> <idea|exploring|building|shipped|dropped>
    idea.py edit <id> [--title ..] [--note ..] [--kind ..]
    idea.py link <id> <project-folder>        # also moves it to building
    idea.py delete <id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))
import ideas  # noqa: E402


def show(i: dict) -> str:
    bits = [f"#{i['id']}", f"[{i['stage']}]", i["title"]]
    if i["kind"] != "unknown":
        bits.append(f"({i['kind']})")
    if i.get("project"):
        bits.append(f"→ {i['project']}")
    line = " ".join(bits)
    return line + (f"\n      {i['note']}" if i.get("note") else "")


def main() -> None:
    ap = argparse.ArgumentParser(description="Dev hub idea backlog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--note"); a.add_argument("--kind", default="unknown", choices=ideas.KINDS)
    l = sub.add_parser("list"); l.add_argument("--all", action="store_true"); l.add_argument("--json", action="store_true")
    m = sub.add_parser("move"); m.add_argument("id"); m.add_argument("stage", choices=ideas.STAGES)
    e = sub.add_parser("edit"); e.add_argument("id"); e.add_argument("--title"); e.add_argument("--note"); e.add_argument("--kind", choices=ideas.KINDS)
    k = sub.add_parser("link"); k.add_argument("id"); k.add_argument("project")
    d = sub.add_parser("delete"); d.add_argument("id")
    args = ap.parse_args()

    try:
        if args.cmd == "add":
            print(show(ideas.add(args.title, args.note, args.kind)))
        elif args.cmd == "list":
            items = ideas.load()
            if not args.all:
                items = [i for i in items if i["stage"] not in ("shipped", "dropped")]
            if args.json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                print("\n".join(show(i) for i in items) or "The backlog is empty.")
        elif args.cmd == "move":
            print(show(ideas.update(args.id, stage=args.stage)))
        elif args.cmd == "edit":
            changes = {k: v for k, v in (("title", args.title), ("note", args.note), ("kind", args.kind)) if v is not None}
            print(show(ideas.update(args.id, **changes)))
        elif args.cmd == "link":
            print(show(ideas.update(args.id, project=args.project)))
        elif args.cmd == "delete":
            ideas.delete(args.id)
            print(f"Deleted #{args.id.lstrip('#')}")
    except (KeyError, ValueError) as err:
        raise SystemExit(str(err).strip("'\""))


if __name__ == "__main__":
    main()
