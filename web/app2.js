// Shelton Studio — resto de secciones del panel.
"use strict";

const pageHead = (ico, title, sub, actions = "") => `<div class="page-head"><div class="page-icon">${ico}</div><div><h1>${title}</h1><p>${sub}</p></div><div class="actions">${actions}</div></div>`;
const KINDS = { person: "Persona", car: "Auto", vehicle: "Vehículo / nave", place: "Lugar", product: "Producto", pet: "Mascota", creature: "Criatura", object: "Objeto", style: "Estilo visual", other: "Otro" };

// ------------------------------------------------------------------ TAREAS
VIEWS.tareas = async (el) => {
  const mode = localStorage.getItem("tasks-mode") || "board", agent = localStorage.getItem("tasks-agent") || "";
  const t = await api("/api/tasks");
  const COLS = [["queued", "En cola", ""], ["running", "En curso", "accent"], ["waiting", "Esperando aprobación", "warn"], ["done", "Hecha", "good"], ["failed", "Fallida", "bad"]];
  const filt = (cards) => cards.filter((c) => !agent || c.agent === agent);
  const card = (c, col) => `<div class="k-card" data-post="${esc(c.post_id || "")}" data-kind="${c.kind}">
    <div class="who">${avatar(c.agent, true)}${esc(c.agent_name)}<div class="grow"></div><span class="chip">${esc(c.label)}</span></div>
    <p>${esc(c.title)}</p>${c.image_url ? `<img src="${esc(c.image_url)}" loading="lazy">` : ""}
    <div class="faint small">${when(c.at)}${c.error ? ` · <span style="color:#fca5a5">${esc(c.error.slice(0, 90))}</span>` : ""}</div>
    ${col === "failed" && c.kind !== "approval" ? `<button class="btn sm" style="margin-top:8px" data-retry="${c.id}">${icon("refresh", 14)} Reintentar</button>` : ""}</div>`;
  el.innerHTML = pageHead("📋", "Tareas", "Todo lo que hicieron y hacen los agentes.", `<button class="btn primary" onclick="go('chat')">${icon("plus", 17)} Nueva tarea</button>`) +
    `<div class="toolbar"><div class="seg" id="tm"><button data-m="board" class="${mode === "board" ? "on" : ""}">Tablero</button><button data-m="list" class="${mode === "list" ? "on" : ""}">Lista</button></div>
     <select id="ta" style="width:240px"><option value="">Todos los agentes</option>${S.agents.map((a) => `<option value="${a.key}" ${a.key === agent ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
     <button class="btn ghost" id="tr">${icon("refresh", 16)} Actualizar</button></div>` +
    (mode === "board" ? `<div class="kanban">${COLS.map(([k, l, cls]) => `<div class="k-col"><div class="k-top"><span class="chip ${cls}">${l}</span><b class="muted">${filt(t.columns[k]).length}</b></div>${filt(t.columns[k]).map((c) => card(c, k)).join("") || '<div class="faint small" style="padding:8px">Vacío</div>'}</div>`).join("")}</div>`
      : `<div class="card"><table class="t"><thead><tr><th>Estado</th><th>Agente</th><th>Tarea</th><th>Cuándo</th></tr></thead><tbody>${COLS.flatMap(([k, l, cls]) => filt(t.columns[k]).map((c) => `<tr class="k-card" style="display:table-row" data-post="${esc(c.post_id || "")}" data-kind="${c.kind}"><td><span class="chip ${cls}">${l}</span></td><td>${esc(c.agent_name)}</td><td>${esc(c.title)}</td><td class="muted">${when(c.at)}</td></tr>`)).join("")}</tbody></table></div>`);
  $$("#tm button").forEach((b) => b.onclick = () => { localStorage.setItem("tasks-mode", b.dataset.m); route(); });
  $("#ta").onchange = (e) => { localStorage.setItem("tasks-agent", e.target.value); route(); };
  $("#tr").onclick = route;
  $$("[data-retry]").forEach((b) => b.onclick = async (e) => { e.stopPropagation(); await api(`/api/tasks/${b.dataset.retry}/retry`, { method: "POST" }); toast("Reintentando", "good"); route(); });
  $$(".k-card[data-post]").forEach((c) => c.onclick = () => { if (c.dataset.kind === "approval") go("aprobaciones"); else if (c.dataset.post) openPost(c.dataset.post); });
};

// ------------------------------------------------------------------ APROBACIONES
VIEWS.aprobaciones = async (el) => {
  const tab = localStorage.getItem("appr-tab") || "pending";
  const [list, review] = await Promise.all([api(`/api/approvals?status=${tab === "rejected" ? "rejected,failed" : tab}`), api("/api/posts?status=ready&limit=1")]);
  el.innerHTML = pageHead("✅", "Aprobaciones", "Lo que los agentes quieren hacer afuera espera tu visto bueno.") +
    `<div class="toolbar"><div class="seg" id="at">${[["pending", "Pendientes"], ["approved", "Aprobadas"], ["rejected", "Rechazadas"]].map(([v, l]) => `<button data-v="${v}" class="${tab === v ? "on" : ""}">${l}</button>`).join("")}</div>
     <div class="grow"></div>${review.total ? `<a class="btn" href="#/galeria" onclick="S.galleryFilter='ready'">${icon("gallery", 16)} ${review.total} posts esperan tu revisión</a>` : ""}</div>
    <div class="grid" style="gap:12px">${list.map((a) => `<div class="card" style="display:flex;gap:16px;align-items:center">
      ${a.payload.image_url ? `<img src="${esc(a.payload.image_url)}" style="width:96px;height:120px;object-fit:cover;border-radius:12px;cursor:pointer" data-post="${esc(a.payload.post_id)}">` : ""}
      <div class="grow"><div class="row" style="gap:8px">${avatar(a.agent, true)}<b>${esc(a.title)}</b></div>
      <div class="muted small" style="margin:6px 0;white-space:pre-wrap;max-height:90px;overflow:hidden">${esc(a.payload.caption || "")}</div>
      <div class="faint small">Pedido por ${esc(agentName(a.agent))} · ${when(a.created_at)}${a.decided_at ? ` · decidido ${when(a.decided_at)}` : ""}${a.result?.error ? ` · <span style="color:#fca5a5">${esc(a.result.error)}</span>` : ""}</div></div>
      ${a.status === "pending" ? `<div class="col" style="gap:8px"><button class="btn good" data-ok="${a.id}">${icon("check", 16)} Aprobar y publicar</button><button class="btn bad" data-no="${a.id}">Rechazar</button></div>` : `<span class="chip ${a.status === "approved" ? "good" : "bad"}">${{ approved: "Aprobada", rejected: "Rechazada", failed: "Falló" }[a.status]}</span>`}</div>`).join("") ||
      `<div class="card">${empty(icon("approve", 26), "No hay nada en esta bandeja.")}</div>`}</div>`;
  $$("#at button").forEach((b) => b.onclick = () => { localStorage.setItem("appr-tab", b.dataset.v); route(); });
  $$("[data-post]").forEach((i) => i.onclick = () => openPost(i.dataset.post));
  const decide = (id, ok) => async (e) => { e.target.disabled = true; try { const r = await api(`/api/approvals/${id}`, { method: "POST", body: { approve: ok } }); toast(r.status === "approved" ? "Aprobado y publicado" : r.status === "failed" ? "Falló al publicar: " + esc(r.result.error) : "Rechazado", r.status === "failed" ? "bad" : "good"); route(); updateCounts(); } catch (err) { toast(esc(err.message), "bad"); } };
  $$("[data-ok]").forEach((b) => b.onclick = decide(b.dataset.ok, true));
  $$("[data-no]").forEach((b) => b.onclick = decide(b.dataset.no, false));
};

// ------------------------------------------------------------------ CAMPAÑAS
VIEWS.campanas = async (el) => {
  const [list, subs] = await Promise.all([api("/api/campaigns"), api("/api/subjects")]);
  el.innerHTML = pageHead("🚀", "Campañas", "Lotes grandes planificados por el equipo (10, 50, 200 posts). Se generan en cola, local y en paralelo.") +
    `<form class="card col" id="cf" style="max-width:900px"><label class="f">Brief<textarea name="brief" rows="3" required placeholder="Ej: semana de lanzamiento del menú de otoño, tono cálido, promo 2x1 el viernes, mezcla fotos y frases"></textarea></label>
      <div class="row"><label class="f" style="width:140px">Cantidad<input name="count" type="number" min="1" max="200" value="50"></label>
      <label class="f grow">Modelos del banco (opcional)<select name="subjects" multiple size="3">${subs.map((s) => `<option value="${esc(s.name)}">${esc(s.name)} · ${KINDS[s.kind] || s.kind}</option>`).join("")}</select></label></div>
      <div class="checks" id="cp">${S.platforms.filter((p) => p.key !== "original").map((p) => `<label><input type="checkbox" value="${p.key}" ${["instagram_feed", "tiktok", "telegram"].includes(p.key) ? "checked" : ""}> ${esc(p.name)}</label>`).join("")}</div>
      <div><button class="btn primary">${icon("rocket", 17)} Lanzar campaña</button></div></form>
    <div class="section-title">Campañas recientes</div><div class="grid" style="gap:12px">${list.map((c) => {
      const done = (c.counts.ready || 0) + (c.counts.approved || 0) + (c.counts.published || 0), failed = c.counts.failed || 0, pct = Math.min(100, (done / c.count) * 100);
      return `<div class="card" style="display:flex;gap:16px;align-items:center"><div class="grow"><b class="ellipsis" style="display:block">${esc(c.brief)}</b>
        <div class="muted small" style="margin:4px 0 10px">${done}/${c.count} listos${failed ? ` · ${failed} fallidos` : ""} · ${esc(c.platforms.join(", "))} · ${when(c.created_at)}${c.error ? ` · <span style="color:#fca5a5">${esc(c.error)}</span>` : ""}</div>
        <div class="hbar" style="grid-template-columns:1fr 50px;margin:0"><div class="track"><i style="width:${pct}%"></i></div><b>${Math.round(pct)}%</b></div></div>
        <button class="btn" onclick="go('galeria','${c.id}')">Ver posts</button></div>`; }).join("") || `<div class="card">${empty("🚀", "Sin campañas todavía.")}</div>`}</div>`;
  $("#cf").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    try { const r = await api("/api/campaigns", { method: "POST", body: { brief: f.brief.value, count: +f.count.value, platforms: $$("#cp input:checked").map((i) => i.value), subjects: [...f.subjects.selectedOptions].map((o) => o.value) } });
      toast(r.subjects_not_found?.length ? "No están en el banco: " + esc(r.subjects_not_found.join(", ")) : "Campaña lanzada: el planificador está pensando las ideas.", "good", "🚀"); route(); } catch (err) { toast(esc(err.message), "bad"); }
  };
};

// ------------------------------------------------------------------ BANCO DE MODELOS
VIEWS.banco = async (el) => {
  const subs = await api("/api/subjects");
  el.innerHTML = pageHead("👥", "Banco de modelos", "Personajes, autos, naves, lugares, productos o estilos recurrentes. Shelton los reconoce por su nombre y mantiene su identidad (y su voz).",
    `<button class="btn primary" id="new-sub">${icon("plus", 17)} Nuevo modelo</button>`) +
    `<div class="post-grid" style="grid-template-columns:repeat(auto-fill,minmax(250px,1fr))">${subs.map((s) => `<div class="card subject-card">
      <div class="img" style="${s.references[0] ? `background-image:url('${esc(s.references[0])}')` : ""}">${s.references[0] ? "" : '<div><div class="spinner"></div>Creando referencia…</div>'}</div>
      <div class="body"><div class="row" style="gap:8px"><b>${esc(s.name)}</b><span class="chip">${KINDS[s.kind] || esc(s.kind)}</span>${s.voice_url ? '<span class="chip accent">🎙️ voz</span>' : ""}</div>
      <p>${esc(s.description || s.notes || "")}</p>
      ${s.references.length > 1 ? `<div class="refs">${s.references.slice(1, 8).map((r) => `<img src="${esc(r)}" title="Referencia">`).join("")}</div>` : ""}
      ${s.alternatives.length ? `<div class="faint small">Otros intentos (clic para usar como principal):</div><div class="refs">${s.alternatives.map((r, i) => `<img src="${esc(r)}" data-salt="${s.id}:${i}" style="opacity:.7">`).join("")}</div>` : ""}
      ${s.voice_url ? `<audio controls src="${esc(s.voice_url)}"></audio>` : ""}
      <div class="row" style="gap:6px"><label class="btn sm">+ Foto<input type="file" accept="image/*" hidden data-photo="${s.id}"></label>
        <label class="btn sm">🎙️ Voz<input type="file" accept="audio/*" hidden data-voice="${s.id}"></label>
        <button class="btn sm" data-use="${esc(s.name)}">Usar</button><button class="btn sm ghost" data-del="${s.id}" title="Eliminar">${icon("trash", 15)}</button></div></div></div>`).join("") ||
      `<div class="card" style="grid-column:1/-1">${empty("👥", "El banco está vacío. Crea tu primer personaje, auto, nave o lugar.")}</div>`}</div>`;
  $("#new-sub").onclick = () => {
    const box = modal("Nuevo modelo del banco", `<div class="row"><label class="f grow">Nombre<input id="sn" placeholder="Valentina, Mustang Rojo, Nave Aurora…"></label>
      <label class="f" style="width:200px">Tipo<select id="sk">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label></div>
      <label class="f">Cómo es<textarea id="sr" rows="3" placeholder="26 años, pelo rizado castaño, ojos verdes, estilo urbano… / deportivo rojo, llantas negras… / acuarela suave, pastel…"></textarea></label>
      <label class="f">…o sube tus fotos de referencia (la primera se analiza)<input type="file" id="sp" accept="image/*" multiple></label>
      <div class="row"><label class="f grow">Muestra de su voz (opcional, 5-15 s)<input type="file" id="sv" accept="audio/*"></label><label class="f grow">Cómo suena<input id="sst" placeholder="grave, cálida, acento neutro"></label></div>`,
      `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="ss">Guardar en el banco</button>`);
    $("#ss", box).onclick = async () => {
      const files = [...$("#sp").files]; if (!$("#sn").value.trim()) return toast("Ponle un nombre", "bad");
      try {
        const { id } = await api("/api/subjects", { method: "POST", body: { name: $("#sn").value, kind: $("#sk").value, request: $("#sr").value, generate: !files.length } });
        for (const [i, f] of files.entries()) { const fd = new FormData(); fd.append("file", f); fd.append("describe", i === 0 ? "true" : "false"); await api(`/api/subjects/${id}/photos`, { method: "POST", body: fd }); }
        if ($("#sv").files[0] || $("#sst").value) { const fd = new FormData(); if ($("#sv").files[0]) fd.append("file", $("#sv").files[0]); await api(`/api/subjects/${id}/voice?style=${encodeURIComponent($("#sst").value)}`, { method: "POST", body: fd }); }
        closeModal(); toast(files.length ? "Guardado con tus fotos" : "El diseñador está creando su imagen de referencia…", "good", "👤"); route();
      } catch (err) { toast(esc(err.message), "bad"); }
    };
  };
  $$("[data-photo]").forEach((i) => i.onchange = async (e) => { const fd = new FormData(); fd.append("file", e.target.files[0]); fd.append("describe", "false"); await api(`/api/subjects/${i.dataset.photo}/photos`, { method: "POST", body: fd }); route(); });
  $$("[data-voice]").forEach((i) => i.onchange = async (e) => { const fd = new FormData(); fd.append("file", e.target.files[0]); await api(`/api/subjects/${i.dataset.voice}/voice`, { method: "POST", body: fd }); toast("Voz guardada: se clonará en sus videos", "good", "🎙️"); route(); });
  $$("[data-salt]").forEach((im) => im.onclick = async () => { const [sid, i] = im.dataset.salt.split(":"); await api(`/api/subjects/${sid}/use-alternative`, { method: "POST", body: { index: +i } }); route(); });
  $$("[data-use]").forEach((b) => b.onclick = () => { S.chatAgent = "shelton"; go("chat"); setTimeout(() => { $("#input").value = `Haz 3 posts de ${b.dataset.use} `; $("#input").focus(); }, 300); });
  $$("[data-del]").forEach((b) => b.onclick = async () => { if (confirm("¿Eliminar este modelo del banco?")) { await api(`/api/subjects/${b.dataset.del}`, { method: "DELETE" }); route(); } });
};

// ------------------------------------------------------------------ TENDENCIAS (scout)
VIEWS.tendencias = async (el) => {
  const kind = localStorage.getItem("find-kind") || "";
  const [list, subs, brand] = await Promise.all([api(`/api/findings${kind ? "?kind=" + kind : ""}`), api("/api/subjects"), api("/api/brand")]);
  el.innerHTML = pageHead("🔭", "Tendencias y herramientas", `El scout busca 24/7 (cada ${brand.scout_every_hours || "—"} h) tendencias para replicar con tus modelos y herramientas locales nuevas. Tú decides qué se usa.`,
    `<button class="btn primary" id="run">${icon("search", 17)} Buscar ahora</button>`) +
    `<div class="toolbar"><div class="seg" id="fk">${[["", "Todo"], ["trend", "Tendencias"], ["tool", "Herramientas"]].map(([v, l]) => `<button data-v="${v}" class="${kind === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div class="grid g2">${list.map((f) => `<div class="card finding"><div class="row" style="gap:8px"><span class="chip ${f.kind === "trend" ? "good" : "info"}">${f.kind === "trend" ? "Tendencia" : "Herramienta"}</span><span class="faint small">${when(f.created_at)}</span><div class="grow"></div>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener" class="small">Fuente ↗</a>` : ""}</div>
      <b style="font-size:16px">${esc(f.title)}</b><div class="muted">${esc(f.summary)}</div>
      <div class="small"><b>Por qué nos sirve:</b> <span class="muted">${esc(f.why)}</span></div><div class="small"><b>Cómo replicarlo:</b> <span class="muted">${esc(f.how)}</span></div>
      ${f.ideas.length ? `<div class="ideas">${f.ideas.map((i, n) => `<label><input type="checkbox" data-f="${f.id}" value="${n}" checked> ${esc(i)}</label>`).join("")}</div>
      <div class="row" style="gap:8px"><select data-fp="${f.id}" style="width:auto">${S.platforms.filter((p) => p.key !== "original").map((p) => `<option value="${p.key}" ${(f.platforms[0] || "instagram_feed") === p.key ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
        <select data-fs="${f.id}" style="width:auto"><option value="">Sin modelo</option>${subs.map((s) => `<option>${esc(s.name)}</option>`).join("")}</select>
        <select data-ff="${f.id}" style="width:auto"><option value="image">Imagen</option><option value="video">Video</option></select></div>` : ""}
      <div class="row" style="gap:8px">${f.ideas.length ? `<button class="btn primary sm" data-use="${f.id}">${icon("sparkle", 15)} Replicar</button>` : ""}<button class="btn ghost sm" data-dis="${f.id}">Descartar</button></div></div>`).join("") ||
      `<div class="card" style="grid-column:1/-1">${empty("🔭", "Sin hallazgos nuevos. Pulsa «Buscar ahora» o espera a la próxima ronda del scout.")}</div>`}</div>`;
  $("#run").onclick = async () => { await api("/api/scout/run", { method: "POST" }); toast("El scout está investigando en internet. Te aviso cuando termine.", "", "🔭"); };
  $$("#fk button").forEach((b) => b.onclick = () => { localStorage.setItem("find-kind", b.dataset.v); route(); });
  $$("[data-use]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.use, f = list.find((x) => x.id === id), picked = $$(`[data-f="${id}"]:checked`).map((c) => f.ideas[+c.value]);
    const subj = $(`[data-fs="${id}"]`).value;
    const r = await api(`/api/findings/${id}/use`, { method: "POST", body: { ideas: picked, platform: $(`[data-fp="${id}"]`).value, subjects: subj ? [subj] : [], format: $(`[data-ff="${id}"]`).value } });
    toast(`${r.ids.length} posts en producción`, "good", "🚀"); route();
  });
  $$("[data-dis]").forEach((b) => b.onclick = async () => { await api(`/api/findings/${b.dataset.dis}/dismiss`, { method: "POST" }); route(); });
};

// ------------------------------------------------------------------ VOCES
VIEWS.voces = async (el) => {
  const [voices, subs] = await Promise.all([api("/api/voices"), api("/api/subjects")]);
  el.innerHTML = pageHead("🎙️", "Voces", "Banco de voces y laboratorio. Describe la voz con palabras: grave, aguda, femenina, sensual, adulta, anciana, robotizada, épica…") +
    `<div class="grid g2"><div class="card col"><h3>Laboratorio de voz</h3>
      <label class="f">Texto<textarea id="vt" rows="3">Hola, esto es lo nuevo que tenemos para ti esta semana. ¿Te lo vas a perder?</textarea></label>
      <label class="f">Cómo debe sonar<input id="vr" placeholder="Ej: grave y pausada, sensual y cálida, robotizada, señor mayor…"></label>
      <label class="f">Con la voz de un modelo del banco (opcional)<select id="vs"><option value="">—</option>${subs.filter((s) => s.voice_url).map((s) => `<option>${esc(s.name)}</option>`).join("")}</select></label>
      <div><button class="btn primary" id="vp">${icon("play", 15)} Escuchar</button></div><div id="vo"></div>
      <div class="hint">La voz se genera en tu GPU con el servidor local (Chatterbox). Arráncalo con <code>python tts_server/server.py</code>. También puedes conectar ElevenLabs u otra API en Conexiones.</div></div>
    <div class="card col"><h3>Banco de voces</h3><div class="hint">Sube muestras de 5-15 s (una sola persona, sin música). El Locutor elige la más parecida a lo que pidas o la que le indiques.</div>
      <div class="row"><input id="vn" placeholder="Nombre (ej: Lola)" style="flex:1"><input id="vd" placeholder="Descripción (ej: femenina, cálida)" style="flex:2"></div>
      <label class="btn" style="justify-self:start">${icon("plus", 16)} Subir muestra<input type="file" id="vf" accept="audio/*" hidden></label>
      <div class="grid" style="gap:8px">${voices.map((v) => `<div class="row" style="padding:10px;border:1px solid var(--line);border-radius:12px"><div style="min-width:120px"><b>${esc(v.name)}</b><div class="faint small">${esc(v.description)}</div></div><audio controls src="${esc(v.url)}" style="flex:1"></audio><button class="btn sm ghost" data-dv="${esc(v.name)}">${icon("trash", 15)}</button></div>`).join("") || '<div class="faint small">Sin voces todavía.</div>'}</div></div></div>`;
  $("#vp").onclick = async () => {
    $("#vo").innerHTML = '<div class="spinner"></div>';
    try { const r = await api("/api/voices/preview", { method: "POST", body: { text: $("#vt").value, request: $("#vr").value, subject: $("#vs").value } });
      const sp = r.spec; $("#vo").innerHTML = `<audio controls autoplay src="${esc(r.url)}"></audio><div class="faint small" style="margin-top:6px">Tono ${sp.pitch_semitones > 0 ? "+" : ""}${sp.pitch_semitones} · velocidad ${sp.speed} · expresividad ${sp.exaggeration}${sp.warmth ? " · cálida" : ""}${sp.breathy ? " · susurrada" : ""}${sp.robot ? " · robot" : ""}${sp.reverb ? " · eco" : ""}${sp.base_voice ? " · base: " + esc(sp.base_voice) : ""}</div>`;
    } catch (err) { $("#vo").innerHTML = `<div class="qa-box" style="color:#fca5a5">${esc(err.message)}</div>`; }
  };
  $("#vf").onchange = async (e) => { const fd = new FormData(); fd.append("file", e.target.files[0]); await api(`/api/voices?name=${encodeURIComponent($("#vn").value)}&description=${encodeURIComponent($("#vd").value)}`, { method: "POST", body: fd }); toast("Voz agregada", "good"); route(); };
  $$("[data-dv]").forEach((b) => b.onclick = async () => { await api(`/api/voices/${encodeURIComponent(b.dataset.dv)}`, { method: "DELETE" }); route(); });
};

// ------------------------------------------------------------------ AGENTES y FICHA
VIEWS.agentes = async (el) => {
  const [agents, m] = await Promise.all([api("/api/agents"), api("/api/metrics?days=30")]);
  S.agents = agents; const per = Object.fromEntries(m.agents.map((a) => [a.key, a]));
  el.innerHTML = pageHead("🤖", "Agentes", `${esc(S.wl.chief_name || "Shelton")} es el núcleo y dirige al resto. Cada agente tiene su ficha (instrucciones) y su memoria (entrenamiento humano).`) +
    `<div class="grid g2">${agents.map((a) => agentCard(a, per[a.key])).join("")}</div>`;
  bindAgentCards(el);
};
async function openAgent(key) {
  const [agents, lessons, runs] = await Promise.all([api("/api/agents"), api(`/api/training/${key}`), api(`/api/runs?agent=${key}&limit=15`)]);
  S.agents = agents; const a = agents.find((x) => x.key === key);
  const box = modal(`${avatar(key, true)} ${esc(a.name)} <span class="chip">v${a.version}</span>`, `
    <div class="muted">${esc(a.role)}</div>
    <div class="seg" id="ftab"><button class="on" data-t="train">🧠 Entrenamiento (${lessons.length})</button><button data-t="prompt">Ficha / instrucciones</button><button data-t="runs">Actividad</button></div>
    <div id="f-train" class="col">
      <div class="hint">Lo que enseñes aquí queda guardado en <code>data/training/${esc(key)}/</code> con fecha y hora, y el agente lo lee cada vez que trabaja. Si contradice algo anterior, te avisa y decides tú.</div>
      <div class="card col" style="padding:14px"><input id="lt" placeholder="Título (ej: Hashtags en TikTok)"><textarea id="lc" rows="3" placeholder="La lección, clara y con ejemplo. Ej: En TikTok usa siempre 4 hashtags: 2 del nicho y 2 de tendencia."></textarea>
        <div class="row"><input id="la" placeholder="Tu nombre" style="flex:1"><select id="lr" style="width:160px"><option value="admin">Admin</option><option value="operador">Operador</option></select><button class="btn primary" id="ladd">Enseñar</button></div></div>
      <div class="col" id="llist">${lessons.map(lessonHTML(key)).join("") || '<div class="faint small">Sin lecciones todavía.</div>'}</div></div>
    <div id="f-prompt" class="col hidden"><label class="f">Instrucciones (prompt)<textarea id="fp" rows="14" class="mono" style="font-size:12.5px">${esc(a.prompt)}</textarea></label>
      <div class="row"><label class="f grow">Modelo de Claude<input id="fm" value="${esc(a.model)}"></label><label class="f" style="width:180px">Esfuerzo<select id="fe">${["low", "medium", "high", "xhigh", "max"].map((e) => `<option ${e === a.effort ? "selected" : ""}>${e}</option>`).join("")}</select></label>
      <label class="f" style="width:150px">Estado<select id="fen"><option value="1" ${a.enabled ? "selected" : ""}>Activo</option><option value="0" ${!a.enabled ? "selected" : ""}>Pausado</option></select></label></div>
      <div class="hint">Menos esfuerzo = más rápido y menos tokens. Cada guardado sube la versión. ${a.custom_prompt ? "Este agente usa instrucciones personalizadas." : "Usa las instrucciones por defecto."}</div>
      <div class="row"><button class="btn primary" id="fsave">Guardar ficha</button>${a.custom_prompt ? '<button class="btn ghost" id="freset">Volver a las instrucciones por defecto</button>' : ""}</div></div>
    <div id="f-runs" class="hidden"><table class="t"><thead><tr><th></th><th>Qué hizo</th><th>Tokens</th><th>Tiempo</th><th>Cuándo</th></tr></thead><tbody>${runs.map((r) => `<tr><td>${r.ok ? "✅" : "⚠️"}</td><td>${esc(r.summary || "—")}${r.error ? `<div class="small" style="color:#fca5a5">${esc(r.error)}</div>` : ""}</td><td>${fmtN(r.tokens)}</td><td>${(r.ms / 1000).toFixed(1)}s</td><td class="muted">${when(r.created_at)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">Sin actividad</td></tr>'}</tbody></table></div>`,
    `<button class="btn" onclick="closeModal();S.chatAgent='${key}';S.conv=null;go('chat')">${icon("chat", 16)} Chatear con ${esc(a.name)}</button>`, true);
  $$("#ftab button", box).forEach((b) => b.onclick = () => { $$("#ftab button", box).forEach((x) => x.classList.toggle("on", x === b)); ["train", "prompt", "runs"].forEach((t) => $("#f-" + t, box).classList.toggle("hidden", t !== b.dataset.t)); });
  $("#ladd", box).onclick = async () => {
    if (!$("#lt").value.trim() || !$("#lc").value.trim()) return toast("Título y lección", "bad");
    const e = await api(`/api/training/${key}`, { method: "POST", body: { title: $("#lt").value, content: $("#lc").value, author: $("#la").value, role: $("#lr").value } });
    localStorage.setItem("author", $("#la").value); openAgent(key);
  };
  $("#la", box).value = localStorage.getItem("author") || "";
  bindLessons(box, () => openAgent(key));
  $("#fsave", box).onclick = async () => { await api(`/api/agents/${key}`, { method: "PUT", body: { prompt: $("#fp").value === a.default_prompt ? "" : $("#fp").value, model: $("#fm").value, effort: $("#fe").value, enabled: $("#fen").value === "1" } }); toast("Ficha guardada", "good"); openAgent(key); };
  if ($("#freset", box)) $("#freset", box).onclick = async () => { await api(`/api/agents/${key}/reset`, { method: "POST" }); openAgent(key); };
}
const lessonHTML = (key) => (e) => `<div class="lesson ${e.status}"><div class="row" style="gap:8px"><b>${esc(e.title)}</b>
  <span class="chip ${{ active: "good", conflict: "warn", pending: "info", disabled: "" }[e.status]}">${{ active: "Aplicada", conflict: "Contradicción", pending: "Pendiente", disabled: "Desactivada" }[e.status]}</span>
  ${e.agent_name ? `<span class="chip accent">${esc(e.agent_name)}</span>` : ""}<div class="grow"></div><span class="faint small">${esc(e.created)}${e.author ? " · " + esc(e.author) : ""}${e.role ? " (" + esc(e.role) + ")" : ""} · ${{ manual: "manual", chat: "chat", auto: "aprendizaje automático" }[e.source] || esc(e.source)}</span></div>
  <pre>${esc(e.content)}</pre>${e.conflict_reason ? `<div class="small" style="color:#fcd34d">⚖️ ${esc(e.conflict_reason)}</div>` : ""}${e.decided_by ? `<div class="faint small">Decidido por ${esc(e.decided_by)}</div>` : ""}
  <div class="row" style="gap:6px">${e.status === "conflict" || e.status === "pending" ? `<button class="btn sm good" data-res="activate" data-a="${key || e.agent}" data-id="${e.id}" title="Queda bajo tu responsabilidad">Activar igual</button>${e.status === "conflict" ? `<button class="btn sm" data-res="replace" data-a="${key || e.agent}" data-id="${e.id}">Reemplazar la anterior</button>` : ""}<button class="btn sm bad" data-res="discard" data-a="${key || e.agent}" data-id="${e.id}">Descartar</button>`
    : `<button class="btn sm ghost" data-tog="${e.enabled ? 0 : 1}" data-a="${key || e.agent}" data-id="${e.id}">${e.enabled ? "Desactivar" : "Activar"}</button>`}<button class="btn sm ghost" data-del-l="${e.id}" data-a="${key || e.agent}">${icon("trash", 14)}</button><span class="faint small mono" title="${esc(e.file)}">${esc(e.file.split(/[\\/]/).slice(-2).join("/"))}</span></div></div>`;
function bindLessons(root, refresh) {
  $$("[data-res]", root).forEach((b) => b.onclick = async () => {
    if (b.dataset.res === "activate" && !confirm("Activarla aunque contradiga otra lección. Queda registrado que lo decidiste tú y corre por tu cuenta. ¿Seguir?")) return;
    await api(`/api/training/${b.dataset.a}/${b.dataset.id}/resolve`, { method: "POST", body: { action: b.dataset.res, decided_by: localStorage.getItem("author") || "" } }); refresh(); updateCounts();
  });
  $$("[data-tog]", root).forEach((b) => b.onclick = async () => { await api(`/api/training/${b.dataset.a}/${b.dataset.id}`, { method: "PATCH", body: { enabled: b.dataset.tog === "1" } }); refresh(); });
  $$("[data-del-l]", root).forEach((b) => b.onclick = async () => { if (confirm("¿Borrar esta lección?")) { await api(`/api/training/${b.dataset.a}/${b.dataset.delL}`, { method: "DELETE" }); refresh(); } });
}

// ------------------------------------------------------------------ MEMORIA
VIEWS.memoria = async (el) => {
  const filter = localStorage.getItem("mem-filter") || "";
  const [feed, audits, st, brand] = await Promise.all([api(`/api/training${filter ? "?status=" + filter : ""}`), api("/api/audits"), api("/api/learning/status"), api("/api/brand")]);
  const last = audits[0];
  el.innerHTML = pageHead("🧠", "Memoria del equipo", "Todo lo que el equipo aprendió: lecciones de personas (admins y operadores), lo aprendido en el chat y lo que Shelton aprende solo de tus decisiones.",
    `<button class="btn" id="learn">${icon("sparkle", 16)} Aprender ahora</button><button class="btn primary" id="audit">${icon("brain", 16)} Revisar equipo</button>`) +
    `<div class="grid g3"><div class="card"><div class="k-label muted small" style="font-weight:700;letter-spacing:.06em">APRENDIZAJE AUTOMÁTICO</div>
      <select id="lmode" style="margin-top:10px"><option value="auto" ${brand.learning_mode === "auto" ? "selected" : ""}>Automático: aplica y avisa</option><option value="approve" ${brand.learning_mode === "approve" ? "selected" : ""}>Con aprobación: propone y tú decides</option><option value="off" ${brand.learning_mode === "off" ? "selected" : ""}>Apagado</option></select>
      <div class="hint" style="margin-top:8px">${st.pending_decisions} decisiones humanas sin procesar · ${st.total_decisions} en total. Revisa cada 30 min.</div></div>
    <div class="card" style="grid-column:span 2"><div class="card-title"><h3>Última revisión del equipo</h3><span class="right faint small">${last ? esc(last.created) : ""}</span></div>
      ${last ? `<div class="muted" style="margin-bottom:10px">${esc(last.summary)}</div><div class="grid" style="gap:8px">${last.issues.map((i) => `<div class="issue"><div class="row" style="gap:8px"><span class="chip ${i.severity === "alta" ? "bad" : i.severity === "media" ? "warn" : ""}">${esc(i.severity)}</span><b>${esc(i.problem)}</b><div class="grow"></div>${i.agents.map((a) => `<span class="chip accent">${esc(agentName(a))}</span>`).join("")}</div><div class="muted small">${esc(i.explanation)}</div><div class="small"><b>Sugerencia:</b> ${esc(i.suggestion)}</div></div>`).join("") || '<div class="chip good">Todo coherente</div>'}</div>` : '<div class="faint">Aún no hay revisiones. Pulsa «Revisar equipo».</div>'}</div></div>
    <div class="toolbar" style="margin-top:20px"><div class="seg" id="mf">${[["", "Todo"], ["conflict,pending", "Por decidir"], ["active", "Aplicadas"], ["disabled", "Desactivadas"]].map(([v, l]) => `<button data-v="${v}" class="${filter === v ? "on" : ""}">${l}</button>`).join("")}</div><span class="faint small">Carpeta: data/training/&lt;agente&gt;/</span></div>
    <div class="col" id="mlist">${feed.map(lessonHTML(null)).join("") || `<div class="card">${empty("🧠", "Sin lecciones. Enséñale a un agente desde su ficha o en el chat: «aprende que…».")}</div>`}</div>`;
  $$("#mf button").forEach((b) => b.onclick = () => { localStorage.setItem("mem-filter", b.dataset.v); route(); });
  $("#lmode").onchange = async (e) => { await api("/api/brand", { method: "PUT", body: { learning_mode: e.target.value } }); toast("Modo de aprendizaje guardado", "good"); };
  $("#learn").onclick = async (e) => { e.target.disabled = true; try { const r = await api("/api/learning/run", { method: "POST" }); toast(r.lessons.length ? `Aprendió ${r.lessons.length} lecciones nuevas` : "No hay suficientes decisiones nuevas para aprender", r.lessons.length ? "good" : ""); route(); } catch (err) { toast(esc(err.message), "bad"); } };
  $("#audit").onclick = async (e) => { e.target.disabled = true; toast("Shelton está leyendo la memoria de todo el equipo…", "", "🧠"); try { await api("/api/audits", { method: "POST" }); route(); } catch (err) { toast(esc(err.message), "bad"); e.target.disabled = false; } };
  bindLessons(el, route);
};

// ------------------------------------------------------------------ CONEXIONES
const CATS = { brain: ["🧠", "Cerebro"], engine: ["🎨", "Motores de imagen, video y sonido"], voice: ["🎙️", "Voz"], publisher: ["📣", "Publicación"], webhook: ["🔗", "Avisos (webhooks)"] };
VIEWS.conexiones = async (el) => {
  const [types, conns] = await Promise.all([api("/api/connection-types"), api("/api/connections")]);
  S.connTypes = types;
  el.innerHTML = pageHead("🔌", "Conexiones", "Local primero: tus GPUs (una o varias) con ComfyUI y tu servidor de voz. Las APIs en la nube son opcionales y solo se usan si las agregas.",
    `<button class="btn primary" id="add-conn">${icon("plus", 17)} Agregar conexión</button>`) +
    Object.entries(CATS).map(([cat, [ico, name]]) => {
      const items = conns.filter((c) => c.category === cat);
      return `<div class="section-title">${ico} ${name}</div><div class="grid g2">${items.map((c) => `<div class="card conn-card"><div class="conn-ico">${ico}</div><div class="grow">
        <div class="row" style="gap:8px"><b>${esc(c.name)}</b><span class="chip">${esc(c.type_name)}</span>${c.roles.length ? c.roles.map((r) => `<span class="chip accent">${{ generate: "generar", edit: "editar", subject: "referencias", video: "video", sound: "sonido" }[r] || r}</span>`).join("") : ""}<div class="grow"></div><span class="chip ${c.enabled ? "good" : ""}">${c.enabled ? "Activa" : "Apagada"}</span></div>
        <div class="faint small" style="margin:6px 0">${esc(c.config.url || c.config.chat_id || c.config.model || "")}</div>
        ${c.last_test?.message ? `<div class="small ${c.last_test.ok ? "" : ""}" style="color:${c.last_test.ok ? "#86efac" : "#fca5a5"}">${c.last_test.ok ? "✔" : "✖"} ${esc(c.last_test.message)}</div>` : ""}
        <div class="row" style="gap:6px;margin-top:10px"><button class="btn sm" data-test="${c.id}">Probar</button><button class="btn sm" data-edit="${c.id}">Editar</button><button class="btn sm ghost" data-del="${c.id}">${icon("trash", 14)}</button></div></div></div>`).join("") ||
        `<div class="card faint small">${cat === "engine" ? "Sin conexiones: se usa el ComfyUI de tu .env (por defecto http://127.0.0.1:8188)." : cat === "voice" ? "Sin conexiones: se usa el servidor de voz local de tu .env (http://127.0.0.1:8189)." : cat === "brain" ? "Se usa la ANTHROPIC_API_KEY de tu .env." : "Nada conectado todavía."}</div>`}</div>`;
    }).join("");
  $("#add-conn").onclick = () => {
    const box = modal("Agregar conexión", Object.entries(CATS).map(([cat, [ico, name]]) => `<div class="small muted" style="font-weight:700">${ico} ${name}</div><div class="catalog">${Object.entries(types.types).filter(([, t]) => t.category === cat).map(([k, t]) => `<button data-type="${k}"><b>${esc(t.name)}</b><span class="faint small">${esc((t.help || "").slice(0, 90))}</span></button>`).join("")}</div>`).join(""), "", true);
    $$("[data-type]", box).forEach((b) => b.onclick = () => connForm(b.dataset.type));
  };
  $$("[data-test]").forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "Probando…"; const r = await api(`/api/connections/${b.dataset.test}/test`, { method: "POST" }); toast(esc(r.message), r.ok ? "good" : "bad"); route(); });
  $$("[data-edit]").forEach((b) => b.onclick = () => connForm(null, conns.find((c) => c.id === b.dataset.edit)));
  $$("[data-del]").forEach((b) => b.onclick = async () => { if (confirm("¿Eliminar esta conexión?")) { await api(`/api/connections/${b.dataset.del}`, { method: "DELETE" }); route(); } });
};
function connForm(type, conn) {
  type = type || conn.type; const t = S.connTypes.types[type], cfg = conn?.config || {};
  const box = modal(`${conn ? "Editar" : "Nueva"} conexión · ${esc(t.name)}`, `${t.help ? `<div class="hint">${esc(t.help)}</div>` : ""}
    <label class="f">Nombre<input id="cn" value="${esc(conn?.name || t.name)}"></label>
    ${t.fields.map((f) => `<label class="f">${esc(f.label)}${f.required ? " *" : ""}${f.kind === "json" ? `<textarea data-k="${f.key}" rows="3" class="mono" placeholder="${esc(f.placeholder || "")}">${esc(typeof cfg[f.key] === "object" ? JSON.stringify(cfg[f.key]) : cfg[f.key] || "")}</textarea>` : `<input data-k="${f.key}" type="${f.kind === "secret" ? "password" : "text"}" value="${esc(cfg[f.key] || "")}" placeholder="${esc(f.placeholder || "")}">`}</label>`).join("")}
    ${t.category === "engine" ? `<div class="f small muted" style="font-weight:500">Usar esta conexión para:</div><div class="checks">${S.connTypes.engine_roles.map((r) => `<label><input type="checkbox" value="${r}" ${conn?.roles?.includes(r) ? "checked" : ""}> ${{ generate: "Generar imágenes", edit: "Editar", subject: "Escenas con modelos del banco", video: "Video", sound: "Efectos de sonido" }[r]}</label>`).join("")}</div><div class="hint">Si varias GPUs tienen el mismo rol, Shelton reparte el trabajo a la más libre.</div>` : ""}
    <label class="checks"><label><input type="checkbox" id="cen" ${conn?.enabled === false ? "" : "checked"}> Activa</label></label>`,
    `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="csave">Guardar y probar</button>`);
  if (type === "openrouter" || type === "openrouter_image") {
    const inp = $('[data-k="model"]', box), dl = document.createElement("datalist");
    dl.id = "or-models"; inp.setAttribute("list", "or-models"); box.append(dl);
    api(`/api/openrouter/models?kind=${type === "openrouter" ? "brain" : "image"}`).then((ms) => { dl.innerHTML = ms.map((m) => `<option value="${esc(m.id)}">${esc(m.name)} · $${m.price_in.toFixed(2)}/$${m.price_out.toFixed(2)} por millón${m.structured ? "" : " · sin JSON nativo"}</option>`).join(""); }).catch(() => {});
  }
  $("#csave", box).onclick = async () => {
    const config = {}; $$("[data-k]", box).forEach((i) => { if (i.value.trim()) config[i.dataset.k] = i.value.trim(); });
    const missing = t.fields.filter((f) => f.required && !config[f.key]); if (missing.length) return toast("Falta: " + esc(missing.map((f) => f.label).join(", ")), "bad");
    const body = { type, name: $("#cn").value, config, roles: $$(".checks input[value]:checked", box).map((i) => i.value).filter((v) => S.connTypes.engine_roles.includes(v)), enabled: $("#cen").checked };
    const c = conn ? await api(`/api/connections/${conn.id}`, { method: "PUT", body }) : await api("/api/connections", { method: "POST", body });
    closeModal(); const r = await api(`/api/connections/${c.id}/test`, { method: "POST" }); toast(esc(r.message), r.ok ? "good" : "bad"); S.engines = null; route();
  };
}

// ------------------------------------------------------------------ FLUJOS
VIEWS.flujos = async (el) => {
  const wfs = await api("/api/workflows");
  el.innerHTML = pageHead("🧩", "Flujos (workflows de ComfyUI)", "Los flujos que usa cada motor. Sube los tuyos exportados con «Save (API Format)» y usa marcadores como {{prompt}}, {{width}}, {{seed}}, {{image}}.",
    `<label class="btn primary">${icon("plus", 17)} Subir flujo<input type="file" id="wf-up" accept=".json" hidden></label>`) +
    `<div class="card"><table class="t"><thead><tr><th>Archivo</th><th>Qué hace</th><th>Marcadores</th><th>Nodos</th><th></th></tr></thead><tbody>${wfs.map((w) => `<tr><td><b class="mono">${esc(w.name)}</b> ${w.builtin ? '<span class="chip">incluido</span>' : '<span class="chip accent">tuyo</span>'}</td>
      <td class="muted" style="max-width:420px">${esc(w.doc || w.error || "")}</td><td>${(w.placeholders || []).map((p) => `<span class="chip" style="margin:2px">{{${esc(p)}}}</span>`).join("")}</td><td class="muted">${w.nodes || ""}</td>
      <td style="white-space:nowrap"><button class="btn sm" data-view-wf="${esc(w.name)}">Ver</button>${w.builtin ? "" : ` <button class="btn sm ghost" data-del-wf="${esc(w.name)}">${icon("trash", 14)}</button>`}</td></tr>`).join("")}</tbody></table></div>
    <div class="hint" style="margin-top:12px">Para usar un flujo tuyo, ponlo en tu conexión ComfyUI (campos «Workflow generar / editar / referencias») o en <code>.env</code>. Si subes uno con el mismo nombre que uno incluido, el tuyo tiene prioridad. La pantalla Ajustes → Estado revisa que tu ComfyUI tenga todos los nodos y modelos.</div>`;
  $("#wf-up").onchange = async (e) => { const fd = new FormData(); fd.append("file", e.target.files[0]); try { const r = await api("/api/workflows", { method: "POST", body: fd }); toast(`Flujo ${esc(r.name)} guardado · marcadores: ${esc(r.placeholders.join(", ") || "ninguno")}`, "good"); route(); } catch (err) { toast(esc(err.message), "bad"); } };
  $$("[data-view-wf]").forEach((b) => b.onclick = async () => { const j = await api(`/api/workflows/${encodeURIComponent(b.dataset.viewWf)}`); modal(esc(b.dataset.viewWf), `<div class="code">${esc(JSON.stringify(j, null, 2))}</div>`, "", true); });
  $$("[data-del-wf]").forEach((b) => b.onclick = async () => { if (confirm("¿Eliminar este flujo?")) { await api(`/api/workflows/${encodeURIComponent(b.dataset.delWf)}`, { method: "DELETE" }); route(); } });
};

// ------------------------------------------------------------------ ACTIVIDAD
VIEWS.actividad = async (el) => {
  const agent = localStorage.getItem("act-agent") || "", failed = localStorage.getItem("act-failed") === "1";
  const runs = await api(`/api/runs?limit=300${agent ? "&agent=" + agent : ""}${failed ? "&failed=true" : ""}`);
  const tot = runs.reduce((a, r) => ({ t: a.t + r.tokens, c: a.c + r.cost }), { t: 0, c: 0 });
  el.innerHTML = pageHead("📈", "Actividad", "Cada llamada de cada agente: qué hizo, cuánto tardó y cuántos tokens usó.") +
    `<div class="toolbar"><select id="aa" style="width:240px"><option value="">Todos los agentes</option>${S.agents.map((a) => `<option value="${a.key}" ${a.key === agent ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
     <div class="seg" id="af"><button data-v="0" class="${!failed ? "on" : ""}">Todas</button><button data-v="1" class="${failed ? "on" : ""}">Solo fallidas</button></div>
     <div class="grow"></div><span class="chip">${fmtN(tot.t)} tokens</span><span class="chip">${fmtMoney(tot.c)} estimado</span></div>
    <div class="card"><table class="t"><thead><tr><th></th><th>Agente</th><th>Qué hizo</th><th>Modelo</th><th>Tokens</th><th>Costo</th><th>Tiempo</th><th>Cuándo</th></tr></thead><tbody>${runs.map((r) => `<tr><td>${r.ok ? "✅" : "⚠️"}</td><td><div class="row" style="gap:8px">${avatar(r.agent, true)}${esc(r.agent_name)}</div></td><td>${esc(r.summary || "—")}${r.error ? `<div class="small" style="color:#fca5a5">${esc(r.error)}</div>` : ""}</td><td class="faint small">${esc(r.model)}</td><td>${fmtN(r.tokens)}</td><td class="muted">${fmtMoney(r.cost)}</td><td class="muted">${(r.ms / 1000).toFixed(1)}s</td><td class="muted">${when(r.created_at)}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">Sin actividad.</td></tr>'}</tbody></table></div>`;
  $("#aa").onchange = (e) => { localStorage.setItem("act-agent", e.target.value); route(); };
  $$("#af button").forEach((b) => b.onclick = () => { localStorage.setItem("act-failed", b.dataset.v); route(); });
};

// ------------------------------------------------------------------ MARCA
VIEWS.marca = async (el) => {
  const b = await api("/api/brand");
  el.innerHTML = pageHead("🏷️", "Marca", "Todo el contenido sigue este perfil. También aquí: piloto automático diario y frecuencia del scout.") +
    `<form class="card col" id="bf" style="max-width:900px"><div class="row"><label class="f grow">Nombre<input name="name" value="${esc(b.name)}"></label><label class="f grow">Público<input name="audience" value="${esc(b.audience)}"></label></div>
      <label class="f">Qué es / qué vende<textarea name="description" rows="2">${esc(b.description)}</textarea></label>
      <div class="row"><label class="f grow">Tono<input name="tone" value="${esc(b.tone)}"></label><label class="f grow">Estilo visual<input name="visual_style" value="${esc(b.visual_style)}"></label></div>
      <label class="f">Pilares de contenido (separados por coma)<input name="pillars" value="${esc((b.pillars || []).join(", "))}"></label>
      <label class="f">Hashtags fijos<input name="base_hashtags" value="${esc((b.base_hashtags || []).join(" "))}" placeholder="#mimarca #ciudad"></label>
      <div class="f small muted" style="font-weight:500">Redes por defecto</div><div class="checks" id="bp">${S.platforms.filter((p) => p.key !== "original").map((p) => `<label><input type="checkbox" value="${p.key}" ${(b.platforms || []).includes(p.key) ? "checked" : ""}> ${esc(p.name)}</label>`).join("")}</div>
      <div class="row"><label class="f" style="width:220px">Piloto automático (posts/día)<input name="autopilot_daily" type="number" min="0" max="200" value="${b.autopilot_daily || 0}"></label><label class="f" style="width:180px">Hora del lote diario<input name="autopilot_hour" type="number" min="0" max="23" value="${b.autopilot_hour ?? 6}"></label>
      <label class="f" style="width:220px">Scout cada (horas, 0 = apagado)<input name="scout_every_hours" type="number" min="0" max="168" value="${b.scout_every_hours ?? 12}"></label></div>
      <div><button class="btn primary">Guardar marca</button></div></form>`;
  $("#bf").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    await api("/api/brand", { method: "PUT", body: { name: f.name.value, audience: f.audience.value, description: f.description.value, tone: f.tone.value, visual_style: f.visual_style.value,
      pillars: f.pillars.value.split(",").map((s) => s.trim()).filter(Boolean), base_hashtags: f.base_hashtags.value.split(/\s+/).filter(Boolean),
      platforms: $$("#bp input:checked").map((i) => i.value), autopilot_daily: +f.autopilot_daily.value || 0, autopilot_hour: +f.autopilot_hour.value || 0, scout_every_hours: +f.scout_every_hours.value || 0 } });
    toast("Marca guardada", "good");
  };
};

// ------------------------------------------------------------------ AJUSTES (marca blanca + estado)
VIEWS.ajustes = async (el) => {
  const w = await api("/api/whitelabel");
  el.innerHTML = pageHead("⚙️", "Ajustes", "Marca blanca del producto y estado del sistema.") +
    `<div class="grid g2"><form class="card col" id="wf"><h3>Marca blanca</h3><div class="hint">Cambia el nombre, el logo y los colores para revenderlo o replicarlo con otra marca. No hace falta tocar código.</div>
      <div class="row"><label class="f grow">Nombre del producto<input name="product_name" value="${esc(w.product_name)}"></label><label class="f grow">Nombre del agente jefe<input name="chief_name" value="${esc(w.chief_name)}"></label></div>
      <label class="f">Lema<input name="tagline" value="${esc(w.tagline)}"></label>
      <div class="row"><label class="f" style="width:140px">Color principal<input type="color" name="accent" value="${esc(w.accent)}"></label><label class="f" style="width:140px">Color secundario<input type="color" name="accent2" value="${esc(w.accent2)}"></label>
      <label class="f grow">Logo<input type="file" id="logo-up" accept="image/*"></label></div>
      <label class="f">URL de soporte (opcional)<input name="support_url" value="${esc(w.support_url)}"></label>
      <div class="hint">Para cambiar también la personalidad del jefe, edita su ficha en Agentes.</div><div><button class="btn primary">Guardar marca blanca</button></div></form>
    <div class="card col"><div class="card-title"><h3>Estado del sistema</h3><button class="btn sm right" id="rs">${icon("refresh", 15)} Revisar</button></div><div id="sbox"><div class="spinner"></div></div></div></div>`;
  $("#wf").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    S.wl = await api("/api/whitelabel", { method: "PUT", body: { product_name: f.product_name.value, chief_name: f.chief_name.value, tagline: f.tagline.value, accent: f.accent.value, accent2: f.accent2.value, support_url: f.support_url.value } });
    if ($("#logo-up").files[0]) { const fd = new FormData(); fd.append("file", $("#logo-up").files[0]); S.wl = await api("/api/whitelabel/logo", { method: "POST", body: fd }); }
    applyWL(); toast("Marca blanca aplicada", "good");
  };
  const status = async () => {
    $("#sbox").innerHTML = '<div class="spinner"></div>';
    const h = await api("/api/health");
    const li = (ok, t) => `<div class="row" style="gap:10px;align-items:flex-start"><span class="dot ${ok ? "ok" : "bad"}" style="margin-top:6px"></span><div class="grow">${t}</div></div>`;
    $("#sbox").innerHTML = `<div class="col" style="gap:14px">${li(h.claude, `<b>Cerebro</b>: ${h.provider === "openrouter" ? "OpenRouter" : "Claude (Anthropic)"} · <span class="mono">${esc(h.model)}</span> ${h.claude ? "" : "— falta la clave: ponla en .env o en Conexiones → Cerebro (Anthropic u OpenRouter)"}`)}
      ${h.engines.map((e) => li(e.ok, `<b>${esc(e.name)}</b> · ${esc(e.engine || "")} ${e.url ? `<span class="mono faint">${esc(e.url)}</span>` : ""}${e.note ? `<div class="faint small">${esc(e.note)}</div>` : ""}${e.error ? `<div class="small" style="color:#fca5a5">${esc(e.error)}</div>` : ""}
        ${(e.devices || []).map((d) => `<div class="faint small">GPU: ${esc(d.name)} · ${d.vram_gb} GB</div>`).join("")}
        ${(e.missing_models || []).length ? `<div class="small" style="margin-top:6px"><b>Modelos que faltan:</b><br>${e.missing_models.map((m) => `<span class="chip warn" style="margin:2px">${esc(m)}</span>`).join("")}</div>` : ""}
        ${(e.workflow_problems || []).length ? `<div class="small" style="margin-top:6px"><b>Problemas:</b><br>${e.workflow_problems.map(esc).join("<br>")}</div>` : ""}`)).join("")}
      ${li(h.ffmpeg, "<b>ffmpeg</b> para video y audio")}
      <div class="faint small">Cola: ${h.queue.pending} pendientes · ${h.queue.running} en curso · ${h.queue.ready_today} listos hoy · ${h.queue.workers} trabajadores</div></div>`;
  };
  $("#rs").onclick = status; status();
};
