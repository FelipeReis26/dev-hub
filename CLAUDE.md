# Dev hub

Felipe's central place for building apps with Claude Code: a local dashboard over every project in `~/Desktop/Claude`, plus the `/new-app` scaffolder. Started 12 Nov 2026.

**Personal repo.** Local git identity is `Felipe Reis <78143596+FelipeReis26@users.noreply.github.com>`; check `git config user.email` before committing. Remote: `git@github-dev-hub:FelipeReis26/dev-hub.git` (personal GitHub), pushed through a repo-only deploy key: ssh alias `github-dev-hub` in `~/.ssh/config`, key `~/.ssh/dev-hub_deploy`. Never use the work `gh` login here. `projects.json` is gitignored because it holds work project names and URLs.

## Pieces

| Path | What it is |
|---|---|
| `start` | Starts the server if it isn't running and opens http://127.0.0.1:4747/. |
| `hub/server.py` | Python standard library HTTP server. Serves the page and a small JSON API; starts and stops project dev servers. |
| `hub/scanner.py` | Reads each top-level folder in `~/Desktop/Claude` (skips dotfolders and symlinks): stack, git state, newest file change, README line, run command, work or personal. `python3 hub/scanner.py` prints a table. |
| `hub/static/` | The dashboard (`index.html`, `style.css`, `app.js`). No build step, no dependencies. |
| `projects.json` | Felipe's per-project settings from the dashboard (type, live URL, run command, port, notes, pin, hide) and the entries `/new-app` registers. Hand-editable. Created on first save. |
| `bin/new_app.py` | Scaffolds a project from `templates/`. The `/new-app` skill (`~/.claude/skills/new-app/SKILL.md`) wraps it. |
| `templates/` | `static`, `vite-react`, `python`, `blank`, plus `_common/` (CLAUDE.md, README, the personal and work account rules). `{{name}}`-style placeholders. |
| `.run/` | Logs and pid files for dev servers started from the hub, and the `.command` files that open Terminal. Ignored by git. |

## How things are decided

- **Run command**, in order: the card's setting in `projects.json`, the project's `.claude/launch.json` (first config), `package.json` `dev` then `start`, a static server for a folder with `index.html`, or opening the newest top-level `.html` file.
- **Work or personal**, in order: the card's setting, then the git remote and local identity (`FelipeReis26` or the noreply email means personal, `fanatics-gaming` or the work email means work), then a guess from README/CLAUDE.md (shown with a "?"). Otherwise untagged.
- **Claude button**: writes a `.command` file and opens it in Terminal, which runs `claude` in the project. No Automation permission needed.

## Safety

- Binds to 127.0.0.1 only and rejects any other Host header (DNS rebinding).
- Every POST needs an `X-Dev-Hub: 1` header, so another website can't trigger runs (it would need a CORS preflight the server never answers).
- Project names in URLs must be a direct child folder of `~/Desktop/Claude`.
- The scanner only reads package.json, README.md, CLAUDE.md, SPEC.md and .claude/launch.json, plus file mtimes. Some project folders hold secrets (GILD has recovery codes); never widen what it reads without a reason.

## Testing

`DEV_HUB_ROOT=/some/folder DEV_HUB_OVERRIDES=/some/projects.json` points the scanner, server and `new_app.py` somewhere else, so you can test scaffolding without touching `~/Desktop/Claude`.

## Decisions

- 12 Nov 2026: pushed to FelipeReis26/dev-hub on GitHub with a deploy key.
- 12 Nov 2026: made a personal git repo. `projects.json` stays out of git. The hub doesn't start at login; Felipe runs `./start`.
- 12 Nov 2026: started with the project dashboard and `/new-app`. Shared conventions across projects and an idea backlog were offered as later additions.
