"""Dev hub: a local dashboard for everything in ~/Desktop/Claude.

    python3 hub/server.py          # http://127.0.0.1:4747

Standard library only. Listens on 127.0.0.1, rejects any Host header that is
not this machine (DNS rebinding), and every POST needs an X-Dev-Hub header,
which a page on another site can't send without a CORS preflight this server
never answers.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scanner  # noqa: E402

PORT = int(os.environ.get("DEV_HUB_PORT", "4747"))
STATIC = Path(__file__).resolve().parent / "static"
RUN_DIR = scanner.HUB / ".run"
RUN_DIR.mkdir(exist_ok=True)

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
URL_IN_LOG = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d+)[^\s\x1b]*")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

ENV = {
    **os.environ,
    "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin"),
    "BROWSER": "none",
    "FORCE_COLOR": "0",
    "NO_COLOR": "1",
}

_lock = threading.Lock()


# ---------------------------------------------------------------- processes

def _state_file(name: str) -> Path:
    return RUN_DIR / f"{name}.json"


def _log_file(name: str) -> Path:
    return RUN_DIR / f"{name}.log"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie child still answers kill(0); reap it if it's ours.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def _free_port(start: int = 5300) -> int:
    for port in range(start, start + 400):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port")


def run_status(name: str) -> dict | None:
    sf = _state_file(name)
    try:
        state = json.loads(sf.read_text())
    except (FileNotFoundError, ValueError):
        return None
    if not _alive(state["pid"]):
        sf.unlink(missing_ok=True)
        return {"running": False, "exited": True}
    url = state.get("url")
    if not url:
        try:
            tail = _log_file(name).read_text(errors="ignore")[-20000:]
        except OSError:
            tail = ""
        m = URL_IN_LOG.search(ANSI.sub("", tail))
        if m:
            url = m.group(0).replace("0.0.0.0", "127.0.0.1").rstrip("/") + "/"
        elif state.get("port"):
            url = f"http://127.0.0.1:{state['port']}/"
    return {"running": True, "pid": state["pid"], "started": state["started"],
            "url": url, "cmd": state.get("cmd")}


def start(project: dict) -> dict:
    name, path, run = project["name"], Path(project["path"]), project["run"]
    if not run:
        raise ValueError("No run command. Set one in the card's settings.")
    if run.get("file"):
        subprocess.Popen(["open", str(path / run["file"])])
        return {"opened": run["file"]}
    with _lock:
        current = run_status(name)
        if current and current.get("running"):
            return current
        port, url = run.get("port"), None
        if run.get("static"):
            port = _free_port()
            argv = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
            url = f"http://127.0.0.1:{port}/"
        elif run.get("shell"):
            argv = ["/bin/zsh", "-lc", run["shell"]]
        else:
            argv = run["argv"]
        log = open(_log_file(name), "w")
        log.write(f"$ {shlex.join(argv)}\n  (in {path})\n\n")
        log.flush()
        proc = subprocess.Popen(
            argv, cwd=path, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=ENV, start_new_session=True,
        )
        log.close()
        state = {"pid": proc.pid, "started": time.time(), "port": port, "url": url,
                 "cmd": shlex.join(argv)}
        _state_file(name).write_text(json.dumps(state))
    time.sleep(0.4)
    return run_status(name) or {"running": False}


def stop(name: str) -> dict:
    sf = _state_file(name)
    try:
        pid = json.loads(sf.read_text())["pid"]
    except (FileNotFoundError, ValueError, KeyError):
        return {"running": False}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        for _ in range(30):
            if not _alive(pid):
                break
            time.sleep(0.1)
        else:
            continue
        break
    sf.unlink(missing_ok=True)
    return {"running": False}


def log_tail(name: str, lines: int = 150) -> str:
    try:
        text = _log_file(name).read_text(errors="ignore")
    except OSError:
        return ""
    return "\n".join(ANSI.sub("", text).splitlines()[-lines:])


# ---------------------------------------------------------------- opening things

def open_claude(cwd: Path, prompt: str | None = None) -> None:
    """Open Terminal in cwd running claude. Uses a .command file so it needs no
    Automation permission."""
    script = RUN_DIR / f"open-{abs(hash(str(cwd))) % 10**8}.command"
    line = "exec claude" + (f" {shlex.quote(prompt)}" if prompt else "")
    script.write_text(f"#!/bin/zsh -l\ncd {shlex.quote(str(cwd))} || exit 1\nclear\n{line}\n")
    script.chmod(0o700)
    subprocess.Popen(["open", "-a", "Terminal", str(script)])


def open_target(project: dict, target: str) -> None:
    path = Path(project["path"])
    if target == "finder":
        subprocess.Popen(["open", str(path)])
    elif target == "claude":
        open_claude(path)
    elif target == "terminal":
        subprocess.Popen(["open", "-a", "Terminal", str(path)])
    elif target == "url" and project.get("url"):
        subprocess.Popen(["open", project["url"]])
    else:
        raise ValueError(f"can't open {target}")


# ---------------------------------------------------------------- data

META_FIELDS = {"kind", "url", "run", "port", "notes", "hidden", "pinned"}


def projects_payload() -> dict:
    projects = scanner.scan()
    for p in projects:
        p["status"] = run_status(p["name"])
    return {"root": str(scanner.ROOT), "projects": projects, "scanned": time.time()}


def find(name: str) -> dict | None:
    path = scanner.resolve(name)
    if not path:
        return None
    return scanner.describe(path, scanner.load_overrides())


def save_meta(name: str, body: dict) -> dict:
    with _lock:
        data = scanner.load_overrides()
        entry = data.get(name, {})
        for key, value in body.items():
            if key not in META_FIELDS:
                continue
            if key == "kind" and value not in ("work", "personal", None, ""):
                raise ValueError("kind must be work or personal")
            if key == "port" and value not in (None, ""):
                value = int(value)
            if value in (None, "", False):
                entry.pop(key, None)
            else:
                entry[key] = value.strip() if isinstance(value, str) else value
        if entry:
            data[name] = entry
        else:
            data.pop(name, None)
        scanner.save_overrides(data)
    return entry


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "dev-hub"

    def log_message(self, fmt, *args):  # quieter
        if os.environ.get("DEV_HUB_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _error(self, status: int, msg: str) -> None:
        self._json({"error": msg}, status)

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        path = urlparse(self.path).path
        if path == "/api/projects":
            return self._json(projects_payload())
        m = re.fullmatch(r"/api/projects/([^/]+)/log", path)
        if m:
            return self._json({"log": log_tail(_unquote(m.group(1)))})
        if path == "/api/status":
            names = [p.name for p in scanner.project_dirs()]
            return self._json({n: run_status(n) for n in names})
        return self._static(path)

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC not in target.parents or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Dev-Hub") != "1":
            return self._error(HTTPStatus.FORBIDDEN, "forbidden")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "bad json")
        path = urlparse(self.path).path
        try:
            if path == "/api/new-app":
                open_claude(scanner.ROOT, "/new-app")
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/projects/([^/]+)/(run|stop|open|meta)", path)
            if not m:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            name, action = _unquote(m.group(1)), m.group(2)
            project = find(name)
            if not project:
                return self._error(HTTPStatus.NOT_FOUND, "no such project")
            if action == "run":
                return self._json(start(project))
            if action == "stop":
                return self._json(stop(name))
            if action == "open":
                open_target(project, body.get("target", ""))
                return self._json({"ok": True})
            if action == "meta":
                return self._json(save_meta(name, body))
        except ValueError as e:
            return self._error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:  # noqa: BLE001
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"dev hub on http://127.0.0.1:{PORT}/  (watching {scanner.ROOT})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
