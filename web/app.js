// Shelton Studio — interfaz web (sin dependencias)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS = { queued: "En cola", generating: "Generando", ready: "Para revisar", approved: "Aprobado", published: "Publicado", failed: "Falló", rejected: "Rechazado" };
const state = { conv: null, posts: new Map(), progress: new Map(), filter: "", offset: 0, selected: new Set(), uploads: [], platforms: [], busy: false };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}, ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3500); }
const scoreCls = (s) => (s >= 85 ? "hi" : s >= 70 ? "mid" : "lo");

// ---------------------------------------------------------------- Navegación
$$(".nav-item").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
function show(view) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  ({ gallery: loadGallery, campaigns: loadCampaigns, bank: loadBank, brand: loadBrand, status: loadStatus })[view]?.();
}

// ---------------------------------------------------------------- Tarjetas de post
function tile(p, selectable = false) {
  const el = document.createElement("div");
  el.className = "tile";
  el.dataset.id = p.id;
  el.innerHTML = tileHTML(p, selectable);
  el.addEventListener("click", (e) => {
    if (selectable && (e.target.closest(".sel") || e.shiftKey || e.metaKey || e.ctrlKey)) { toggleSelect(p.id, el); return; }
    openPost(p.id);
  });
  if (state.selected.has(p.id)) el.classList.add("selected");
  return el;
}
function tileHTML(p, selectable) {
  const busy = p.status === "queued" || p.status === "generating";
  const prog = state.progress.get(p.id);
  const img = p.image_url && !busy
    ? `<div class="img" style="background-image:url('${p.image_url}?v=${encodeURIComponent(p.created_at || "")}')"></div>`
    : `<div class="img">${busy ? `<div><div class="spinner"></div>${esc(prog || STATUS[p.status])}</div>` : esc(p.error || STATUS[p.status])}</div>`;
  const warn = p.status === "ready" && p.qa_score && !p.qa_passed ? `<span class="badge failed" title="El juez la reprobó; decides tú">Juez ✖</span>` : "";
  return `${selectable ? '<div class="sel"></div>' : ""}${img}
    <div class="meta"><span class="badge ${p.status}">${STATUS[p.status] || p.status}</span>${warn}
    <span class="idea">${esc(p.platform_name)} · ${esc(p.idea)}</span>
    ${p.qa_score ? `<span class="score ${scoreCls(p.qa_score)}">${Math.round(p.qa_score)}</span>` : ""}</div>`;
}
function refreshTiles(p) {
  state.posts.set(p.id, p);
  $$(`.tile[data-id="${p.id}"]`).forEach((el) => { el.innerHTML = tileHTML(p, !!el.closest("#grid")); });
  if ($("#drawer").classList.contains("open") && $("#drawer").dataset.id === p.id) openPost(p.id, false);
}
function toggleSelect(id, el) {
  state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id);
  el.classList.toggle("selected");
  $("#sel-count").textContent = state.selected.size ? `${state.selected.size} seleccionados` : "";
}

// ---------------------------------------------------------------- Eventos en vivo
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "post") {
      const isNew = !state.posts.has(ev.post.id);
      if (ev.post.status !== "generating") state.progress.delete(ev.post.id);
      refreshTiles(ev.post);
      if (isNew && $("#view-gallery").classList.contains("active") && matchesFilter(ev.post)) $("#grid").prepend(tile(ev.post, true));
      pollQueue();
    } else if (ev.type === "progress") {
      state.progress.set(ev.id, ev.text);
      const p = state.posts.get(ev.id); if (p) refreshTiles(p);
    } else if (ev.type === "post_deleted") {
      $$(`.tile[data-id="${ev.id}"]`).forEach((e) => e.remove());
    } else if (ev.type === "subject" || ev.type === "subject_failed") {
      if ($("#view-bank").classList.contains("active")) loadBank();
      if (ev.type === "subject_failed") toast("No se pudo crear la foto de referencia: " + ev.error);
    } else if (ev.type === "campaign") {
      if ($("#view-campaigns").classList.contains("active")) loadCampaigns();
    }
  };
  es.onerror = () => { es.close(); setTimeout(connectEvents, 3000); };
}
function matchesFilter(p) { return !state.filter || state.filter.split(",").includes(p.status); }

async function pollQueue() {
  clearTimeout(pollQueue._t);
  pollQueue._t = setTimeout(async () => {
    try {
      const q = await api("/api/queue");
      $("#queue-pill").textContent = `cola: ${q.pending + q.running} · listos hoy: ${q.ready_today}`;
      const r = await api("/api/posts?status=ready&limit=1");
      $("#badge-ready").textContent = r.total || "";
    } catch {}
  }, 400);
}

// ---------------------------------------------------------------- Chat
const input = $("#input");
input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 200) + "px"; });
input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });
$$(".chip").forEach((c) => c.addEventListener("click", () => { input.value = c.textContent; input.focus(); }));
$("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });
$("#new-chat").addEventListener("click", () => { state.conv = null; $("#messages").innerHTML = ""; $("#messages").append(welcomeEl); renderConvs(); });
const welcomeEl = $("#welcome");

$("#file-input").addEventListener("change", async (e) => {
  for (const f of e.target.files) {
    const fd = new FormData(); fd.append("file", f);
    try {
      const p = await api("/api/upload", { method: "POST", body: fd });
      state.uploads.push(p); state.posts.set(p.id, p);
      const img = document.createElement("img"); img.src = p.image_url; $("#attachments").append(img);
    } catch (err) { toast(err.message); }
  }
  e.target.value = "";
});

function addMsg(role, text, postIds = []) {
  welcomeEl.remove();
  const m = document.createElement("div");
  m.className = `msg ${role}`;
  if (role === "bot") m.innerHTML = `<div class="who">Shelton</div>`;
  if (text) { const b = document.createElement("div"); b.className = "bubble"; b.textContent = text; m.append(b); }
  if (postIds.length) m.append(thumbs(postIds));
  $("#messages").append(m);
  $("#messages").scrollTop = 1e9;
  return m;
}
function thumbs(ids) {
  const box = document.createElement("div"); box.className = "thumbs";
  ids.forEach(async (id) => {
    let p = state.posts.get(id);
    if (!p) { try { p = await api(`/api/posts/${id}`); state.posts.set(id, p); } catch { return; } }
    box.append(tile(p));
  });
  return box;
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text || state.busy) return;
  state.busy = true; $("#send").disabled = true;
  const uploads = state.uploads.map((p) => p.id);
  addMsg("user", text, uploads);
  input.value = ""; input.style.height = "auto"; state.uploads = []; $("#attachments").innerHTML = "";

  const bot = addMsg("bot", "");
  const steps = document.createElement("div"); steps.className = "steps"; bot.append(steps);
  const step = (t) => { $$(".step", steps).forEach((s) => s.classList.remove("live")); const s = document.createElement("div"); s.className = "step live"; s.textContent = t; steps.append(s); $("#messages").scrollTop = 1e9; };
  step("Pensando…");
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: state.conv, upload_ids: uploads }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "conversation") { if (state.conv !== ev.id) { state.conv = ev.id; renderConvs(); } }
        else if (ev.type === "status") step(ev.text);
        else if (ev.type === "posts") bot.append(thumbs(ev.ids));
        else if (ev.type === "subjects_changed") step("Banco de modelos actualizado");
        else if (ev.type === "message") { const b = document.createElement("div"); b.className = "bubble"; b.textContent = ev.text || "(sin respuesta)"; bot.insertBefore(b, steps.nextSibling); }
        else if (ev.type === "error") { const b = document.createElement("div"); b.className = "bubble"; b.style.borderColor = "var(--bad)"; b.textContent = "⚠ " + ev.text; bot.append(b); }
      }
    }
  } catch (err) { step("⚠ " + err.message); }
  $$(".step", steps).forEach((s) => s.classList.remove("live"));
  state.busy = false; $("#send").disabled = false; input.focus();
}

async function renderConvs() {
  const list = await api("/api/conversations").catch(() => []);
  $("#conv-list").innerHTML = "";
  list.forEach((c) => {
    const d = document.createElement("div"); d.className = "conv" + (c.id === state.conv ? " active" : ""); d.textContent = c.title;
    d.addEventListener("click", () => openConv(c.id)); $("#conv-list").append(d);
  });
}
async function openConv(id) {
  const c = await api(`/api/conversations/${id}`);
  state.conv = id; $("#messages").innerHTML = "";
  c.messages.forEach((m) => addMsg(m.role === "user" ? "user" : "bot", m.text, m.post_ids || []));
  if (!c.messages.length) $("#messages").append(welcomeEl);
  renderConvs();
}

// ---------------------------------------------------------------- Galería
$$("#status-filters .pill").forEach((b) => b.addEventListener("click", () => {
  $$("#status-filters .pill").forEach((x) => x.classList.toggle("active", x === b));
  state.filter = b.dataset.status; loadGallery();
}));
async function loadGallery(more = false) {
  if (!more) { state.offset = 0; $("#grid").innerHTML = ""; }
  const r = await api(`/api/posts?limit=60&offset=${state.offset}${state.filter ? `&status=${state.filter}` : ""}`);
  r.items.forEach((p) => { state.posts.set(p.id, p); $("#grid").append(tile(p, true)); });
  state.offset += r.items.length;
  $("#load-more").hidden = state.offset >= r.total;
  if (!r.total) $("#grid").innerHTML = `<p class="muted">Aún no hay posts aquí. Pídele algo a Shelton o usa la generación rápida.</p>`;
}
$("#load-more").addEventListener("click", () => loadGallery(true));
$("#quick-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/posts", { method: "POST", body: { idea: $("#quick-idea").value, platform: $("#quick-platform").value, count: +$("#quick-count").value } });
    $("#quick-idea").value = ""; toast("En cola. Los verás aparecer aquí.");
  } catch (err) { toast(err.message); }
});
$("#bulk-approve").addEventListener("click", async () => {
  if (!state.selected.size) return toast("Selecciona posts (clic en el recuadro o Ctrl+clic)");
  await Promise.all([...state.selected].map((id) => api(`/api/posts/${id}`, { method: "PATCH", body: { status: "approved" } })));
  toast(`${state.selected.size} aprobados`); state.selected.clear(); $("#sel-count").textContent = ""; loadGallery();
});
$("#export").addEventListener("click", () => {
  const ids = [...state.selected];
  location.href = ids.length ? `/api/export?ids=${ids.join(",")}` : `/api/export?status=approved`;
});

// ---------------------------------------------------------------- Detalle de post
$("#drawer-close").addEventListener("click", () => $("#drawer").classList.remove("open"));
async function openPost(id, refetch = true) {
  let p = state.posts.get(id);
  if (refetch || !p) { p = await api(`/api/posts/${id}`); state.posts.set(id, p); }
  const versions = await api(`/api/posts/${id}/versions`).catch(() => []);
  const d = $("#drawer"); d.dataset.id = id; d.classList.add("open");
  const busy = p.status === "queued" || p.status === "generating";
  $("#drawer-body").innerHTML = `
    ${p.image_url && !busy ? `<img class="main" src="${p.image_url}?v=${encodeURIComponent(p.created_at)}" alt="${esc(p.alt_text)}">` : `<div class="tile"><div class="img"><div><div class="spinner"></div>${esc(state.progress.get(id) || STATUS[p.status])}</div></div></div>`}
    <div class="actions">
      <span class="badge ${p.status}">${STATUS[p.status]}</span> <span class="muted">${esc(p.platform_name)} · ${p.width}×${p.height}</span>
      ${p.qa_score ? `<span class="score ${scoreCls(p.qa_score)}">Calidad ${Math.round(p.qa_score)}/100</span>` : ""}
    </div>
    ${versions.length > 1 ? `<h3>Versiones</h3><div class="versions">${versions.map((v) => `<img src="${v.image_url}" data-v="${v.id}" class="${v.id === id ? "cur" : ""}" title="${esc(STATUS[v.status])}">`).join("")}</div>` : ""}
    <h3>Texto del post</h3>
    <textarea id="d-caption" rows="6">${esc(p.caption)}</textarea>
    <input id="d-tags" value="${esc((p.hashtags || []).join(" "))}" style="margin-top:8px" placeholder="#hashtags">
    <div class="actions">
      <button class="btn" id="d-copy">Copiar texto</button>
      <button class="btn" id="d-save">Guardar cambios</button>
      ${p.image_url ? `<a class="btn" href="${p.image_url}" download>Descargar imagen</a>` : ""}
    </div>
    <h3>Pedir un cambio</h3>
    <textarea id="d-edit" rows="2" placeholder="Ej: cambia el fondo a una playa al atardecer / hazlo más cálido / quita el texto"></textarea>
    <div class="actions">
      <button class="btn primary" id="d-edit-go">Editar imagen</button>
      <button class="btn" id="d-regen">Regenerar desde cero</button>
    </div>
    <div class="actions">
      <button class="btn good" id="d-approve">Aprobar</button>
      <button class="btn" id="d-telegram">Publicar en Telegram</button>
      <button class="btn bad" id="d-reject">Rechazar</button>
      <button class="btn bad" id="d-delete">Eliminar</button>
    </div>
    ${p.qa_notes ? `<h3>Opinión del juez (tú decides)</h3><div class="qa">${esc(p.qa_notes)}</div>` : ""}
    ${(p.alternatives || []).length ? `<h3>Otros intentos guardados</h3><p class="muted" style="margin:0 0 8px">El juez los puntuó más bajo, pero no se borran. Si crees que se equivocó, elige uno.</p>
      <div class="alts">${p.alternatives.map((a, i) => `<div class="alt"><img src="${a.image_url}"><div><span class="score ${scoreCls(a.score)}">${a.score}/100</span>
      <div class="muted">${esc((a.issues || []).join("; ") || a.summary)}</div><button class="btn" data-alt="${i}">Usar esta</button></div></div>`).join("")}</div>` : ""}
    ${p.error ? `<h3>Error</h3><div class="qa" style="color:var(--bad)">${esc(p.error)}</div>` : ""}
    <h3>Idea</h3><div class="prompt">${esc(p.idea)}</div>
    ${p.prompt ? `<h3>Prompt usado</h3><div class="prompt">${esc(p.prompt)}</div>` : ""}`;
  const body = $("#drawer-body");
  $$("[data-v]", body).forEach((im) => im.addEventListener("click", () => openPost(im.dataset.v)));
  $$("[data-alt]", body).forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/posts/${id}/use-alternative`, { method: "POST", body: { index: +b.dataset.alt } });
    toast("Listo: usando la imagen que elegiste"); openPost(id);
  }));
  const tags = () => $("#d-tags").value.split(/\s+/).filter(Boolean);
  $("#d-copy").onclick = () => { navigator.clipboard.writeText(`${$("#d-caption").value}\n\n${tags().join(" ")}`.trim()); toast("Copiado"); };
  $("#d-save").onclick = async () => { await api(`/api/posts/${id}`, { method: "PATCH", body: { caption: $("#d-caption").value, hashtags: tags() } }); toast("Guardado"); };
  $("#d-approve").onclick = () => setStatus(id, "approved");
  $("#d-reject").onclick = () => setStatus(id, "rejected");
  $("#d-delete").onclick = async () => { if (!confirm("¿Eliminar este post?")) return; await api(`/api/posts/${id}`, { method: "DELETE" }); $("#drawer").classList.remove("open"); };
  $("#d-regen").onclick = async () => { const r = await api(`/api/posts/${id}/regenerate`, { method: "POST" }); toast("Regenerando…"); openPost(r.id); };
  $("#d-edit-go").onclick = async () => {
    const instruction = $("#d-edit").value.trim(); if (!instruction) return;
    try { const r = await api(`/api/posts/${id}/edit`, { method: "POST", body: { instruction } }); toast("Editando… (se crea una versión nueva)"); openPost(r.id); }
    catch (err) { toast(err.message); }
  };
  $("#d-telegram").onclick = async () => {
    try { await api(`/api/posts/${id}/publish`, { method: "POST", body: { target: "telegram" } }); toast("Publicado en Telegram"); }
    catch (err) { toast(err.message); }
  };
}
async function setStatus(id, status) { await api(`/api/posts/${id}`, { method: "PATCH", body: { status } }); toast(STATUS[status]); }

// ---------------------------------------------------------------- Campañas
function platformChecks(box, selected = []) {
  box.innerHTML = state.platforms.map((p) => `<label><input type="checkbox" value="${p.key}" ${selected.includes(p.key) ? "checked" : ""}> ${esc(p.name)}</label>`).join("");
}
const checked = (box) => $$("input:checked", box).map((i) => i.value);
$("#campaign-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    const r = await api("/api/campaigns", { method: "POST", body: { brief: f.brief.value, count: +f.count.value,
      platforms: checked($("#campaign-platforms")), subjects: f.subjects.value.split(",").map((s) => s.trim()).filter(Boolean) } });
    if (r.subjects_not_found?.length) toast("No están en el banco: " + r.subjects_not_found.join(", "));
    else toast("Campaña lanzada");
    f.brief.value = ""; loadCampaigns();
  } catch (err) { toast(err.message); }
});
async function loadCampaigns() {
  const list = await api("/api/campaigns");
  $("#campaign-list").innerHTML = list.length ? "" : `<p class="muted">Sin campañas todavía.</p>`;
  list.forEach((c) => {
    const done = (c.counts.ready || 0) + (c.counts.approved || 0) + (c.counts.published || 0);
    const failed = c.counts.failed || 0;
    const el = document.createElement("div"); el.className = "card camp";
    el.innerHTML = `<div class="info"><b>${esc(c.brief)}</b><span class="muted">${done}/${c.count} listos${failed ? ` · ${failed} fallidos` : ""} · ${esc(c.status)} · ${c.platforms.join(", ")}</span>
      <div class="bar"><i style="width:${Math.min(100, (done / c.count) * 100)}%"></i></div></div>
      <button class="btn">Ver posts</button>`;
    $("button", el).onclick = async () => {
      show("gallery"); $("#grid").innerHTML = "";
      const r = await api(`/api/posts?campaign_id=${c.id}&limit=500`);
      r.items.forEach((p) => { state.posts.set(p.id, p); $("#grid").append(tile(p, true)); });
    };
    $("#campaign-list").append(el);
  });
}

// ---------------------------------------------------------------- Banco de modelos
$("#subject-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target; const files = [...f.photos.files];
  try {
    const { id } = await api("/api/subjects", { method: "POST", body: { name: f.name.value, kind: f.kind.value, request: f.request.value, generate: !files.length } });
    for (const [i, file] of files.entries()) {
      const fd = new FormData(); fd.append("file", file); fd.append("describe", i === 0 ? "true" : "false");
      await api(`/api/subjects/${id}/photos`, { method: "POST", body: fd });
    }
    toast(files.length ? "Guardado con tus fotos" : "Shelton está creando su foto de referencia…");
    f.reset(); loadBank();
  } catch (err) { toast(err.message); }
});
async function loadBank() {
  const list = await api("/api/subjects");
  $("#bank-grid").innerHTML = list.length ? "" : `<p class="muted">El banco está vacío. Crea tu primer personaje, auto o lugar.</p>`;
  const kinds = { person: "Persona", car: "Auto", place: "Lugar", product: "Producto", pet: "Mascota", other: "Otro" };
  list.forEach((s) => {
    const el = document.createElement("div"); el.className = "subject";
    el.innerHTML = `<div class="img" style="${s.references[0] ? `background-image:url('${s.references[0]}')` : ""}">${s.references[0] ? "" : '<div><div class="spinner"></div>Creando referencia…</div>'}</div>
      <div class="body"><b>${esc(s.name)}</b> <span class="badge">${kinds[s.kind] || s.kind}</span>
      <p>${esc(s.description || s.notes || "")}</p>
      <div class="refs">${s.references.slice(1, 7).map((r) => `<img src="${r}" title="Foto de referencia">`).join("")}</div>
      ${(s.alternatives || []).length ? `<div class="muted" style="font-size:12px">Otros intentos (clic para usar como principal):</div>
      <div class="refs">${s.alternatives.map((r, i) => `<img src="${r}" data-salt="${i}" style="cursor:pointer;opacity:.75">`).join("")}</div>` : ""}
      <div class="actions"><label class="btn">+ Foto<input type="file" accept="image/*" hidden></label>
      <button class="btn" data-use>Usar en chat</button><button class="btn bad" data-del>✕</button></div></div>`;
    $("input", el).onchange = async (e) => {
      const fd = new FormData(); fd.append("file", e.target.files[0]); fd.append("describe", "false");
      await api(`/api/subjects/${s.id}/photos`, { method: "POST", body: fd }); loadBank();
    };
    $$("[data-salt]", el).forEach((im) => im.onclick = async () => {
      await api(`/api/subjects/${s.id}/use-alternative`, { method: "POST", body: { index: +im.dataset.salt } }); loadBank();
    });
    $("[data-use]", el).onclick = () => { show("chat"); input.value = `Haz 3 posts de ${s.name} `; input.focus(); };
    $("[data-del]", el).onclick = async () => { if (confirm(`¿Eliminar a ${s.name} del banco?`)) { await api(`/api/subjects/${s.id}`, { method: "DELETE" }); loadBank(); } };
    $("#bank-grid").append(el);
  });
}

// ---------------------------------------------------------------- Marca
async function loadBrand() {
  const b = await api("/api/brand"); const f = $("#brand-form");
  for (const k of ["name", "description", "audience", "tone", "visual_style", "autopilot_daily", "autopilot_hour"]) f[k].value = b[k] ?? "";
  f.pillars.value = (b.pillars || []).join(", "); f.base_hashtags.value = (b.base_hashtags || []).join(" ");
  platformChecks($("#brand-platforms"), b.platforms || []);
}
$("#brand-form").addEventListener("submit", async (e) => {
  e.preventDefault(); const f = e.target;
  await api("/api/brand", { method: "PUT", body: {
    name: f.name.value, description: f.description.value, audience: f.audience.value, tone: f.tone.value, visual_style: f.visual_style.value,
    pillars: f.pillars.value.split(",").map((s) => s.trim()).filter(Boolean), base_hashtags: f.base_hashtags.value.split(/\s+/).filter(Boolean),
    platforms: checked($("#brand-platforms")), autopilot_daily: +f.autopilot_daily.value || 0, autopilot_hour: +f.autopilot_hour.value || 0 } });
  toast("Marca guardada");
});

// ---------------------------------------------------------------- Estado
$("#refresh-status").addEventListener("click", loadStatus);
async function loadStatus() {
  $("#status-box").innerHTML = "Comprobando…";
  const h = await api("/api/health"); const e = h.engine;
  const li = (ok, t) => `<div><span class="${ok ? "ok" : "bad"}">${ok ? "✔" : "✖"}</span> ${t}</div>`;
  $("#status-box").innerHTML = `<div class="status-list">
    ${li(h.claude, `Claude (${esc(h.model)}) ${h.claude ? "configurado" : "— falta <code>ANTHROPIC_API_KEY</code> en .env"}`)}
    ${li(e.ok, `Motor de imagen: <b>${esc(e.engine)}</b> ${e.url ? `en <code>${esc(e.url)}</code>` : ""} ${e.note ? `— ${esc(e.note)}` : ""}`)}
    ${e.error ? `<div class="bad">${esc(e.error)}</div>` : ""}
    ${(e.devices || []).map((d) => `<div class="muted">GPU: ${esc(d.name)} · ${d.vram_gb} GB</div>`).join("")}
    ${(e.missing_models || []).length ? `<div><b>Modelos que faltan en ComfyUI:</b><br>${e.missing_models.map((m) => `<code>${esc(m)}</code>`).join(" ")}</div>` : ""}
    ${(e.workflow_problems || []).length ? `<div><b>Problemas de workflow:</b><br>${e.workflow_problems.map(esc).join("<br>")}</div>` : ""}
    <div class="muted">Cola: ${h.queue.pending} pendientes · ${h.queue.running} en curso · ${h.queue.ready_today} listos hoy · ${h.queue.workers} trabajadores</div></div>`;
}

// ---------------------------------------------------------------- Inicio
(async function init() {
  state.platforms = await api("/api/platforms");
  $("#quick-platform").innerHTML = state.platforms.map((p) => `<option value="${p.key}">${esc(p.name)} (${p.aspect})</option>`).join("");
  platformChecks($("#campaign-platforms"), ["instagram_feed", "tiktok", "telegram"]);
  connectEvents(); renderConvs(); pollQueue();
})();
