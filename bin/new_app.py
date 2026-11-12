#!/usr/bin/env python3
"""Scaffold a new project in ~/Desktop/Claude.

    python3 bin/new_app.py <name> --kind personal|work --template static|vite-react|python|blank \
        --description "One line on what it is" [--title "Nice Name"] [--idea <id>] [--dry-run]

Creates the folder, fills in the template, writes CLAUDE.md, README.md,
.gitignore and .claude/launch.json (with a port nothing else uses), runs
`git init -b main`, sets the personal git identity for personal projects, and
registers the project with the dev hub. It doesn't commit, install, create a
remote or push: the /new-app skill does those steps with Felipe.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HUB / "hub"))
import ideas  # noqa: E402
import scanner  # noqa: E402

TEMPLATES = HUB / "templates"
ROOT = scanner.ROOT
PERSONAL_NAME = "Felipe Reis"
PERSONAL_EMAIL = "78143596+FelipeReis26@users.noreply.github.com"
RESERVED_PORTS = {3000, 4747, 5000, 5173, 7000, 8000, 8080, 8090, 8888}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")


def templates() -> dict[str, dict]:
    out = {}
    for d in sorted(TEMPLATES.iterdir()):
        meta = d / "template.json"
        if d.is_dir() and meta.exists():
            out[d.name] = json.loads(meta.read_text())
    return out


def used_ports() -> set[int]:
    ports = set(RESERVED_PORTS)
    launch_files = [ROOT / ".claude" / "launch.json", *ROOT.glob("*/.claude/launch.json")]
    for f in launch_files:
        try:
            for cfg in json.loads(f.read_text()).get("configurations", []):
                if cfg.get("port"):
                    ports.add(int(cfg["port"]))
                for arg in cfg.get("runtimeArgs", []):
                    if str(arg).isdigit() and 1000 < int(arg) < 65536:
                        ports.add(int(arg))
        except (OSError, ValueError):
            continue
    for entry in scanner.load_overrides().values():
        if entry.get("port"):
            ports.add(int(entry["port"]))
    return ports


def pick_port(start: int = 5200) -> int:
    taken = used_ports()
    for port in range(start, start + 800):
        if port in taken:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise SystemExit("no free port found")


def escape_for(path: Path, text: str) -> str:
    ext = path.suffix
    if ext in (".html", ".tsx", ".jsx"):
        text = html.escape(text, quote=True)
        if ext != ".html":
            text = text.replace("{", "&#123;").replace("}", "&#125;")
    elif ext == ".py":
        text = text.replace("\\", "\\\\").replace('"', '\\"')
    elif ext == ".json":
        text = json.dumps(text)[1:-1]
    return text


def fill(text: str, values: dict, path: Path) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            return m.group(0)
        value = str(values[key])
        return value if key in ("accounts", "run_notes") else escape_for(path, value)
    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def main() -> None:
    tpls = templates()
    ap = argparse.ArgumentParser(description="Scaffold a project in ~/Desktop/Claude")
    ap.add_argument("name", help="folder name, kebab-case")
    ap.add_argument("--kind", required=True, choices=["personal", "work"])
    ap.add_argument("--template", required=True, choices=sorted(tpls))
    ap.add_argument("--description", required=True)
    ap.add_argument("--title")
    ap.add_argument("--idea", help="backlog idea id to link to the new project")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-templates", action="store_true")
    a = ap.parse_args()

    if not NAME_RE.match(a.name):
        raise SystemExit(f"'{a.name}' isn't a usable folder name. Use kebab-case, like trip-budget.")
    target = ROOT / a.name
    if target.exists() or target.is_symlink():
        raise SystemExit(f"{target} already exists. Pick another name.")

    tpl = tpls[a.template]
    description = " ".join(a.description.split())
    title = a.title or a.name.replace("-", " ").title()
    port = pick_port() if tpl.get("launch") else None
    values = {
        "name": a.name, "title": title, "description": description, "port": port or "",
        "date": dt.date.today().strftime("%-d %b %Y"), "template": tpl["label"],
    }
    values["accounts"] = fill((TEMPLATES / "_common" / f"accounts-{a.kind}.md").read_text(), values, Path("x.md")).strip()
    values["run_notes"] = fill(tpl.get("run_notes", ""), values, Path("x.md"))

    plan: dict[Path, str] = {}
    src_dir = TEMPLATES / a.template
    for src in sorted(src_dir.rglob("*")):
        if src.is_file() and src.name != "template.json" and src.name != ".DS_Store":
            rel = src.relative_to(src_dir)
            plan[target / rel] = fill(src.read_text(), values, src)
    for name in ("CLAUDE.md", "README.md"):
        plan[target / name] = fill((TEMPLATES / "_common" / name).read_text(), values, Path(name))
    plan[target / ".gitignore"] = "\n".join(tpl.get("gitignore", [".DS_Store"])) + "\n"
    if tpl.get("launch"):
        launch = json.loads(fill(json.dumps(tpl["launch"]), values, Path("x.json")))
        cfg = {"name": f"{a.name}-dev", **launch, "port": port}
        plan[target / ".claude" / "launch.json"] = json.dumps(
            {"version": "0.0.1", "configurations": [cfg]}, indent=2) + "\n"

    if a.dry_run:
        print(f"Would create {target} ({a.kind}, {a.template}, port {port or 'none'}):")
        for p in plan:
            print("  ", p.relative_to(ROOT))
        return

    for path, content in plan.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    git(target, "init", "-b", "main")
    if a.kind == "personal":
        git(target, "config", "user.name", PERSONAL_NAME)
        git(target, "config", "user.email", PERSONAL_EMAIL)

    data = scanner.load_overrides()
    entry = data.get(a.name, {})
    entry.update({"kind": a.kind, "created": dt.date.today().isoformat(), "template": a.template})
    if tpl.get("run"):
        entry["run"] = tpl["run"]
    data[a.name] = entry
    scanner.save_overrides(data)

    linked = None
    if a.idea:
        try:
            linked = ideas.update(a.idea, project=a.name)["id"]
        except (KeyError, ValueError) as err:
            print(f"warning: couldn't link idea {a.idea}: {err}", file=sys.stderr)

    email = subprocess.run(["git", "-C", str(target), "config", "user.email"],
                           capture_output=True, text=True).stdout.strip()
    print(json.dumps({
        "path": str(target),
        "kind": a.kind,
        "template": a.template,
        "port": port,
        "git_email": email,
        "install": tpl.get("install"),
        "idea": linked,
        "files": [str(p.relative_to(target)) for p in plan],
    }, indent=2))


if __name__ == "__main__":
    main()
