"use strict";

const $ = (s, el = document) => el.querySelector(s);
const h = (tag, props = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el[k] = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  return el;
};

const state = {
  view: "projects",
  projects: [],
  otherSessions: [],
  ideas: [],
  stages: ["idea", "exploring", "building", "shipped", "dropped"],
  filter: load("filter", "all"),
  sort: load("sort", "session"),
  query: "",
  showHidden: false,
  openLogs: new Set(),
  openSessions: new Set(),
  editingIdea: null,
  writes: 0, // bumped by every change, so a refresh that started earlier doesn't overwrite it
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

let refreshing = null;
async function refresh() {
  if (refreshing) return refreshing;
  refreshing = doRefresh().finally(() => { refreshing = null; });
  return refreshing;
}

async function doRefresh() {
  const started = state.writes;
  try {
    const [data, ideaData] = await Promise.all([api("/api/projects"), api("/api/ideas")]);
    if (state.writes !== started) return doRefresh();
    state.projects = data.projects;
    state.otherSessions = data.other_sessions || [];
    state.ideas = ideaData.ideas;
    state.stages = ideaData.stages;
    render();
  } catch (e) {
    $("#summary").textContent = "Couldn't reach the hub server. Run ~/Desktop/Claude/dev-hub/start.";
  }
}

async function refreshStatus() {
  if (!state.projects.length || document.hidden) return;
  try {
    const status = await api("/api/status");
    let changed = false;
    for (const p of state.projects) {
      const next = status[p.name] || null;
      if (JSON.stringify(next) !== JSON.stringify(p.status)) { p.status = next; changed = true; }
    }
    if (changed) renderProjects();
    for (const name of state.openLogs) loadLog(name);
    // Ideas added from a Claude session (/idea) show up without a reload.
    const started = state.writes;
    const { ideas: fresh } = await api("/api/ideas");
    if (state.writes === started && !state.editingIdea &&
        JSON.stringify(fresh) !== JSON.stringify(state.ideas)) {
      state.ideas = fresh;
      render();
    }
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
const isoAgo = iso => ago(Date.parse(iso) / 1000);

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

const lastSession = p => (p.sessions || []).find(s => !s.archived) || (p.sessions || [])[0];
const kindLabel = k => ({ work: "Work", personal: "Personal", unknown: "Untagged" }[k] || "Untagged");

// ------------------------------------------------------------------ views

function setView(view) {
  state.view = view === "ideas" ? "ideas" : "projects";
  for (const t of document.querySelectorAll(".tab")) {
    const on = t.dataset.view === state.view;
    t.classList.toggle("on", on);
    t.setAttribute("aria-selected", on);
  }
  $("#view-projects").hidden = state.view !== "projects";
  $("#view-ideas").hidden = state.view !== "ideas";
  if (state.view === "ideas" && !state.editingIdea) setTimeout(() => $("#idea-form").title.focus(), 0);
}

function render() {
  renderProjects();
  renderIdeas();
  const open = state.ideas.filter(i => !["shipped", "dropped"].includes(i.stage)).length;
  $("#count-projects").textContent = state.projects.filter(p => !p.hidden).length || "";
  $("#count-ideas").textContent = open || "";
}

// ------------------------------------------------------------------ projects

function visible(p) {
  if (p.hidden && !state.showHidden) return false;
  const f = state.filter;
  if (f === "running" && !p.status?.running) return false;
  if (["work", "personal", "unknown"].includes(f) && p.kind !== f) return false;
  if (state.query) {
    const hay = [p.name, p.blurb, p.stack.join(" "), p.git?.remote, p.kind,
      ...(p.sessions || []).map(s => s.title)].join(" ").toLowerCase();
    if (!state.query.split(/\s+/).every(w => hay.includes(w))) return false;
  }
  return true;
}

function sorted(list) {
  const sessTime = p => lastSession(p)?.last || 0;
  const by = {
    name: (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    touched: (a, b) => b.touched - a.touched,
    session: (a, b) => sessTime(b) - sessTime(a) || b.touched - a.touched,
    changes: (a, b) => (b.git?.changes ?? -1) - (a.git?.changes ?? -1) || b.touched - a.touched,
  }[state.sort] || ((a, b) => b.touched - a.touched);
  return [...list].sort((a, b) => (b.pinned - a.pinned) || by(a, b));
}

function renderProjects() {
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

  renderWaiting();

  const grid = $("#grid");
  const list = sorted(state.projects.filter(visible));
  const keep = new Map([...grid.children].map(el => [el.dataset.name, el]));
  const frag = document.createDocumentFragment();
  for (const p of list) {
    const el = keep.get(p.name);
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
    grid.append(h("p", { class: "empty-state" },
      state.query ? `Nothing matches “${state.query}”.` : "Nothing here."));
  }

  const hidden = state.projects.filter(p => p.hidden).length;
  const note = $("#hidden-note");
  note.replaceChildren();
  if (hidden) {
    note.append(`${hidden} hidden project${hidden === 1 ? "" : "s"}, `,
      h("button", { onclick: () => { state.showHidden = !state.showHidden; renderProjects(); } },
        state.showHidden ? "hide them again" : "show them"));
  }

  const other = $("#other-sessions");
  const others = state.otherSessions.filter(s => !s.archived);
  other.hidden = !others.length;
  $("summary span", other).textContent = others.length;
  $(".sess-list", other).replaceChildren(...others.map(s => h("li", {}, sessionRow(s))));
}

function renderWaiting() {
  const rows = [];
  for (const p of state.projects) {
    for (const s of p.sessions || []) if (s.waiting && !s.archived) rows.push([p.name, s]);
  }
  for (const s of state.otherSessions) if (s.waiting && !s.archived) rows.push(["No project", s]);
  rows.sort((a, b) => b[1].last - a[1].last);
  const box = $("#waiting");
  box.hidden = !rows.length;
  $("#waiting-list").replaceChildren(...rows.map(([proj, s]) => h("li", {},
    h("span", { class: "w-proj" }, proj),
    h("span", { class: "w-text", title: s.needs || s.detail || s.title },
      h("b", {}, s.title), s.needs || s.detail ? `, ${s.needs || s.detail}` : ""),
    h("button", { class: "btn", onclick: () => openSession(s) }, "Open"),
  )));
}

function fact(dl, label, value, cls) {
  if (value === null || value === undefined || value === "") return;
  dl.append(h("dt", {}, label), h("dd", { class: cls, title: value }, value));
}

function sessionRow(s) {
  const statusClass = s.waiting ? "waiting" : (s.status || "none");
  const detail = s.needs || s.detail;
  const src = s.source === "terminal" ? "terminal" : s.scheduled ? "scheduled" : s.archived ? "archived" : null;
  return h("button", {
    class: `sess ${statusClass}${s.archived ? " archived" : ""}`,
    title: s.source === "app" ? "Open this session in the Claude app" : "Resume this session in Terminal",
    onclick: () => openSession(s),
  },
    h("span", { class: "s-dot" }),
    h("span", { class: "s-title" }, s.title, src ? h("span", { class: "s-src" }, src) : null),
    h("span", { class: "s-when" }, ago(s.last)),
    detail ? h("span", { class: `s-detail${s.needs ? " needs" : ""}` }, s.needs ? `Waiting on you: ${s.needs}` : detail) : null,
  );
}

function sessionsBlock(p) {
  const list = p.sessions || [];
  const live = list.filter(s => !s.archived);
  const shown = state.openSessions.has(p.name) ? list.slice(0, 12) : live.slice(0, 1);
  if (!list.length) return null;
  const wrap = h("div", {}, h("ul", { class: "sess-list" }, shown.map(s => h("li", {}, sessionRow(s)))));
  const extra = list.length - shown.length;
  if (extra > 0 || state.openSessions.has(p.name)) {
    wrap.append(h("button", {
      class: "more-sess",
      onclick: () => {
        state.openSessions.has(p.name) ? state.openSessions.delete(p.name) : state.openSessions.add(p.name);
        renderProjects();
      },
    }, state.openSessions.has(p.name) ? "Show less" : `${extra} more session${extra === 1 ? "" : "s"}`));
  }
  return wrap;
}

function card(p) {
  const el = $("#card").content.firstElementChild.cloneNode(true);
  el.dataset.name = p.name;
  el.classList.toggle("is-running", !!p.status?.running);
  el.classList.toggle("is-pinned", p.pinned);

  $(".name", el).textContent = p.name;
  $(".name", el).title = p.path;
  const kind = $(".kind", el);
  kind.textContent = kindLabel(p.kind);
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
  for (const s of p.stack) stack.append(h("li", {}, s));
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

  const sess = sessionsBlock(p);
  if (sess) $(".sessions", el).append(sess);
  if (p.ideas?.length) {
    el.insertBefore(h("p", { class: "card-ideas" }, "From the backlog: ",
      ...p.ideas.map((i, n) => [n ? ", " : "", h("a", { href: "#ideas" }, i.title)])), $(".card-actions", el));
  }

  // running line
  const line = $(".running-line", el);
  if (p.status?.running) {
    if (p.status.url) {
      line.append(h("a", { href: p.status.url, target: "_blank", rel: "noopener" },
        p.status.url.replace(/^https?:\/\//, "").replace(/\/$/, "")));
    } else {
      line.append("Starting…");
    }
    line.append(h("button", { onclick: () => toggleLog(p.name, el) }, "log"));
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
  $("[data-terminal]", el).onclick = () => openTarget(p, "claude");
  $(".more", el).onclick = () => toggleMeta(p, el);
  return el;
}

// ------------------------------------------------------------------ project actions

async function act(p, action, btn) {
  btn.disabled = true;
  try {
    const res = await api(`/api/projects/${enc(p.name)}/${action}`, {});
    if (res.opened) toast(`Opened ${res.opened}`);
    else p.status = res.running ? res : null;
    renderProjects();
    if (action === "run" && res.running) {
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
    if (target === "app") toast(`New Claude session in ${p.name}`);
    if (target === "claude") toast(`Opening Claude in Terminal in ${p.name}`);
  } catch (e) { toast(e.message, true); }
}

async function openSession(s) {
  try {
    await api("/api/sessions/open", { id: s.id, source: s.source, cwd: s.cwd });
    toast(s.source === "app" ? `Opening “${s.title}”` : "Resuming in Terminal");
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
    state.writes++;
    try {
      await api(`/api/projects/${enc(p.name)}/meta`, body);
      form.hidden = true;
      toast("Saved");
      refresh();
    } catch (e) { toast(e.message, true); }
  };
  form.kind.focus();
}

// ------------------------------------------------------------------ ideas

const STAGE_LABEL = { idea: "Ideas", exploring: "Exploring", building: "Building", shipped: "Shipped", dropped: "Dropped" };
const STAGE_HINT = {
  idea: "Nothing here. Add one above.",
  exploring: "Ideas you're poking at.",
  building: "Ideas with a project folder.",
  shipped: "Done and in use.",
};

function renderIdeas() {
  const board = $("#board");
  const cols = state.stages.filter(s => s !== "dropped").map(stage => {
    const items = state.ideas.filter(i => i.stage === stage);
    return h("section", { class: "col" },
      h("h3", {}, STAGE_LABEL[stage], h("span", {}, items.length || "")),
      items.length ? items.map(ideaCard) : h("p", { class: "empty" }, STAGE_HINT[stage]));
  });
  board.replaceChildren(...cols);
  const dropped = state.ideas.filter(i => i.stage === "dropped");
  const box = $("#dropped");
  box.hidden = !dropped.length;
  $("summary span", box).textContent = dropped.length;
  $(".dropped-list", box).replaceChildren(...dropped.map(ideaCard));
}

function ideaCard(i) {
  if (state.editingIdea === i.id) return ideaEditor(i);
  const project = i.project && state.projects.find(p => p.name === i.project);
  const stageSel = h("select", {
    "aria-label": "Stage",
    onchange: ev => updateIdea(i, { stage: ev.target.value }),
  }, state.stages.map(s => h("option", { value: s, selected: s === i.stage }, STAGE_LABEL[s].replace(/s$/, "").replace("Idea", "Idea"))));
  return h("article", { class: "idea" },
    h("h4", {}, i.title),
    i.note ? h("p", {}, i.note) : null,
    h("div", { class: "i-meta" },
      h("span", { class: `kind ${i.kind}` }, kindLabel(i.kind)),
      h("span", { title: i.created }, `added ${isoAgo(i.created)}`),
      i.project ? h("a", {
        href: "#projects",
        onclick: () => { state.query = i.project.toLowerCase(); $("#search").value = i.project; },
      }, project ? `→ ${i.project}` : `→ ${i.project} (missing)`) : null,
      h("span", { title: "Use this id with /idea in Claude" }, `#${i.id}`),
    ),
    h("div", { class: "i-actions" },
      !i.project && ["idea", "exploring"].includes(i.stage)
        ? h("button", { class: "btn", title: "Opens a Claude session with /new-app and this idea filled in", onclick: () => startIdea(i) }, "Start it")
        : null,
      h("button", { class: "btn ghost", onclick: () => { state.editingIdea = i.id; renderIdeas(); } }, "Edit"),
      h("button", { class: "btn ghost", onclick: () => deleteIdea(i) }, "Delete"),
      stageSel,
    ),
  );
}

function ideaEditor(i) {
  const form = h("form", {},
    h("input", { name: "title", value: i.title, required: true, maxlength: "140", "aria-label": "Title" }),
    h("input", { name: "note", value: i.note || "", placeholder: "Note", "aria-label": "Note" }),
    h("select", { name: "kind", "aria-label": "Work or personal" },
      ["unknown", "personal", "work"].map(k => h("option", { value: k, selected: k === i.kind }, k === "unknown" ? "Not sure yet" : kindLabel(k)))),
    h("select", { name: "project", "aria-label": "Linked project" },
      h("option", { value: "" }, "No project yet"),
      state.projects.map(p => h("option", { value: p.name, selected: p.name === i.project }, p.name))),
    h("div", {},
      h("button", { class: "btn", type: "button", onclick: () => { state.editingIdea = null; renderIdeas(); } }, "Cancel"),
      h("button", { class: "btn primary", type: "submit" }, "Save")),
  );
  form.onsubmit = async ev => {
    ev.preventDefault();
    await updateIdea(i, { title: form.title.value, note: form.note.value, kind: form.kind.value, project: form.project.value || null });
    state.editingIdea = null;
    renderIdeas();
  };
  setTimeout(() => form.title.focus(), 0);
  return h("article", { class: "idea" }, form);
}

async function updateIdea(i, changes) {
  state.writes++;
  try {
    const next = await api(`/api/ideas/${i.id}`, changes);
    Object.assign(i, next);
    render();
  } catch (e) { toast(e.message, true); }
}

async function deleteIdea(i) {
  if (!confirm(`Delete “${i.title}”? This can't be undone. (Dropping it keeps it in the Dropped list.)`)) return;
  state.writes++;
  try {
    await api(`/api/ideas/${i.id}/delete`, {});
    state.ideas = state.ideas.filter(x => x.id !== i.id);
    render();
  } catch (e) { toast(e.message, true); }
}

async function startIdea(i) {
  state.writes++;
  try {
    const next = await api(`/api/ideas/${i.id}/start`, {});
    Object.assign(i, next);
    render();
    toast("Opening a Claude session with /new-app for this idea");
  } catch (e) { toast(e.message, true); }
}

$("#idea-form").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target;
  state.writes++;
  try {
    const idea = await api("/api/ideas", { title: f.title.value, note: f.note.value, kind: f.kind.value });
    state.ideas.unshift(idea);
    f.title.value = ""; f.note.value = "";
    render();
    f.title.focus();
  } catch (e) { toast(e.message, true); }
};

// ------------------------------------------------------------------ misc

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 5000 : 2200);
}

$("#filters").addEventListener("click", ev => {
  const chip = ev.target.closest(".chip");
  if (!chip) return;
  state.filter = chip.dataset.filter;
  save("filter", state.filter);
  renderProjects();
});
$("#sort").value = state.sort;
$("#sort").onchange = ev => { state.sort = ev.target.value; save("sort", state.sort); renderProjects(); };
$("#search").oninput = ev => { state.query = ev.target.value.trim().toLowerCase(); renderProjects(); };
$("#refresh").onclick = refresh;
$("#new-app").onclick = async () => {
  try {
    await api("/api/new-app", {});
    toast("Opening a Claude session with /new-app");
  } catch (e) { toast(e.message, true); }
};

document.addEventListener("keydown", ev => {
  const typing = ev.target.matches("input, select, textarea");
  if (ev.key === "/" && !typing && state.view === "projects") { ev.preventDefault(); $("#search").focus(); }
  else if (ev.key === "Escape" && ev.target === $("#search")) { ev.target.value = ""; state.query = ""; renderProjects(); ev.target.blur(); }
  else if ((ev.key === "r" || ev.key === "R") && !typing && !ev.metaKey && !ev.ctrlKey) refresh();
});

window.addEventListener("hashchange", () => setView(location.hash.slice(1)));
window.addEventListener("focus", refresh);
setInterval(refreshStatus, 3000);
setView(location.hash.slice(1) || "projects");
refresh();
