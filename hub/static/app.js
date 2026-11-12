"use strict";

const $ = (s, el = document) => el.querySelector(s);
const state = {
  projects: [],
  filter: load("filter", "all"),
  sort: load("sort", "touched"),
  query: "",
  showHidden: false,
  openLogs: new Set(),
};

function load(key, fallback) {
  try { return localStorage.getItem("devhub." + key) || fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem("devhub." + key, value); } catch { /* private window */ }
}

// ------------------------------------------------------------------ api

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Dev-Hub": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

const enc = encodeURIComponent;

async function refresh() {
  try {
    const data = await api("/api/projects");
    state.projects = data.projects;
    render();
  } catch (e) {
    $("#summary").textContent = "Couldn't reach the hub server. Is it running?";
  }
}

async function refreshStatus() {
  if (!state.projects.length) return;
  try {
    const status = await api("/api/status");
    let changed = false;
    for (const p of state.projects) {
      const next = status[p.name] || null;
      if (JSON.stringify(next) !== JSON.stringify(p.status)) { p.status = next; changed = true; }
    }
    if (changed) render();
    for (const name of state.openLogs) loadLog(name);
  } catch { /* server restarting */ }
}

// ------------------------------------------------------------------ formatting

function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  const units = [[60, "min"], [3600, "hour"], [86400, "day"], [604800, "week"], [2629800, "month"], [31557600, "year"]];
  let [div, label] = units[0];
  for (const u of units) if (s >= u[0]) [div, label] = u;
  const n = Math.floor(s / div);
  return `${n} ${label}${n === 1 ? "" : "s"} ago`;
}

function remoteLabel(url) {
  if (!url) return null;
  const m = url.match(/[:/]([^/:]+\/[^/]+?)(?:\.git)?$/);
  return m ? m[1] : url;
}

function runLabel(p) {
  const r = p.run;
  if (!r) return null;
  if (r.file) return `opens ${r.file}`;
  if (r.static) return "static server";
  if (r.shell) return r.shell;
  return r.argv.join(" ");
}

// ------------------------------------------------------------------ render

function visible(p) {
  if (p.hidden && !state.showHidden) return false;
  const f = state.filter;
  if (f === "running" && !p.status?.running) return false;
  if (["work", "personal", "unknown"].includes(f) && p.kind !== f) return false;
  if (state.query) {
    const hay = [p.name, p.blurb, p.stack.join(" "), p.git?.remote, p.kind].join(" ").toLowerCase();
    if (!state.query.split(/\s+/).every(w => hay.includes(w))) return false;
  }
  return true;
}

function sorted(list) {
  const by = {
    name: (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    touched: (a, b) => b.touched - a.touched,
    changes: (a, b) => (b.git?.changes ?? -1) - (a.git?.changes ?? -1) || b.touched - a.touched,
  }[state.sort];
  return [...list].sort((a, b) => (b.pinned - a.pinned) || by(a, b));
}

function render() {
  const all = state.projects.filter(p => !p.hidden);
  const counts = {
    all: all.length,
    work: all.filter(p => p.kind === "work").length,
    personal: all.filter(p => p.kind === "personal").length,
    unknown: all.filter(p => p.kind === "unknown").length,
    running: state.projects.filter(p => p.status?.running).length,
  };
  for (const chip of document.querySelectorAll("#filters .chip")) {
    chip.classList.toggle("on", chip.dataset.filter === state.filter);
    chip.querySelector("span").textContent = counts[chip.dataset.filter] || "";
  }
  const dirty = all.filter(p => p.git?.changes).length;
  $("#summary").textContent =
    `${all.length} projects in ~/Desktop/Claude` +
    (counts.running ? ` · ${counts.running} running` : "") +
    (dirty ? ` · ${dirty} with uncommitted changes` : "");

  const grid = $("#grid");
  const list = sorted(state.projects.filter(visible));
  const keep = new Map([...grid.children].map(el => [el.dataset.name, el]));
  const frag = document.createDocumentFragment();
  for (const p of list) {
    let el = keep.get(p.name);
    // Don't redraw a card whose settings form is open; it would wipe what's typed.
    if (el && !$(".meta", el).hidden) { frag.append(el); continue; }
    const fresh = card(p);
    if (el) {
      $(".log", fresh).hidden = $(".log", el).hidden;
      $(".log", fresh).textContent = $(".log", el).textContent;
    }
    frag.append(fresh);
  }
  grid.replaceChildren(frag);
  if (!list.length) {
    const msg = document.createElement("p");
    msg.className = "empty-state";
    msg.textContent = state.query ? `Nothing matches “${state.query}”.` : "Nothing here.";
    grid.append(msg);
  }

  const hidden = state.projects.filter(p => p.hidden).length;
  const note = $("#hidden-note");
  note.replaceChildren();
  if (hidden) {
    const b = document.createElement("button");
    b.textContent = state.showHidden ? "hide them again" : "show them";
    b.onclick = () => { state.showHidden = !state.showHidden; render(); };
    note.append(`${hidden} hidden project${hidden === 1 ? "" : "s"}, `, b);
  }
}

function fact(dl, label, value, cls) {
  if (value === null || value === undefined || value === "") return;
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = label;
  dd.textContent = value;
  dd.title = value;
  if (cls) dd.className = cls;
  dl.append(dt, dd);
}

function card(p) {
  const el = $("#card").content.firstElementChild.cloneNode(true);
  el.dataset.name = p.name;
  el.classList.toggle("is-running", !!p.status?.running);
  el.classList.toggle("is-pinned", p.pinned);

  $(".name", el).textContent = p.name;
  $(".name", el).title = p.path;
  const kind = $(".kind", el);
  kind.textContent = { work: "Work", personal: "Personal", unknown: "Untagged" }[p.kind];
  kind.classList.add(p.kind);
  if (p.kind_source === "guess") {
    kind.classList.add("guess");
    kind.title = "Guessed from the README. Set it in settings to confirm.";
  } else if (p.kind_source === "git") {
    kind.title = "From the git remote and identity";
  }

  const blurb = $(".blurb", el);
  blurb.textContent = p.blurb || "No README yet.";
  blurb.classList.toggle("empty", !p.blurb);

  const stack = $(".stack", el);
  for (const s of p.stack) {
    const li = document.createElement("li");
    li.textContent = s;
    stack.append(li);
  }
  if (!p.stack.length) stack.remove();

  const dl = $(".facts", el);
  fact(dl, "Touched", ago(p.touched));
  if (p.git) {
    const g = p.git;
    const bits = [g.branch];
    if (g.ahead) bits.push(`${g.ahead} to push`);
    if (g.behind) bits.push(`${g.behind} behind`);
    if (g.worktree) bits.push("worktree");
    fact(dl, "Branch", bits.filter(Boolean).join(" · "));
    if (g.changes) fact(dl, "Changes", `${g.changes} uncommitted`, "warn");
    if (g.last_message) fact(dl, "Last commit", `${ago(g.last_commit)}, ${g.last_message}`);
    fact(dl, "Remote", remoteLabel(g.remote) || "none yet");
  } else if (p.nested_repos.length) {
    fact(dl, "Repos", p.nested_repos.join(", "));
  } else {
    fact(dl, "Git", "not a repo");
  }
  if (p.run && !p.run.file) fact(dl, "Runs", runLabel(p), "mono");

  // running line
  const line = $(".running-line", el);
  if (p.status?.running) {
    if (p.status.url) {
      const a = document.createElement("a");
      a.href = p.status.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = p.status.url.replace(/^https?:\/\//, "").replace(/\/$/, "");
      line.append(a);
    } else {
      line.append("Starting…");
    }
    const logBtn = document.createElement("button");
    logBtn.textContent = "log";
    logBtn.onclick = () => toggleLog(p.name, el);
    line.append(logBtn);
  }

  // actions
  const run = $(".run", el);
  if (p.is_hub) {
    run.remove();
  } else if (p.status?.running) {
    run.textContent = "Stop";
    run.onclick = () => act(p, "stop", run);
  } else if (p.run?.file) {
    run.textContent = "Open";
    run.title = p.run.file;
    run.onclick = () => act(p, "run", run);
  } else if (p.run) {
    run.textContent = "Run";
    run.title = runLabel(p);
    run.onclick = () => act(p, "run", run);
  } else {
    run.textContent = "Run";
    run.disabled = true;
    run.title = "No run command found. Add one in settings.";
  }

  for (const b of el.querySelectorAll("[data-open]")) {
    if (b.dataset.open === "url" && !p.url) { b.remove(); continue; }
    if (b.dataset.open === "url") b.title = p.url;
    b.onclick = () => openTarget(p, b.dataset.open);
  }

  $(".more", el).onclick = () => toggleMeta(p, el);
  return el;
}

// ------------------------------------------------------------------ actions

async function act(p, action, btn) {
  btn.disabled = true;
  try {
    const res = await api(`/api/projects/${enc(p.name)}/${action}`, {});
    if (res.opened) toast(`Opened ${res.opened}`);
    else p.status = res.running ? res : null;
    render();
    if (action === "run" && res.running) {
      // the dev server prints its URL a moment later
      setTimeout(refreshStatus, 1500);
      setTimeout(refreshStatus, 4000);
    }
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}

async function openTarget(p, target) {
  try {
    await api(`/api/projects/${enc(p.name)}/open`, { target });
    if (target === "claude") toast(`Opening Claude in ${p.name}`);
  } catch (e) { toast(e.message, true); }
}

async function loadLog(name) {
  const el = document.querySelector(`.card[data-name="${CSS.escape(name)}"] .log`);
  if (!el || el.hidden) return;
  const { log } = await api(`/api/projects/${enc(name)}/log`);
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
  el.textContent = log || "(nothing yet)";
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function toggleLog(name, el) {
  const log = $(".log", el);
  log.hidden = !log.hidden;
  if (log.hidden) state.openLogs.delete(name);
  else { state.openLogs.add(name); loadLog(name); }
}

function toggleMeta(p, el) {
  const form = $(".meta", el);
  if (!form.hidden) { form.hidden = true; return; }
  const o = p.overrides || {};
  form.kind.value = o.kind || "";
  form.url.value = o.url || "";
  form.url.placeholder = p.url && !o.url ? p.url : "https://…";
  form.run.value = o.run || "";
  form.run.placeholder = p.run && p.run.source !== "hub" ? `${runLabel(p)}  (from ${p.run.source})` : "e.g. npm run dev, or python3 app.py";
  form.port.value = o.port || "";
  form.notes.value = o.notes || "";
  form.pinned.checked = !!o.pinned;
  form.hide.checked = !!o.hidden;
  form.hidden = false;
  form.querySelector("[data-cancel]").onclick = () => { form.hidden = true; };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const body = {
      kind: form.kind.value || null,
      url: form.url.value,
      run: form.run.value,
      port: form.port.value || null,
      notes: form.notes.value,
      pinned: form.pinned.checked,
      hidden: form.hide.checked,
    };
    try {
      await api(`/api/projects/${enc(p.name)}/meta`, body);
      form.hidden = true;
      toast("Saved");
      refresh();
    } catch (e) { toast(e.message, true); }
  };
  form.kind.focus();
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 5000 : 2200);
}

// ------------------------------------------------------------------ wiring

$("#filters").addEventListener("click", ev => {
  const chip = ev.target.closest(".chip");
  if (!chip) return;
  state.filter = chip.dataset.filter;
  save("filter", state.filter);
  render();
});
$("#sort").value = state.sort;
$("#sort").onchange = ev => { state.sort = ev.target.value; save("sort", state.sort); render(); };
$("#search").oninput = ev => { state.query = ev.target.value.trim().toLowerCase(); render(); };
$("#refresh").onclick = refresh;
$("#new-app").onclick = async () => {
  try {
    await api("/api/new-app", {});
    toast("Opening Claude with /new-app in Terminal");
  } catch (e) { toast(e.message, true); }
};

document.addEventListener("keydown", ev => {
  const typing = ev.target.matches("input, select, textarea");
  if (ev.key === "/" && !typing) { ev.preventDefault(); $("#search").focus(); }
  else if (ev.key === "Escape" && ev.target === $("#search")) { ev.target.value = ""; state.query = ""; render(); ev.target.blur(); }
  else if ((ev.key === "r" || ev.key === "R") && !typing && !ev.metaKey && !ev.ctrlKey) refresh();
});

window.addEventListener("focus", refresh);
setInterval(refreshStatus, 3000);
refresh();
