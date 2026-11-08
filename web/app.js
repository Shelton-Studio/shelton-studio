// Shelton Studio — interfaz (núcleo). Sin dependencias: funciona 100 % en local.
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("es-ES");
const fmtN = (n) => nf.format(Math.round(n || 0));
const fmtMoney = (n) => "$" + (n || 0).toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const S = { posts: new Map(), progress: new Map(), selected: new Set(), wl: {}, platforms: [], agents: [], engines: null,
  conv: null, chatAgent: "shelton", chatEngine: "", uploads: [], busy: false, notes: [], unread: 0, galleryFilter: "", galleryMedia: "", offset: 0 };

// ------------------------------------------------------------------ Íconos (trazos estilo lucide)
const ICONS = {
  home: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  tasks: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/>',
  approve: '<path d="m2 12 5 5L18 6"/><path d="m11 16 1 1 10-11"/>',
  gallery: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1z"/><path d="M12 15 9 12a22 22 0 0 1 2-4A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2z"/><path d="M9 12H4s.6-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.6 4-2c1.1-1.6 0-5 0-5"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 3.1a4 4 0 0 1 0 7.8M22 21a7 7 0 0 0-4-6.3"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M8 14h.01M16 14h.01M9 18h6"/>',
  brain: '<path d="M12 5a3 3 0 1 0-5.9.8A3 3 0 0 0 4 11a3 3 0 0 0 1.5 5.2A3 3 0 0 0 12 18z"/><path d="M12 5a3 3 0 1 1 5.9.8A3 3 0 0 1 20 11a3 3 0 0 1-1.5 5.2A3 3 0 0 1 12 18z"/><path d="M12 5v13"/>',
  trend: '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>',
  plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a6 6 0 0 1-12 0V8z"/>',
  flow: '<rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="15" y="15" width="6" height="6" rx="1.5"/><path d="M6 9v3a3 3 0 0 0 3 3h6"/>',
  pulse: '<path d="M22 12h-4l-3 8-6-16-3 8H2"/>',
  tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8z"/><circle cx="7" cy="7" r="1.5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1A2 2 0 1 1 4.3 17l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8L4.2 7A2 2 0 1 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  check: '<path d="M20 6 9 17l-5-5"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>', alert: '<path d="m10.3 3.9-8.2 14A2 2 0 0 0 3.8 21h16.4a2 2 0 0 0 1.7-3.1l-8.2-14a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  hourglass: '<path d="M5 22h14M5 2h14M17 22v-4.2a2 2 0 0 0-.6-1.4L12 12l-4.4 4.4a2 2 0 0 0-.6 1.4V22M7 2v4.2a2 2 0 0 0 .6 1.4L12 12l4.4-4.4a2 2 0 0 0 .6-1.4V2"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>', clip: '<path d="m21.4 11-8.8 8.8a5.6 5.6 0 0 1-8-8l8.8-8.8a3.8 3.8 0 0 1 5.3 5.3l-8.8 8.8a1.9 1.9 0 0 1-2.7-2.7L15.4 6.2"/>',
  send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>', play: '<path d="m6 3 14 9-14 9z"/>', film: '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M7 3v18M17 3v18M2 8h5M17 8h5M2 16h5M17 16h5"/>',
  wave: '<path d="M2 12h2M6 8v8M10 4v16M14 7v10M18 10v4M22 12h0"/>', sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>', download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>', trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>', eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
};
const icon = (n, s = 20) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[n] || ""}</svg>`;

// ------------------------------------------------------------------ Utilidades
async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const r = await fetch(path, { ...opts, headers: opts.body && !isForm ? { "Content-Type": "application/json" } : {},
    body: opts.body && !isForm ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(typeof m === "string" ? m : JSON.stringify(m)); }
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : r;
}
function toast(msg, kind = "", ico = "", actions = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = `<div class="t-ico">${ico || (kind === "good" ? "✅" : kind === "bad" ? "⚠️" : kind === "warn" ? "⚖️" : "ℹ️")}</div><div class="grow">${msg}${actions ? `<div class="row" style="margin-top:8px">${actions}</div>` : ""}</div><button class="t-x">${icon("x", 16)}</button>`;
  $(".t-x", t).onclick = () => t.remove();
  $("#toasts").prepend(t);
  setTimeout(() => t.remove(), kind === "warn" ? 15000 : 5500);
  return t;
}
const when = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso), s = (Date.now() - d) / 1000;
  if (s < 60) return "recién"; if (s < 3600) return `hace ${Math.floor(s / 60)} min`; if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  if (s < 172800) return "ayer"; return d.toLocaleDateString("es-ES", { day: "numeric", month: "short" });
};
const scoreCls = (s) => (s >= 85 ? "hi" : s >= 70 ? "mid" : "lo");
const STATUS = { queued: ["En cola", ""], generating: ["Generando", "accent"], ready: ["Para revisar", "warn"], approved: ["Aprobado", "good"], published: ["Publicado", "info"], failed: ["Falló", "bad"], rejected: ["Rechazado", "bad"] };
const chip = (st) => `<span class="chip ${STATUS[st]?.[1] || ""}">${STATUS[st]?.[0] || esc(st)}</span>`;
const agentName = (k) => S.agents.find((a) => a.key === k)?.name || k;
const agentColor = (k) => S.agents.find((a) => a.key === k)?.color || "#8b5cf6";
const avatar = (k, sm) => `<div class="avatar ${sm ? "sm" : ""}" style="background:linear-gradient(140deg, ${agentColor(k)}, #4c1d95)">${esc((agentName(k) || "?")[0])}</div>`;
function empty(ico, text, extra = "") { return `<div class="empty"><div class="em-ico">${ico}</div><div>${text}</div>${extra}</div>`; }

// ------------------------------------------------------------------ Navegación
const NAV = [
  ["inicio", "home", "Inicio"], ["chat", "chat", "Chat"], ["tareas", "tasks", "Tareas"], ["aprobaciones", "approve", "Aprobaciones", "approvals"], "-",
  ["galeria", "gallery", "Galería", "review"], ["campanas", "rocket", "Campañas"], ["banco", "users", "Banco de modelos"], ["tendencias", "trend", "Tendencias", "findings"], ["voces", "mic", "Voces"], "-",
  ["agentes", "bot", "Agentes"], ["memoria", "brain", "Memoria", "memory"], "-",
  ["conexiones", "plug", "Conexiones"], ["flujos", "flow", "Flujos"], ["actividad", "pulse", "Actividad"], ["marca", "tag", "Marca"], ["ajustes", "gear", "Ajustes"],
];
const VIEWS = {};
function renderRail() {
  const wide = localStorage.getItem("wide") === "1";
  $("#app").classList.toggle("wide", wide);
  $("#rail").innerHTML = `<button class="brand-btn" id="brand-btn" title="Expandir menú"><div class="logo" id="logo"></div><div class="names"><b id="wl-name"></b><small id="wl-tag"></small></div></button>` +
    NAV.map((n) => n === "-" ? '<div class="nav-sep"></div>' :
      `<button class="nav-item" data-view="${n[0]}" data-tip="${n[2]}">${icon(n[1])}<span class="label">${n[2]}</span><span class="count" data-count="${n[3] || ""}"></span></button>`).join("") +
    `<div class="rail-foot"><button class="nav-item" id="toggle-rail" data-tip="Expandir">${icon("menu")}<span class="label">Contraer menú</span></button></div>`;
  $$(".nav-item[data-view]").forEach((b) => b.onclick = () => go(b.dataset.view));
  $("#toggle-rail").onclick = $("#brand-btn").onclick = () => { localStorage.setItem("wide", $("#app").classList.contains("wide") ? "0" : "1"); renderRail(); applyWL(); markNav(); };
  $$("[data-tip]").forEach((el) => {
    el.onmouseenter = () => { if ($("#app").classList.contains("wide")) return; const r = el.getBoundingClientRect(); const t = document.createElement("div"); t.className = "tip"; t.id = "tip"; t.textContent = el.dataset.tip; t.style.left = r.right + 10 + "px"; t.style.top = r.top + 10 + "px"; document.body.append(t); };
    el.onmouseleave = () => $("#tip")?.remove();
  });
}
function markNav() { const v = location.hash.slice(2).split("/")[0] || "inicio"; $$(".nav-item[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === v)); }
function go(view, arg) { location.hash = `#/${view}${arg ? "/" + arg : ""}`; }
async function route() {
  $("#tip")?.remove();
  const [view, arg] = location.hash.slice(2).split("/");
  const v = VIEWS[view] ? view : "inicio";
  markNav();
  const el = $("#view");
  el.className = "view" + (v === "chat" ? " flush" : "");
  el.innerHTML = `<div class="empty"><div class="spinner"></div></div>`;
  try { await VIEWS[v](el, arg ? decodeURIComponent(arg) : undefined); }
  catch (e) { el.innerHTML = empty("⚠️", `No se pudo cargar: ${esc(e.message)}`); }
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ Marca blanca
function applyWL() {
  const w = S.wl;
  document.title = w.product_name || "Shelton Studio";
  const r = document.documentElement.style;
  if (w.accent) { r.setProperty("--accent", w.accent); r.setProperty("--accent-soft", w.accent + "24"); r.setProperty("--accent-glow", w.accent + "59"); }
  if (w.accent2) r.setProperty("--accent-2", w.accent2);
  const logo = $("#logo");
  if (logo) logo.innerHTML = w.logo_url ? `<img src="${esc(w.logo_url)}" alt="">` : esc((w.product_name || "S")[0]);
  if ($("#wl-name")) { $("#wl-name").textContent = w.product_name; $("#wl-tag").textContent = w.tagline; }
}

// ------------------------------------------------------------------ Eventos en vivo y avisos
function note(text, kind, link) { S.notes.unshift({ text, kind, link, at: new Date().toISOString() }); S.notes = S.notes.slice(0, 60); S.unread++; $("#bell-count").textContent = S.unread > 99 ? "99+" : (S.unread || ""); }
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "post") {
      if (ev.post.status !== "generating") S.progress.delete(ev.post.id);
      const prev = S.posts.get(ev.post.id);
      refreshPost(ev.post);
      if (window.agentAsk) agentAsk(ev.post, prev);
      if (ev.post.status === "failed" && prev?.status !== "failed") note(`Falló un post: ${esc(ev.post.error || ev.post.idea)}`, "bad", `galeria`);
      if (!prev && location.hash.startsWith("#/galeria") && galleryMatches(ev.post)) $("#grid")?.prepend(tile(ev.post, true));
      updateCounts();
    } else if (ev.type === "progress") {
      S.progress.set(ev.id, ev.text); const p = S.posts.get(ev.id); if (p) refreshPost(p);
    } else if (ev.type === "learned") {
      learnedBanner(ev, true);
    } else if (ev.type === "approval") {
      if (ev.approval.status === "pending") { note(`Nueva aprobación: ${esc(ev.approval.title)}`, "warn", "aprobaciones"); toast(`<b>Aprobación pendiente</b><br>${esc(ev.approval.title)}`, "warn", "🛂", `<button class="btn sm primary" onclick="go('aprobaciones')">Revisar</button>`); }
      updateCounts();
    } else if (ev.type === "findings" && ev.new) {
      if (window.agentAskFindings) agentAskFindings(ev.new);
      note(`El scout encontró ${ev.new} novedades`, "", "tendencias"); toast(`🔭 El scout encontró <b>${ev.new}</b> tendencias/herramientas nuevas.`, "", "🔭", `<button class="btn sm" onclick="go('tendencias')">Ver</button>`); updateCounts();
    } else if (ev.type === "audit") {
      note(`Revisión del equipo: ${ev.issues} problema(s)`, ev.issues ? "warn" : "good", "memoria");
      toast(`<b>Shelton revisó al equipo</b><br>${esc(ev.summary)}`, ev.issues ? "warn" : "good", "🧠", `<button class="btn sm" onclick="go('memoria')">Ver revisión</button>`);
    } else if (ev.type === "subject" || ev.type === "subject_failed") {
      if (location.hash.startsWith("#/banco")) VIEWS.banco($("#view"));
      if (ev.type === "subject_failed") toast("No se pudo crear la referencia: " + esc(ev.error), "bad");
    } else if (ev.type === "campaign" && location.hash.startsWith("#/campanas")) { VIEWS.campanas($("#view")); }
  };
  es.onerror = () => { es.close(); setTimeout(connectEvents, 3000); };
}
function learnedBanner(ev, global) {
  const ok = ev.applied;
  const msg = ok ? `<b>${esc(ev.agent_name || agentName(ev.agent))}</b> guardó en memoria: «${esc(ev.title)}» · <span class="chip good">Aplicado</span>`
    : `<b>${esc(ev.agent_name || agentName(ev.agent))}</b> NO aplicó «${esc(ev.title)}» · <span class="chip warn">${ev.status === "conflict" ? "Contradicción" : "Pendiente"}</span>${ev.reason ? `<div class="small muted" style="margin-top:6px">${esc(ev.reason)}</div>` : ""}`;
  if (global) { toast(msg, ok ? "good" : "warn", "🧠", ok ? "" : `<button class="btn sm primary" onclick="go('memoria')">Decidir</button>`); note(msg, ok ? "good" : "warn", "memoria"); }
  return `<div class="learned ${ok ? "ok" : "conflict"}"><span>🧠</span><div>${msg}${ok ? "" : ` <a href="#/memoria">Decidir en Memoria →</a>`}</div></div>`;
}
$("#bell").onclick = (e) => {
  e.stopPropagation(); $("#popover")?.remove(); S.unread = 0; $("#bell-count").textContent = "";
  const r = $("#bell").getBoundingClientRect(), p = document.createElement("div");
  p.className = "popover"; p.id = "popover"; p.style.top = r.bottom + 8 + "px"; p.style.right = "16px";
  p.innerHTML = S.notes.length ? S.notes.map((n, i) => `<div class="p-item" data-i="${i}">${n.text}<div class="faint small" style="margin-top:4px">${when(n.at)}</div></div>`).join("") : `<div class="empty small">Sin avisos todavía.</div>`;
  $$(".p-item", p).forEach((it) => it.onclick = () => { const n = S.notes[+it.dataset.i]; p.remove(); if (n.link) go(n.link); });
  document.body.append(p);
};
document.addEventListener("click", (e) => { if (!e.target.closest("#popover")) $("#popover")?.remove(); });

async function updateCounts() {
  clearTimeout(updateCounts.t);
  updateCounts.t = setTimeout(async () => {
    try {
      const [rev, appr, find, mem] = await Promise.all([api("/api/posts?status=ready&limit=1"), api("/api/approvals"), api("/api/findings"), api("/api/training?status=conflict,pending")]);
      const set = (k, v) => $$(`[data-count="${k}"]`).forEach((e) => e.textContent = v || "");
      set("review", rev.total); set("approvals", appr.length); set("findings", find.length); set("memory", mem.length);
    } catch {}
  }, 500);
}
async function pollHealth() {
  try {
    const h = await api("/api/health");
    const ok = h.engine?.ok && h.claude;
    $("#local-dot").className = "dot " + (ok ? "ok" : h.engine?.ok || h.claude ? "warn" : "bad");
    $("#local-text").textContent = h.engine?.engine === "mock" ? "Demo" : "Local";
    $("#local-pill").title = `Claude: ${h.claude ? "OK" : "falta clave"} · Motor: ${h.engine?.ok ? "OK" : (h.engine?.error || "revisar")}`;
    loadMode();
  } catch { $("#local-dot").className = "dot bad"; }
}
$("#local-pill").onclick = () => go("ajustes");

// ------------------------------------------------------------------ Buscador (Ctrl/Cmd + K)
function openCmdK() {
  if ($("#cmdk")) return;
  const box = document.createElement("div");
  box.className = "cmdk"; box.id = "cmdk";
  box.innerHTML = `<div class="box"><input id="cmdk-in" placeholder="Busca posts, modelos, conversaciones o escribe a dónde ir…" autocomplete="off"><div class="res" id="cmdk-res"></div></div>`;
  document.body.append(box);
  const input = $("#cmdk-in"), res = $("#cmdk-res");
  let items = [], idx = 0;
  const pages = NAV.filter((n) => n !== "-").map((n) => ({ type: "page", id: n[0], title: n[2], ico: n[1] }));
  const draw = () => {
    res.innerHTML = items.map((it, i) => `<div class="r ${i === idx ? "on" : ""}" data-i="${i}">${it.image_url ? `<img src="${esc(it.image_url)}">` : `<span class="conn-ico" style="width:36px;height:36px">${icon(it.ico || (it.type === "conversation" ? "chat" : it.type === "subject" ? "users" : "gallery"), 18)}</span>`}<div class="grow"><div class="ellipsis">${esc(it.title)}</div><div class="faint small">${{ page: "Ir a", post: "Post", subject: "Banco de modelos", conversation: "Conversación" }[it.type]}</div></div></div>`).join("") || `<div class="empty small">Sin resultados</div>`;
    $$(".r", res).forEach((r) => r.onclick = () => pick(items[+r.dataset.i]));
  };
  const pick = (it) => { box.remove(); if (!it) return; if (it.type === "page") go(it.id); else if (it.type === "post") openPost(it.id); else if (it.type === "subject") go("banco"); else go("chat", it.id); };
  const search = async () => {
    const q = input.value.trim().toLowerCase();
    items = pages.filter((p) => !q || p.title.toLowerCase().includes(q));
    if (q.length >= 2) { try { items = items.concat(await api(`/api/search?q=${encodeURIComponent(q)}`)); } catch {} }
    idx = 0; draw();
  };
  input.oninput = () => { clearTimeout(search.t); search.t = setTimeout(search, 160); };
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown") { idx = Math.min(idx + 1, items.length - 1); draw(); e.preventDefault(); }
    if (e.key === "ArrowUp") { idx = Math.max(idx - 1, 0); draw(); e.preventDefault(); }
    if (e.key === "Enter") pick(items[idx]);
    if (e.key === "Escape") box.remove();
  };
  box.onclick = (e) => { if (e.target === box) box.remove(); };
  input.focus(); search();
}
$("#search-open").onclick = openCmdK;
document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openCmdK(); } if (e.key === "Escape") { closeDrawer(); closeModal(); } });

// ------------------------------------------------------------------ Modal y panel lateral
function modal(title, body, foot = "", lg = false) {
  const m = $("#modal");
  m.classList.remove("hidden");
  m.innerHTML = `<div class="box ${lg ? "lg" : ""}"><div class="m-head"><h2>${title}</h2><div class="grow"></div><button class="icon-btn" id="m-x">${icon("x")}</button></div><div class="m-body">${body}</div>${foot ? `<div class="m-foot">${foot}</div>` : ""}</div>`;
  $("#scrim").classList.add("on");
  $("#m-x").onclick = closeModal;
  return $(".box", m);
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modal").innerHTML = ""; if (!$("#drawer").classList.contains("on")) $("#scrim").classList.remove("on"); }
function closeDrawer() { $("#drawer").classList.remove("on"); if ($("#modal").classList.contains("hidden")) $("#scrim").classList.remove("on"); }
$("#scrim").onclick = () => { closeModal(); closeDrawer(); };

// ------------------------------------------------------------------ Gráficas SVG (sin librerías)
function spark(values, color = "var(--accent)") {
  const v = values.length ? values : [0], max = Math.max(...v, 1), w = 300, h = 54, step = w / Math.max(v.length - 1, 1);
  const pts = v.map((y, i) => `${(i * step).toFixed(1)},${(h - 6 - (y / max) * (h - 14)).toFixed(1)}`);
  const id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg class="k-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="M0,${h} L${pts.join(" L")} L${w},${h} Z" fill="url(#${id})"/><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}
function donut(parts) {
  const total = parts.reduce((a, p) => a + p.v, 0) || 1; let acc = 0; const r = 52, c = 2 * Math.PI * r;
  const segs = parts.filter((p) => p.v).map((p) => { const len = (p.v / total) * c; const s = `<circle r="${r}" cx="70" cy="70" fill="none" stroke="${p.color}" stroke-width="18" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-acc}" transform="rotate(-90 70 70)"/>`; acc += len; return s; }).join("");
  return `<div class="donut-wrap"><svg width="140" height="140" viewBox="0 0 140 140"><circle r="${r}" cx="70" cy="70" fill="none" stroke="var(--panel-3)" stroke-width="18"/>${segs}<text x="70" y="66" text-anchor="middle" fill="var(--text)" font-size="24" font-weight="800">${fmtN(total === 1 && !parts.some((p) => p.v) ? 0 : total)}</text><text x="70" y="86" text-anchor="middle" fill="var(--muted)" font-size="11">posts</text></svg><div class="legend">${parts.map((p) => `<div><span style="background:${p.color}"></span>${esc(p.label)} · <b>${fmtN(p.v)}</b></div>`).join("")}</div></div>`;
}
const delta = (a, b, inverse) => { if (!b) return ""; const d = Math.round(((a - b) / b) * 100); if (!d) return `<span class="delta">=</span>`; const up = d > 0; return `<span class="delta ${up !== !!inverse ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(d)}%</span>`; };

// ------------------------------------------------------------------ INICIO
VIEWS.inicio = async (el) => {
  const days = +(localStorage.getItem("days") || 7);
  const [m, agents, wl] = await Promise.all([api(`/api/metrics?days=${days}`), api("/api/agents"), S.wl]);
  S.agents = agents;
  const k = m.kpis, s = m.series;
  const date = new Date().toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const per = Object.fromEntries(m.agents.map((a) => [a.key, a]));
  const statusParts = [["ready", "Para revisar", "#f59e0b"], ["approved", "Aprobados", "#22c55e"], ["published", "Publicados", "#38bdf8"], ["rejected", "Rechazados", "#ef4444"], ["failed", "Fallidos", "#7f1d1d"], ["generating", "Generando", "#8b5cf6"], ["queued", "En cola", "#4b4566"]]
    .map(([key, label, color]) => ({ label, color, v: m.by_status[key] || 0 }));
  const platMax = Math.max(1, ...Object.values(m.by_platform));
  const postMax = Math.max(1, ...s.map((d) => d.posts));
  el.innerHTML = `
  <div class="hero"><div class="h-ico">✨</div><div><h1>Hola, <span>equipo</span></h1><div class="muted">${esc(date)}</div></div>
    <div class="actions"><div class="seg" id="range">${[7, 30, 90].map((d) => `<button class="${d === days ? "on" : ""}" data-d="${d}">${d} días</button>`).join("")}</div>
    <button class="btn primary" onclick="go('chat')">${icon("chat", 18)} Hablar con ${esc(S.wl.chief_name || "Shelton")}</button></div></div>
  <div class="grid g4">
    ${kpi("Ejecuciones hechas", fmtN(k.runs), delta(k.runs, k.runs_prev), "check", s.map((d) => d.runs), "var(--accent)", `${days} días`)}
    ${kpi("Ejecuciones fallidas", fmtN(k.failed), `<a href="#/actividad">Ver fallidas</a>`, "alert", s.map((d) => d.failed), "#ef4444")}
    ${kpi("Aprobaciones pendientes", fmtN(k.pending_approvals), `<a href="#/aprobaciones">Ir a Aprobaciones</a> · ${fmtN(k.to_review)} posts para revisar`, "hourglass", null)}
    ${kpi("Tokens", fmtN(k.tokens), `${fmtMoney(k.cost)} estimado ${delta(k.cost, k.cost_prev, true)}`, "zap", s.map((d) => d.tokens), "#a78bfa")}
  </div>
  <div class="grid g4" style="margin-top:16px">
    ${kpi("Posts listos", fmtN(k.posts), `${k.per_day}/día ${delta(k.posts, k.posts_prev)}`, "gallery", s.map((d) => d.posts), "var(--accent-2)")}
    ${kpi("Nota media del juez", k.avg_qa ? k.avg_qa : "—", `${k.qa_pass_rate}% pasa a la primera`, "sparkle", null)}
    ${kpi("Tasa de aprobación", (k.approval_rate || 0) + "%", "de lo que revisa el equipo", "approve", null)}
    ${kpi("Tiempo por post", k.avg_seconds_per_post ? `${k.avg_seconds_per_post}s` : "—", `cola: ${m.queue.pending} pendientes · ${m.queue.running} en curso`, "hourglass", null)}
  </div>
  <div class="grid g3" style="margin-top:16px">
    <div class="card"><div class="card-title"><h3>Posts por día</h3><span class="right muted small">${days} días</span></div>
      <div class="bars">${s.slice(-Math.min(days, 30)).map((d) => `<div class="b" title="${d.day}: ${d.posts}"><i style="height:${(d.posts / postMax) * 130 + 3}px"></i><small>${days <= 7 ? new Date(d.day + "T12:00").toLocaleDateString("es-ES", { weekday: "short" }) : d.day.slice(8)}</small></div>`).join("")}</div></div>
    <div class="card"><div class="card-title"><h3>Estado del contenido</h3></div>${donut(statusParts)}</div>
    <div class="card"><div class="card-title"><h3>Por red y formato</h3></div>
      ${Object.entries(m.by_platform).sort((a, b) => b[1] - a[1]).map(([p, v]) => `<div class="hbar"><span class="ellipsis">${esc(S.platforms.find((x) => x.key === p)?.name || p)}</span><div class="track"><i style="width:${(v / platMax) * 100}%"></i></div><b>${v}</b></div>`).join("") || `<div class="muted small">Aún sin datos.</div>`}
      <div class="row" style="margin-top:12px">${Object.entries(m.by_media).map(([t, v]) => `<span class="chip accent">${{ image: "Imágenes", video: "Videos", reel: "Reels" }[t] || t}: ${v}</span>`).join("")}</div></div>
  </div>
  <div class="section-title">Agentes activos <span class="muted small" style="font-weight:400">· ${agents.length} en el equipo</span><div class="grow"></div><button class="btn sm" onclick="go('agentes')">Ver todos</button></div>
  <div class="grid g2">${agents.slice(0, 8).map((a) => agentCard(a, per[a.key])).join("")}</div>`;
  $$("#range button", el).forEach((b) => b.onclick = () => { localStorage.setItem("days", b.dataset.d); route(); });
  bindAgentCards(el);
};
function kpi(label, value, sub, ico, series, color, extra) {
  return `<div class="card kpi"><div class="k-head"><div class="k-label">${label}${extra ? ` (${extra})` : ""}</div><div class="k-ico">${icon(ico, 18)}</div></div><div class="k-value">${value}</div><div class="k-sub">${sub}</div>${series ? spark(series, color) : '<div style="height:20px"></div>'}</div>`;
}
function agentCard(a, stats) {
  const runs = stats ? stats.runs : a.runs;
  return `<div class="card agent-card">${avatar(a.key)}<div class="info"><div class="row" style="gap:8px"><b>${esc(a.name)}</b><div class="grow"></div><span class="chip ${a.enabled ? "good" : ""}">${a.enabled ? "Activo" : "Pausado"}</span></div>
    <div class="meta">${esc(a.role)} · v${a.version} · ${fmtN(runs)} ejecuciones${a.lessons_active ? ` · 🧠 ${a.lessons_active} lecciones` : ""}</div>
    <div class="meta">${a.last ? `Última actividad ${when(a.last)}` : "Sin actividad todavía"}${stats?.cost ? ` · ${fmtMoney(stats.cost)}` : ""}</div>
    <div class="acts"><button class="btn sm" data-chat="${a.key}">Chatear</button><button class="btn sm ghost" data-ficha="${a.key}">Ficha</button></div></div></div>`;
}
function bindAgentCards(el) {
  $$("[data-chat]", el).forEach((b) => b.onclick = () => { S.chatAgent = b.dataset.chat; S.conv = null; go("chat"); });
  $$("[data-ficha]", el).forEach((b) => b.onclick = () => openAgent(b.dataset.ficha));
}

// ------------------------------------------------------------------ CHAT
const SUGGEST = [
  ["📸", "Hazme 4 posts para Instagram de una cafetería de especialidad, estilo cálido y natural"],
  ["👩", "Crea un personaje llamado Valentina: 26 años, pelo rizado castaño, estilo urbano, influencer fitness"],
  ["🎬", "Haz un reel de 3 escenas de Valentina entrenando al amanecer y ponle voz femenina con energía"],
  ["🚀", "Planifica 50 posts para esta semana mezclando Instagram, TikTok y Telegram"],
  ["🏎️", "Investiga autos exóticos de este año y dame opciones para crear un modelo en el banco"],
  ["🧠", "Aprende que en TikTok siempre usamos 4 hashtags y un gancho con pregunta"],
];
VIEWS.chat = async (el, convId) => {
  if (convId) S.conv = convId; else if (!location.hash.includes("/chat/")) { /* mantiene la conversación actual */ }
  if (!S.engines) S.engines = await api("/api/engines");
  el.innerHTML = `<div class="chat-wrap">
    <aside class="threads"><div class="th-head"><button class="btn primary block" id="new-chat">${icon("plus", 18)} Nueva conversación</button>
      <input id="th-search" placeholder="Buscar en títulos…"></div><div class="th-list" id="th-list"></div></aside>
    <section class="chat"><div class="chat-head">${avatar(S.chatAgent, true)}
      <select id="agent-sel" title="Con quién hablas">${S.agents.map((a) => `<option value="${a.key}" ${a.key === S.chatAgent ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
      <div class="grow"></div><span class="muted small">Motor</span>
      <select id="engine-sel" title="Motor de imagen de esta conversación">${S.engines.image.map((e) => `<option value="${esc(e.id)}">${esc(e.label)}${e.local ? "" : " ☁"}</option>`).join("")}</select>
      <button class="icon-btn" id="engine-help" title="¿Qué motor elijo?">?</button>
      <button class="btn sm" id="video-models" title="Elegir el modelo de video (Veo, Sora, Seedance, Kling, Wan…)">🎬 Video</button></div>
      <div class="msgs" id="msgs"></div>
      <form class="composer" id="composer"><div class="composer-box"><div class="attachments" id="attachments"></div>
        <textarea id="input" rows="1" placeholder="Pídele a ${esc(agentName(S.chatAgent))} lo que quieras…"></textarea>
        <div class="composer-row"><label class="icon-btn" title="Subir imagen para editar o usar como referencia">${icon("clip", 19)}<input type="file" id="file-in" accept="image/*" multiple hidden></label>
        <span class="faint small">Enter para enviar · Shift+Enter nueva línea</span><div class="grow"></div><button class="btn primary" id="send">${icon("send", 17)} Enviar</button></div></div></form></section></div>`;
  $("#engine-sel").value = S.chatEngine;
  $("#agent-sel").onchange = (e) => { S.chatAgent = e.target.value; S.conv = null; go("chat"); route(); };
  $("#engine-sel").onchange = (e) => { S.chatEngine = e.target.value; toast(`Motor: <b>${esc(e.target.selectedOptions[0].text)}</b>. Se aplica desde tu próximo mensaje.`, "good", "🎛️"); };
  $("#engine-help").onclick = engineHelp;
  $("#video-models").onclick = () => openVideoModels();
  $("#new-chat").onclick = () => { S.conv = null; S.chatEngine = ""; go("chat"); route(); };
  $("#th-search").oninput = () => renderThreads($("#th-search").value);
  const input = $("#input");
  input.oninput = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 220) + "px"; };
  input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } };
  $("#composer").onsubmit = (e) => { e.preventDefault(); sendMessage(); };
  $("#file-in").onchange = async (e) => {
    for (const f of e.target.files) { const fd = new FormData(); fd.append("file", f); try { const p = await api("/api/upload", { method: "POST", body: fd }); S.uploads.push(p); S.posts.set(p.id, p); const im = document.createElement("img"); im.src = p.image_url; $("#attachments").append(im); } catch (err) { toast(esc(err.message), "bad"); } }
    e.target.value = "";
  };
  renderThreads();
  if (S.conv) await loadConv(S.conv); else welcome();
  input.focus();
};
function engineHelp() {
  const e = S.engines;
  modal("¿Qué motor elijo?", `<p class="muted" style="margin:0">Todo corre en <b>tu GPU</b> (ComfyUI) salvo las conexiones marcadas con ☁. Puedes cambiarlo aquí o pedírselo a ${esc(S.wl.chief_name || "Shelton")}: «usa calidad máxima».</p>
    <div class="grid" style="gap:10px">${e.image.map((x) => `<div class="card" style="padding:14px"><b>${esc(x.label)}</b> ${x.local ? '<span class="chip good">Local</span>' : '<span class="chip warn">Nube · con costo</span>'}<div class="muted small" style="margin-top:4px">${esc(x.description)}</div></div>`).join("")}</div>
    <h3 style="margin:8px 0 0">Video</h3><div class="grid" style="gap:10px">${e.video.map((x) => `<div class="card" style="padding:14px"><b>${esc(x.label)}</b><div class="muted small">${esc(x.description)}</div></div>`).join("")}</div>
    <div class="hint">Para agregar otra GPU, un servidor remoto o una API en la nube ve a <a href="#/conexiones" onclick="closeModal()">Conexiones</a>. Los modelos de cada perfil se configuran en <code>.env</code> o en la conexión ComfyUI.</div>`);
}
async function renderThreads(q = "") {
  const list = await api(`/api/conversations?agent=${S.chatAgent}${q ? "&q=" + encodeURIComponent(q) : ""}`).catch(() => []);
  const groups = { Hoy: [], Ayer: [], "Esta semana": [], Antes: [] };
  const now = new Date(); const day = (d) => Math.floor((new Date(now.toDateString()) - new Date(new Date(d).toDateString())) / 864e5);
  list.forEach((c) => { const d = day(c.updated_at); (d <= 0 ? groups.Hoy : d === 1 ? groups.Ayer : d < 7 ? groups["Esta semana"] : groups.Antes).push(c); });
  $("#th-list").innerHTML = Object.entries(groups).filter(([, v]) => v.length).map(([g, v]) => `<div class="th-group">${g}</div>` + v.map((c) => `<div class="th-item ${c.id === S.conv ? "on" : ""}" data-id="${c.id}"><b>${esc(c.title)}</b><small>${when(c.updated_at)}</small></div>`).join("")).join("") || `<div class="empty small">Sin conversaciones con ${esc(agentName(S.chatAgent))}.</div>`;
  $$(".th-item").forEach((t) => t.onclick = () => go("chat", t.dataset.id));
}
function welcome() {
  const chief = S.chatAgent === "shelton", a = S.agents.find((x) => x.key === S.chatAgent);
  $("#msgs").innerHTML = `<div class="welcome" id="welcome"><div class="logo">${S.wl.logo_url && chief ? `<img src="${esc(S.wl.logo_url)}">` : esc((a?.name || "S")[0])}</div>
    <h1>${chief ? `Hola, soy ${esc(S.wl.chief_name || "Shelton")}.` : esc(a?.name || "")}</h1>
    <p>${chief ? "Dirijo a todo el equipo: director creativo, motores de imagen y video, juez de calidad, copywriter, locutor, sonido, planificador y scouts. Dime qué necesitas y lo dejo listo para publicar." : `${esc(a?.role || "")}. Puedes preguntarle cómo trabaja o enseñarle algo («aprende que…»): queda guardado en su memoria.`}</p>
    ${chief ? `<div class="suggest">${SUGGEST.map(([e, t]) => `<button data-s="${esc(t)}"><span>${e}</span><span>${esc(t)}</span></button>`).join("")}</div>` : ""}</div>`;
  $$("[data-s]").forEach((b) => b.onclick = () => { $("#input").value = b.dataset.s; $("#input").focus(); });
}
async function loadConv(id) {
  try {
    const c = await api(`/api/conversations/${id}`);
    S.conv = id; S.chatAgent = c.agent; S.chatEngine = c.engine || ""; if ($("#engine-sel")) $("#engine-sel").value = S.chatEngine;
    $("#msgs").innerHTML = "";
    c.messages.forEach((m) => addMsg(m.role === "user" ? "user" : "bot", m.text, m.post_ids || []));
    if (!c.messages.length) welcome();
    renderThreads();
  } catch { S.conv = null; welcome(); }
}
function addMsg(role, text, postIds = []) {
  $("#welcome")?.remove();
  const m = document.createElement("div"); m.className = `msg ${role}`;
  m.innerHTML = (role === "bot" ? avatar(S.chatAgent, true) : "") + `<div class="body">${role === "bot" ? `<div class="who">${esc(agentName(S.chatAgent))}</div>` : ""}</div>`;
  const body = $(".body", m);
  if (text) { const b = document.createElement("div"); b.className = "bubble"; b.textContent = text; body.append(b); }
  if (postIds.length) body.append(thumbs(postIds));
  $("#msgs").append(m); $("#msgs").scrollTop = 1e9;
  return body;
}
function thumbs(ids) {
  const box = document.createElement("div"); box.className = "thumbs";
  ids.forEach(async (id) => { let p = S.posts.get(id); if (!p) { try { p = await api(`/api/posts/${id}`); S.posts.set(id, p); } catch { return; } } if (!$(`.tile[data-id="${id}"]`, box)) box.append(tile(p)); });
  return box;
}
async function sendMessage() {
  const input = $("#input"), text = input.value.trim();
  if (!text || S.busy) return;
  S.busy = true; $("#send").disabled = true;
  const uploads = S.uploads.map((p) => p.id);
  addMsg("user", text, uploads);
  input.value = ""; input.style.height = "auto"; S.uploads = []; $("#attachments").innerHTML = "";
  const body = addMsg("bot", "");
  const steps = document.createElement("div"); steps.className = "steps"; body.append(steps);
  const step = (t) => { $$(".step", steps).forEach((s) => s.classList.remove("live")); const s = document.createElement("div"); s.className = "step live"; s.textContent = t; steps.append(s); $("#msgs").scrollTop = 1e9; };
  step("Pensando…");
  const shown = new Set();
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: S.conv, upload_ids: uploads, agent: S.chatAgent, engine: S.chatEngine }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "conversation") { if (S.conv !== ev.id) { S.conv = ev.id; history.replaceState(null, "", `#/chat/${ev.id}`); renderThreads(); } }
        else if (ev.type === "status") step(ev.text);
        else if (ev.type === "posts") { const fresh = ev.ids.filter((x) => !shown.has(x)); fresh.forEach((x) => shown.add(x)); if (fresh.length) body.append(thumbs(fresh)); }
        else if (ev.type === "mode_tag") { const t = document.createElement("div"); t.innerHTML = `<span class="chip accent" style="font-size:13px;padding:5px 12px">${esc(ev.text)}</span>`; body.insertBefore(t.firstChild, steps); }
        else if (ev.type === "engine") { S.chatEngine = ev.id; $("#engine-sel").value = ev.id; step("Motor de imagen cambiado"); }
        else if (ev.type === "learned") { const d = document.createElement("div"); d.innerHTML = learnedBanner(ev, false); body.append(d.firstChild); }
        else if (ev.type === "subjects_changed") step("Banco de modelos actualizado");
        else if (ev.type === "message") { const b = document.createElement("div"); b.className = "bubble"; b.textContent = ev.text || "(sin respuesta)"; body.insertBefore(b, steps.nextSibling); const rest = ev.post_ids.filter((x) => !shown.has(x)); if (rest.length) body.append(thumbs(rest)); }
        else if (ev.type === "error") { const b = document.createElement("div"); b.className = "bubble"; b.style.borderColor = "var(--bad)"; b.textContent = "⚠ " + ev.text; body.append(b); }
      }
    }
  } catch (err) { step("⚠ " + err.message); }
  $$(".step", steps).forEach((s) => s.classList.remove("live"));
  S.busy = false; $("#send").disabled = false; input.focus();
}

// ------------------------------------------------------------------ Tarjetas de post
function tile(p, selectable = false) {
  const el = document.createElement("div");
  el.className = "tile" + (S.selected.has(p.id) ? " sel" : ""); el.dataset.id = p.id; el.dataset.sel = selectable ? "1" : "";
  el.innerHTML = tileHTML(p, selectable);
  el.onclick = (e) => { if (selectable && (e.target.closest(".check") || e.shiftKey || e.ctrlKey || e.metaKey)) { toggleSel(p.id, el); return; } openPost(p.id); };
  S.posts.set(p.id, p);
  return el;
}
function tileHTML(p, selectable) {
  const busy = p.status === "queued" || p.status === "generating", prog = S.progress.get(p.id);
  const media = p.media_type === "reel" ? `${icon("film", 13)} Reel` : p.media_type === "video" ? `${icon("play", 12)} Video` : "";
  const img = p.image_url && !busy ? `<div class="img" style="background-image:url('${esc(p.image_url)}')">${media ? `<span class="media-tag">${media}${p.duration ? ` · ${Math.round(p.duration)}s` : ""}${p.audio_url ? " · 🔊" : ""}</span>` : ""}</div>`
    : `<div class="img">${busy ? `<div><div class="spinner"></div>${esc(prog || STATUS[p.status][0])}</div>` : esc(p.error || STATUS[p.status]?.[0] || p.status)}</div>`;
  const judge = p.status === "ready" && p.qa_score && !p.qa_passed ? `<span class="chip bad" title="El juez la reprobó; decides tú">Juez ✖</span>` : "";
  const mw = p.made_with || {}, short = (s) => String(s || "").split("/").pop();
  const mtag = mw.video ? `🎬 ${short(mw.video)}` : mw.image ? `🖼️ ${short(mw.image)}` : "";
  return `${selectable ? '<div class="check"></div>' : ""}${mtag ? `<span class="model-tag" title="${esc(Object.entries(mw).map(([k, v]) => `${k}: ${v}`).join(" · "))}">${esc(mtag)}</span>` : ""}${img}<div class="meta">${chip(p.status)}${judge}<span class="idea">${esc(p.platform_name)} · ${esc(p.idea)}</span>${p.qa_score ? `<span class="score ${scoreCls(p.qa_score)}">${Math.round(p.qa_score)}</span>` : ""}</div>`;
}
function refreshPost(p) {
  const prev = S.posts.get(p.id);
  S.posts.set(p.id, p);
  $$(`.tile[data-id="${p.id}"]`).forEach((el) => el.innerHTML = tileHTML(p, el.dataset.sel === "1"));
  const d = $("#drawer");
  if (!d.classList.contains("on") || d.dataset.id !== p.id) return;
  // Solo se redibuja si cambió algo importante, y nunca mientras escribes.
  const changed = !prev || prev.status !== p.status || prev.image_url !== p.image_url || prev.video_url !== p.video_url
    || prev.caption !== p.caption || (prev.alternatives || []).length !== (p.alternatives || []).length;
  const typing = d.contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  if (changed && !typing) openPost(p.id, false, true);
  else if (p.status === "generating" || p.status === "queued") { const sp = $(".d-body .img > div", d); if (sp && sp.lastChild) sp.lastChild.textContent = S.progress.get(p.id) || ""; }
}
function toggleSel(id, el) { S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id); el.classList.toggle("sel"); renderSelbar(); }

// ------------------------------------------------------------------ GALERÍA
VIEWS.galeria = async (el, campaign) => {
  el.innerHTML = `<div class="page-head"><div class="page-icon">🖼️</div><div><h1>Galería</h1><p>Todo lo que produjo el equipo. Clic para ver, Ctrl+clic o el recuadro para seleccionar varios.</p></div>
    <div class="actions"><button class="btn" id="export">${icon("download", 17)} Descargar ZIP</button></div></div>
    <form class="quick card" id="quick" style="padding:12px"><input id="q-idea" placeholder="Generación rápida: describe la imagen o el clip…" required>
      <select id="q-plat">${S.platforms.filter((p) => p.key !== "original").map((p) => `<option value="${p.key}">${esc(p.name)} (${p.aspect})</option>`).join("")}</select>
      <select id="q-format"><option value="image">Imagen</option><option value="video">Video (imagen → clip)</option></select>
      <input id="q-count" type="number" min="1" max="12" value="1" style="width:76px" title="Cantidad"><button class="btn primary">${icon("sparkle", 17)} Generar</button></form>
    <div class="toolbar"><div class="seg" id="st">${[["", "Todo"], ["queued,generating", "En proceso"], ["ready", "Para revisar"], ["approved", "Aprobados"], ["published", "Publicados"], ["failed,rejected", "Fallidos"]].map(([v, l]) => `<button data-v="${v}" class="${S.galleryFilter === v ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="seg" id="mt">${[["", "Todo"], ["image", "Imágenes"], ["video", "Videos"], ["reel", "Reels"]].map(([v, l]) => `<button data-v="${v}" class="${S.galleryMedia === v ? "on" : ""}">${l}</button>`).join("")}</div>
      ${campaign ? `<span class="chip accent">Campaña ${esc(campaign)} <a href="#/galeria" style="margin-left:6px">✕</a></span>` : ""}</div>
    <div id="selbar-slot"></div><div class="post-grid" id="grid"></div><div style="margin-top:16px"><button class="btn block hidden" id="more">Cargar más</button></div>`;
  S.offset = 0;
  $$("#st button").forEach((b) => b.onclick = () => { S.galleryFilter = b.dataset.v; VIEWS.galeria(el, campaign); });
  $$("#mt button").forEach((b) => b.onclick = () => { S.galleryMedia = b.dataset.v; VIEWS.galeria(el, campaign); });
  $("#quick").onsubmit = async (e) => { e.preventDefault(); try { await api("/api/posts", { method: "POST", body: { idea: $("#q-idea").value, platform: $("#q-plat").value, count: +$("#q-count").value, format: $("#q-format").value } }); $("#q-idea").value = ""; toast("En cola. Los verás aparecer aquí en vivo.", "good"); } catch (err) { toast(esc(err.message), "bad"); } };
  $("#export").onclick = () => { const ids = [...S.selected]; location.href = ids.length ? `/api/export?ids=${ids.join(",")}` : `/api/export?status=approved`; if (!ids.length) toast("Descargando los aprobados. Selecciona posts para elegir otros.", ""); };
  $("#more").onclick = () => loadGallery(campaign, true);
  renderSelbar();
  await loadGallery(campaign);
};
function galleryMatches(p) { return (!S.galleryFilter || S.galleryFilter.split(",").includes(p.status)) && (!S.galleryMedia || p.media_type === S.galleryMedia); }
async function loadGallery(campaign, more) {
  if (!more) { S.offset = 0; $("#grid").innerHTML = ""; }
  const q = new URLSearchParams({ limit: 60, offset: S.offset }); if (S.galleryFilter) q.set("status", S.galleryFilter); if (campaign) q.set("campaign_id", campaign);
  const r = await api(`/api/posts?${q}`);
  const items = r.items.filter((p) => !S.galleryMedia || p.media_type === S.galleryMedia);
  items.forEach((p) => $("#grid").append(tile(p, true)));
  S.offset += r.items.length;
  $("#more").classList.toggle("hidden", S.offset >= r.total);
  if (!r.total) $("#grid").outerHTML = `<div id="grid">${empty("🖼️", "Aún no hay nada aquí. Pídele algo a Shelton o usa la generación rápida.")}</div>`;
}
function renderSelbar() {
  const slot = $("#selbar-slot"); if (!slot) return;
  if (!S.selected.size) { slot.innerHTML = ""; return; }
  slot.innerHTML = `<div class="selbar"><b>${S.selected.size} seleccionados</b><div class="grow"></div>
    <button class="btn sm good" data-a="approved">${icon("check", 15)} Aprobar</button><button class="btn sm bad" data-a="rejected">Rechazar</button>
    <button class="btn sm" data-a="reel">${icon("film", 15)} Hacer reel</button><button class="btn sm" data-a="zip">${icon("download", 15)} ZIP</button><button class="btn sm ghost" data-a="clear">Limpiar</button></div>`;
  $$("[data-a]", slot).forEach((b) => b.onclick = async () => {
    const ids = [...S.selected], a = b.dataset.a;
    if (a === "clear") { S.selected.clear(); $$(".tile.sel").forEach((t) => t.classList.remove("sel")); return renderSelbar(); }
    if (a === "zip") return location.href = `/api/export?ids=${ids.join(",")}`;
    if (a === "reel") { const r = await api("/api/reels", { method: "POST", body: { post_ids: ids, idea: "" } }); toast("Montando el reel: las imágenes se animan primero.", "good", "🎬"); S.selected.clear(); renderSelbar(); return openPost(r.id); }
    await Promise.all(ids.map((id) => api(`/api/posts/${id}`, { method: "PATCH", body: { status: a } })));
    toast(`${ids.length} ${a === "approved" ? "aprobados" : "rechazados"}`, a === "approved" ? "good" : "");
    S.selected.clear(); $$(".tile.sel").forEach((t) => t.classList.remove("sel")); renderSelbar();
  });
}

// ------------------------------------------------------------------ DETALLE DE POST
async function openPost(id, refetch = true, keepScroll = false) {
  const scroll = keepScroll ? $("#drawer").scrollTop : 0;
  let p = S.posts.get(id);
  if (refetch || !p) { p = await api(`/api/posts/${id}`); S.posts.set(id, p); }
  const [versions, targets] = await Promise.all([api(`/api/posts/${id}/versions`).catch(() => []), api("/api/targets").catch(() => [])]);
  const d = $("#drawer"); d.dataset.id = id; d.classList.add("on"); $("#scrim").classList.add("on");
  const busy = p.status === "queued" || p.status === "generating", isVid = !!p.video_url;
  const media = busy ? `<div class="tile"><div class="img" style="aspect-ratio:4/5"><div><div class="spinner"></div>${esc(S.progress.get(id) || STATUS[p.status][0])}</div></div></div>`
    : isVid ? `<video class="media-main" src="${esc(p.video_url)}" poster="${esc(p.image_url)}" controls playsinline loop></video>`
    : p.image_url ? `<img class="media-main" src="${esc(p.image_url)}" alt="${esc(p.alt_text)}">` : `<div class="qa-box">${esc(p.error || "Sin imagen")}</div>`;
  const MW = { image: "🖼️ Imagen", edit: "✏️ Edición", video: "🎬 Video", voice: "🎙️ Voz", sound: "🔊 Sonido" };
  const madeWith = Object.entries(p.made_with || {}).map(([k, v]) => `<span class="chip ${k === "video" ? "accent" : k === "voice" || k === "sound" ? "info" : ""}">${MW[k] || esc(k)}: ${esc(v)}</span>`).join("");
  d.innerHTML = `<div class="d-head">${chip(p.status)}<span class="muted small">${esc(p.platform_name)} · ${p.width}×${p.height}${p.duration ? ` · ${p.duration.toFixed(1)} s` : ""}</span>
    ${p.qa_score ? `<span class="score ${scoreCls(p.qa_score)}">Juez ${Math.round(p.qa_score)}/100</span>` : ""}<div class="grow"></div><button class="icon-btn" id="d-x">${icon("x")}</button></div>
  <div class="d-body">${madeWith ? `<div class="row" style="gap:6px">${madeWith}</div>` : ""}${media}
    ${versions.length > 1 ? `<div class="d-sec"><h4>Versiones (nada se borra)</h4><div class="versions">${versions.map((v) => `<img src="${esc(v.image_url)}" data-v="${v.id}" class="${v.id === id ? "cur" : ""}" title="${esc(STATUS[v.status]?.[0])} · ${esc(v.media_type)}">`).join("")}</div></div>` : ""}
    <div class="row"><button class="btn good" id="d-ok">${icon("check", 17)} Aprobar</button><button class="btn bad" id="d-no">Rechazar</button>
      <select id="d-target" style="width:auto">${targets.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("") || '<option value="">Sin destinos (ver Conexiones)</option>'}</select>
      <button class="btn primary" id="d-pub" ${targets.length ? "" : "disabled"}>${icon("send", 16)} Publicar</button>
      <a class="btn ghost" href="${esc(p.video_url || p.image_url)}" download>${icon("download", 16)}</a></div>
    ${p.voiceover ? `<div class="d-sec"><h4>Locución</h4><div class="qa-box">🎙️ ${esc(p.voiceover)}</div></div>` : ""}
    <div class="d-sec"><h4>Texto del post</h4><textarea id="d-cap" rows="6">${esc(p.caption)}</textarea><input id="d-tags" value="${esc((p.hashtags || []).join(" "))}" style="margin-top:8px" placeholder="#hashtags">
      <div class="row" style="margin-top:8px"><button class="btn sm" id="d-copy">Copiar todo</button><button class="btn sm" id="d-save">Guardar cambios</button><span class="faint small">Tus correcciones enseñan al copywriter.</span></div></div>
    <div class="d-sec"><h4>Pedir cambios</h4><textarea id="d-edit" rows="2" placeholder="Ej: cambia el fondo a una playa al atardecer, quita el texto, hazlo estilo óleo…"></textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary sm" id="d-edit-go">${icon("edit", 15)} Editar imagen</button><button class="btn sm" id="d-regen">${icon("refresh", 15)} Regenerar</button>
      ${!isVid ? `<button class="btn sm" id="d-video">${icon("play", 14)} Animar (video)</button>` : ""}</div></div>
    <div class="d-sec"><h4>Voz y sonido</h4><div class="grid g2" style="gap:8px"><input id="d-voice" placeholder="Voz: grave, femenina cálida, robot, anciano…"><input id="d-sfx" placeholder="Sonido: motor V8, moto, olas… (opcional)"></div>
      <textarea id="d-script" rows="2" style="margin-top:8px" placeholder="Guion exacto (vacío = lo escribe el Locutor)"></textarea>
      <div class="row" style="margin-top:8px"><button class="btn sm" id="d-audio">${icon("mic", 15)} Agregar voz${isVid ? "" : " (crea video)"}</button><button class="btn sm ghost" id="d-sfx-only">${icon("wave", 15)} Solo sonido</button></div></div>
    ${p.qa_notes ? `<div class="d-sec"><h4>Opinión del juez (tú decides)</h4><div class="qa-box">${esc(p.qa_notes)}</div></div>` : ""}
    ${(p.alternatives || []).length ? `<div class="d-sec"><h4>Otros intentos guardados</h4><div class="hint" style="margin-bottom:8px">El juez los puntuó más bajo, pero no se borran. Si crees que se equivocó, elige uno.</div><div class="grid" style="gap:8px">${p.alternatives.map((a, i) => `<div class="alt"><img src="${esc(a.image_url)}"><div class="grow"><span class="score ${scoreCls(a.score)}">${a.score}/100</span><div class="muted" style="margin:4px 0 8px">${esc((a.issues || []).join("; ") || a.summary)}</div><button class="btn sm" data-alt="${i}">Usar esta</button></div></div>`).join("")}</div></div>` : ""}
    ${p.error ? `<div class="d-sec"><h4>Error</h4><div class="qa-box" style="color:#fca5a5">${esc(p.error)}</div></div>` : ""}
    ${(p.publish_log || []).length ? `<div class="d-sec"><h4>Publicaciones</h4>${p.publish_log.map((l) => `<div class="small muted">✔ ${esc(l.target)} · ${when(l.at)}</div>`).join("")}</div>` : ""}
    <div class="d-sec"><h4>Idea</h4><div class="muted">${esc(p.idea)}</div></div>
    ${p.prompt ? `<div class="d-sec"><h4>Prompt usado</h4><div class="muted small" style="white-space:pre-wrap">${esc(p.prompt)}</div></div>` : ""}
    <button class="btn bad sm" id="d-del" style="justify-self:start">${icon("trash", 15)} Eliminar este post</button></div>`;
  d.scrollTop = scroll;
  const tags = () => $("#d-tags").value.split(/\s+/).filter(Boolean);
  const run = (fn, msg) => async () => { try { const r = await fn(); if (msg) toast(msg, "good"); return r; } catch (e) { toast(esc(e.message), "bad"); } };
  $("#d-x").onclick = closeDrawer;
  $$("[data-v]", d).forEach((im) => im.onclick = () => openPost(im.dataset.v));
  $$("[data-alt]", d).forEach((b) => b.onclick = run(async () => { await api(`/api/posts/${id}/use-alternative`, { method: "POST", body: { index: +b.dataset.alt } }); openPost(id); }, "Usando la imagen que elegiste"));
  $("#d-ok").onclick = run(() => api(`/api/posts/${id}`, { method: "PATCH", body: { status: "approved" } }), "Aprobado");
  $("#d-no").onclick = run(() => api(`/api/posts/${id}`, { method: "PATCH", body: { status: "rejected" } }), "Rechazado");
  $("#d-pub").onclick = run(async () => { if (!confirm("¿Publicar ahora en " + $("#d-target").selectedOptions[0].text + "?")) return; await api(`/api/posts/${id}/publish`, { method: "POST", body: { target: $("#d-target").value } }); }, "Publicado 🎉");
  $("#d-copy").onclick = () => { navigator.clipboard.writeText(`${$("#d-cap").value}\n\n${tags().join(" ")}`.trim()); toast("Copiado", "good"); };
  $("#d-save").onclick = run(() => api(`/api/posts/${id}`, { method: "PATCH", body: { caption: $("#d-cap").value, hashtags: tags() } }), "Guardado");
  $("#d-edit-go").onclick = run(async () => { const t = $("#d-edit").value.trim(); if (!t) throw new Error("Escribe qué quieres cambiar"); const r = await api(`/api/posts/${id}/edit`, { method: "POST", body: { instruction: t } }); openPost(r.id); }, "Editando… (versión nueva; la original se conserva)");
  $("#d-regen").onclick = run(async () => { const r = await api(`/api/posts/${id}/regenerate`, { method: "POST" }); openPost(r.id); }, "Regenerando…");
  if ($("#d-video")) $("#d-video").onclick = run(async () => { const r = await api(`/api/posts/${id}/video`, { method: "POST", body: { motion: $("#d-edit").value } }); openPost(r.id); }, "Animando con el motor de video…");
  $("#d-audio").onclick = run(async () => { const r = await api(`/api/posts/${id}/audio`, { method: "POST", body: { voice_request: $("#d-voice").value, script: $("#d-script").value, sound_request: $("#d-sfx").value } }); openPost(r.id); }, "El Locutor está trabajando…");
  $("#d-sfx-only").onclick = run(async () => { if (!$("#d-sfx").value.trim()) throw new Error("Describe el sonido"); const r = await api(`/api/posts/${id}/audio`, { method: "POST", body: { voice: false, sound_request: $("#d-sfx").value } }); openPost(r.id); }, "Creando el sonido…");
  $("#d-del").onclick = run(async () => { if (!confirm("¿Eliminar este post? Esta acción es tuya, no del juez.")) return; await api(`/api/posts/${id}`, { method: "DELETE" }); closeDrawer(); $$(`.tile[data-id="${id}"]`).forEach((t) => t.remove()); }, "");
}

// ------------------------------------------------------------------ Switch Local / Auto / Nube
async function loadMode() {
  try { S.mode = await api("/api/mode"); } catch { return; }
  $$("#mode-seg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode.mode));
  const missing = S.mode.routes.filter((r) => !r.ok);
  $("#mode-info").innerHTML = missing.length ? `<span style="color:var(--warn);font-weight:800">!</span>` : "?";
  $("#mode-info").title = missing.length ? `Faltan herramientas en este modo: ${missing.map((r) => r.label).join(", ")}` : "¿Qué herramienta usa cada parte?";
}
async function setMode(mode) {
  S.mode = await api("/api/mode", { method: "PUT", body: { mode } });
  S.engines = null;
  await loadMode();
  const missing = S.mode.routes.filter((r) => !r.ok);
  toast(`Modo <b>${esc(S.mode.label)}</b>${missing.length ? `. Faltan: ${esc(missing.map((r) => r.label).join(", "))}` : ". Todo listo."}`, missing.length ? "warn" : "good", mode === "cloud" ? "☁️" : mode === "local" ? "🖥️" : "🔀",
    `<button class="btn sm" onclick="showModeRoutes()">Ver detalle</button>`);
}
function showModeRoutes() {
  const m = S.mode; if (!m) return;
  modal(`Herramientas · modo ${esc(m.label)}`, `<div class="hint">${{ local: "Todo corre en tus máquinas (ComfyUI con tu GPU y el servidor de voz). Sin costo por imagen o clip.", cloud: "Todo corre con APIs en la nube (OpenRouter, fal.ai, ElevenLabs). Sin GPU, cobra por uso.", auto: "Usa lo que esté conectado, priorizando lo local." }[m.mode]} El cerebro (Shelton y sus agentes) siempre usa tu clave de Claude u OpenRouter.</div>
    ${m.routes.map((r) => `<div class="card" style="padding:12px;display:flex;gap:12px;align-items:center"><span class="dot ${r.ok ? "ok" : "bad"}"></span><b style="width:90px">${esc(r.label)}</b><div class="grow ${r.ok ? "" : ""}" style="color:${r.ok ? "inherit" : "#fca5a5"}">${esc(r.tool)}</div>${r.ok ? `<span class="chip ${r.local ? "good" : "info"}">${r.local ? "Local" : "Nube"}</span>` : ""}</div>`).join("")}`,
    `<button class="btn" onclick="closeModal();openApis()">${icon("key", 16)} Abrir APIs</button>`);
}

// ------------------------------------------------------------------ Inicio de la app
(async function init() {
  $("#i-search").innerHTML = icon("search", 18); $("#i-bell").innerHTML = icon("bell", 20); $("#i-key").innerHTML = icon("key", 16);
  $("#apis-btn").onclick = () => openApis();
  $$("#mode-seg button").forEach((b) => b.onclick = () => setMode(b.dataset.mode));
  $("#mode-info").onclick = () => showModeRoutes();
  loadMode();
  renderRail();
  [S.wl, S.platforms, S.agents] = await Promise.all([api("/api/whitelabel"), api("/api/platforms"), api("/api/agents")]);
  applyWL();
  connectEvents(); updateCounts(); pollHealth(); setInterval(pollHealth, 30000);
  route();
})();
