"""Scan ~/Desktop/Claude and describe every project in it.

Read-only. It looks at a handful of files per project (package.json, README.md,
CLAUDE.md, .claude/launch.json) and asks git for status. It never opens anything
else, so secrets lying around in a project folder stay unread.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(os.environ.get("DEV_HUB_ROOT") or Path.home() / "Desktop" / "Claude")
HUB = Path(__file__).resolve().parent.parent
OVERRIDES = Path(os.environ.get("DEV_HUB_OVERRIDES") or HUB / "projects.json")

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__",
    ".next", ".vercel", ".turbo", ".cache", "coverage", ".pytest_cache",
    ".run", ".supabase", "supabase/.temp",
}
WALK_LIMIT = 6000  # files per project when looking for the newest change

WORK_HINTS = re.compile(r"fanatics|betfanatics|\bFBG\b|\bBTS\b|\bOkta\b|Alveus", re.I)
PERSONAL_HINTS = re.compile(r"FelipeReis26|users\.noreply\.github\.com", re.I)


# ---------------------------------------------------------------- overrides

def load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_overrides(data: dict) -> None:
    tmp = OVERRIDES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(OVERRIDES)


# ---------------------------------------------------------------- helpers

def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def git_info(path: Path) -> dict | None:
    if not (path / ".git").exists():
        return None
    status = _git(path, "status", "--porcelain")
    last = _git(path, "log", "-1", "--format=%ct%x1f%s")
    counts = _git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    behind = ahead = None
    if counts:
        try:
            behind, ahead = (int(x) for x in counts.split())
        except ValueError:
            pass
    last_ts, last_msg = None, None
    if last and "\x1f" in last:
        ts, last_msg = last.split("\x1f", 1)
        last_ts = int(ts)
    return {
        "branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        "changes": len(status.splitlines()) if status else 0,
        "last_commit": last_ts,
        "last_message": last_msg,
        "remote": _git(path, "remote", "get-url", "origin"),
        "email": _git(path, "config", "user.email"),
        "ahead": ahead,
        "behind": behind,
        "worktree": (path / ".git").is_file(),
    }


def newest_change(path: Path) -> tuple[float, int]:
    """Newest file mtime under path, skipping build and dependency folders."""
    newest, seen = path.stat().st_mtime, 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f == ".DS_Store":
                continue
            seen += 1
            try:
                m = os.stat(os.path.join(dirpath, f)).st_mtime
            except OSError:
                continue
            newest = max(newest, m)
            if seen >= WALK_LIMIT:
                return newest, seen
    return newest, seen


def detect_stack(path: Path) -> list[str]:
    tags: list[str] = []
    pkg = _read_json(path / "package.json")
    if pkg is not None:
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        for dep, tag in (("next", "Next"), ("react", "React"), ("vue", "Vue"),
                         ("svelte", "Svelte"), ("vite", "Vite"),
                         ("typescript", "TypeScript"), ("@supabase/supabase-js", "Supabase")):
            if dep in deps:
                tags.append(tag)
        if not tags:
            tags.append("Node")
    names = {p.name for p in path.iterdir()} if path.is_dir() else set()
    if names & {"requirements.txt", "pyproject.toml", "setup.py"} or any(n.endswith(".py") for n in names):
        tags.append("Python")
    if "appsscript.json" in names or any(n.endswith(".gs") for n in names):
        tags.append("Apps Script")
    if "Dockerfile" in names:
        tags.append("Docker")
    if ".claude-plugin" in names:
        tags.append("Plugin")
    if not tags and any(n.endswith(".html") for n in names):
        tags.append("HTML")
    if not tags and any(n.endswith(".md") for n in names):
        tags.append("Docs")
    return tags


def detect_run(path: Path, ov: dict) -> dict | None:
    """Work out how to start the project. Overrides win, then launch.json,
    then package.json, then a static server for a folder with index.html."""
    if ov.get("run"):
        return {"shell": ov["run"], "port": ov.get("port"), "source": "hub"}
    launch = _read_json(path / ".claude" / "launch.json")
    if launch:
        for cfg in launch.get("configurations", []):
            exe = cfg.get("runtimeExecutable")
            if exe:
                return {
                    "argv": [exe, *cfg.get("runtimeArgs", [])],
                    "port": cfg.get("port"),
                    "source": "launch.json",
                    "label": cfg.get("name"),
                }
    pkg = _read_json(path / "package.json")
    if pkg:
        scripts = pkg.get("scripts", {})
        for script in ("dev", "start"):
            if script in scripts:
                return {"argv": ["npm", "run", script], "port": None, "source": "package.json"}
    if (path / "index.html").exists():
        return {"static": True, "port": None, "source": "index.html"}
    pages = [p for p in path.glob("*.html") if p.is_file()]
    if pages:
        newest = max(pages, key=lambda p: p.stat().st_mtime)
        return {"file": newest.name, "source": "html file"}
    return None


_MD_NOISE = re.compile(r"[*_`>#]|\[([^\]]*)\]\([^)]*\)")


def blurb(path: Path) -> str | None:
    for name in ("README.md", "CLAUDE.md", "SPEC.md"):
        try:
            text = (path / name).read_text(errors="ignore")[:6000]
        except OSError:
            continue
        for para in re.split(r"\n\s*\n", text):
            line = " ".join(para.split())
            if not line or line.startswith(("#", "|", "!", "<", "```", "---", "-", "*", ">")):
                continue
            clean = _MD_NOISE.sub(lambda m: m.group(1) or "", line).strip()
            if len(clean) < 25:
                continue
            return clean if len(clean) <= 180 else clean[:177].rsplit(" ", 1)[0] + "…"
    return None


def classify(path: Path, gi: dict | None, ov: dict) -> tuple[str, str]:
    if ov.get("kind") in ("work", "personal"):
        return ov["kind"], "set"
    if gi:
        joined = f"{gi.get('remote') or ''} {gi.get('email') or ''}"
        if PERSONAL_HINTS.search(joined):
            return "personal", "git"
        if WORK_HINTS.search(joined):
            return "work", "git"
    for name in ("CLAUDE.md", "README.md"):
        try:
            text = (path / name).read_text(errors="ignore")[:4000]
        except OSError:
            continue
        if WORK_HINTS.search(text):
            return "work", "guess"
    return "unknown", "none"


def live_url(path: Path, gi: dict | None, ov: dict) -> str | None:
    if ov.get("url"):
        return ov["url"]
    if gi and gi.get("remote"):
        m = re.search(r"[:/]([\w.-]+\.github\.io)(?:\.git)?$", gi["remote"], re.I)
        if m:
            return f"https://{m.group(1).lower()}"
    pkg = _read_json(path / "package.json")
    if pkg and str(pkg.get("homepage", "")).startswith("http"):
        return pkg["homepage"]
    return None


def nested_repos(path: Path) -> list[str]:
    out = []
    try:
        for child in sorted(path.iterdir()):
            if child.is_dir() and not child.is_symlink() and (child / ".git").exists():
                out.append(child.name)
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- scan

def describe(path: Path, overrides: dict) -> dict:
    ov = overrides.get(path.name, {})
    gi = git_info(path)
    nested = [] if gi else nested_repos(path)
    git_path = path
    if not gi and len(nested) == 1:
        git_path = path / nested[0]
        gi = git_info(git_path)
    kind, kind_source = classify(path, gi, ov)
    newest, files = newest_change(path)
    run = detect_run(path, ov)
    return {
        "name": path.name,
        "path": str(path),
        "kind": kind,
        "kind_source": kind_source,
        "stack": detect_stack(path),
        "blurb": ov.get("notes") or blurb(path),
        "touched": newest,
        "files": files,
        "git": gi,
        "git_path": str(git_path) if gi else None,
        "nested_repos": nested,
        "has_claude_md": (path / "CLAUDE.md").exists(),
        "run": run,
        "url": live_url(path, gi, ov),
        "hidden": bool(ov.get("hidden")),
        "pinned": bool(ov.get("pinned")),
        "created": ov.get("created"),
        "is_hub": path.resolve() == HUB,
        "overrides": ov,
    }


def project_dirs() -> list[Path]:
    out = []
    for entry in sorted(ROOT.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        out.append(entry)
    return out


def scan() -> list[dict]:
    overrides = load_overrides()
    dirs = project_dirs()
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda p: describe(p, overrides), dirs))


def resolve(name: str) -> Path | None:
    """A project folder by name, or None. Only direct children of ROOT."""
    if not name or "/" in name or name.startswith(".") or name in ("..",):
        return None
    p = ROOT / name
    if p.is_dir() and not p.is_symlink() and p.parent == ROOT:
        return p
    return None


if __name__ == "__main__":
    for proj in scan():
        print(f"{proj['name']:28} {proj['kind']:9} {','.join(proj['stack']):28} "
              f"{(proj['run'] or {}).get('source', '-')}")
