// Shelton Studio — Calidad de imagen: switch «Calidad máxima», comparador de versiones de la LoRA y
// «Entrenando ahora» en el Banco. Todo se SUMA: el botón y el diálogo de entrenar siguen exactamente igual.
"use strict";

// ------------------------------------------------------------------ switch «Calidad máxima» (barra de arriba)
async function qualityButton() {
  const seg = $("#mode-seg");
  if (!seg || $("#q-btn")) return;
  const b = document.createElement("button");
  b.className = "btn sm"; b.id = "q-btn"; b.title = "Calidad máxima de imagen en la GPU local";
  seg.after(b);
  const paint = (q) => { b.innerHTML = q.max_quality ? "✨ Calidad máxima" : "✨ Calidad: normal"; b.classList.toggle("primary", !!q.max_quality); };
  try { paint(await api("/api/calidad")); } catch { paint({}); }
  b.onclick = () => qualityDialog(paint);
}

async function qualityDialog(paint) {
  let q;
  try { q = await api("/api/calidad"); } catch (e) { return toast(esc(e.message), "bad"); }
  const box = modal("✨ Calidad de imagen", `
    <div class="hint">Para fotos en la <b>GPU local</b> (modo Local, con la LoRA de cada modelo). No cambia de modelo: le saca el máximo al que ya usan.</div>
    <label class="q-row"><input type="checkbox" id="q-on" ${q.max_quality ? "checked" : ""}><div><b>Calidad máxima</b><div class="faint small">Cada foto tarda más (≈30-60 s en vez de unos segundos) pero sale mucho más nítida.</div></div></label>
    <div id="q-more" class="grid" style="gap:8px;${q.max_quality ? "" : "opacity:.5"}">
      <label class="q-row"><input type="checkbox" id="q-refine" ${q.refine ? "checked" : ""}><div><b>Segunda pasada de detalle</b><div class="faint small">Se genera más grande y se afina: piel, pelo y telas más nítidos.</div></div></label>
      <label class="q-row"><input type="checkbox" id="q-face" ${q.face ? "checked" : ""}><div><b>Mejora de rostro</b> ${q.face_available ? '<span class="chip good">instalada</span>' : '<span class="chip warn">falta instalar</span>'}<div class="faint small">Detecta la cara y la vuelve a hacer en alta resolución con la LoRA (ojos, dientes, piel).${q.face_available ? "" : " Se instala con el script de la GPU (ver abajo); mientras tanto se salta sola."}</div></div></label>
      <label class="q-row"><span style="width:18px"></span><div><b>Fotos por intento</b> <select id="q-cand" style="width:auto;display:inline-block;padding:2px 8px;margin-left:6px">${[1, 2, 3, 4, 5, 6].map((n) => `<option ${n === q.candidates ? "selected" : ""}>${n}</option>`).join("")}</select><div class="faint small">Genera varias y el Juez de calidad se queda con la mejor.</div></div></label>
    </div>
    <label class="q-row"><input type="checkbox" id="q-cap" ${q.detailed_captions ? "checked" : ""}><div><b>Descripciones detalladas al entrenar LoRA</b><div class="faint small">Antes de entrenar, describe cada foto (pose, ropa, luz, fondo) para que la LoRA aprenda a la persona y no la ropa ni el fondo. En las tarjetas «pro» se hace siempre.</div></div></label>
    ${q.face_available ? "" : `<details class="card" style="padding:10px"><summary class="small" style="cursor:pointer">Cómo instalar la mejora de rostro</summary><div class="small" style="margin-top:6px">En la placa (root): <code>bash /root/shelton-studio/scripts/quality_nodes.sh /root/ComfyUI</code> y después <code>systemctl restart shelton-studio-comfy</code>.</div></details>`}`,
    `<button class="btn ghost" onclick="closeModal()">Cerrar</button><button class="btn primary" id="q-save">Guardar</button>`);
  $("#q-on", box).onchange = (e) => { $("#q-more", box).style.opacity = e.target.checked ? "" : ".5"; };
  $("#q-save", box).onclick = async () => {
    try {
      const r = await api("/api/calidad", { method: "POST", body: { max_quality: $("#q-on", box).checked, refine: $("#q-refine", box).checked,
        face: $("#q-face", box).checked, candidates: +$("#q-cand", box).value, detailed_captions: $("#q-cap", box).checked } });
      paint(r); closeModal(); toast(r.max_quality ? "Calidad máxima activada para las fotos locales" : "Calidad normal", "good", "✨");
    } catch (e) { toast(esc(e.message), "bad"); }
  };
}
qualityButton();

// ------------------------------------------------------------------ Banco: «Entrenando ahora» + «Comparar versiones»
const _bancoCalidad = VIEWS.banco;
let trainingTimer = null;
VIEWS.banco = async (el) => {
  await _bancoCalidad(el);
  const grid = $(".post-grid", el);
  if (grid && !$("#training-now", el)) {
    const now = document.createElement("div"); now.id = "training-now"; grid.before(now);
    renderTrainingNow(now);
    if (trainingTimer) clearInterval(trainingTimer);
    trainingTimer = setInterval(() => { const n = document.getElementById("training-now"); if (!n) { clearInterval(trainingTimer); trainingTimer = null; return; } renderTrainingNow(n); }, 8000);
  }
  let subs = [];
  try { subs = await api("/api/subjects"); } catch { return; }
  const byId = Object.fromEntries(subs.map((s) => [s.id, s]));
  $$(".subject-card").forEach((card) => {
    const sid = $("[data-del]", card)?.dataset.del, s = byId[sid];
    if (!s || $("[data-lvar]", card) || $(".base-chip", card)) return;
    const base = (s.lora?.info || {}).base;
    if (base === "sdxl" || base === "flux") {
      const chip = document.createElement("span"); chip.className = "chip accent base-chip";
      chip.textContent = base === "flux" ? "LoRA para Flux" : "LoRA para SDXL"; chip.title = "Su LoRA se entrena y se usa con ese motor";
      ($(".lora-box", card) || $(".body", card))?.prepend(chip);
    } else if (s.kind === "person" || s.kind === "pet" || s.kind === "creature") {
      const b = document.createElement("button");
      b.className = "btn sm ghost"; b.dataset.lvar = sid; b.textContent = "➕ Versión SDXL / Flux";
      b.title = "Una copia con las mismas fotos que aprende su cara para el motor SDXL o Flux";
      b.onclick = () => variantDialog(sid, s.name);
      ($(".lora-box", card) || $(".body", card))?.append(b);
    }
  });
  $$(".subject-card").forEach((card) => {
    const sid = $("[data-del]", card)?.dataset.del, s = byId[sid];
    const L = s?.lora || {};
    if (!s || !L.trigger || $("[data-lcmp]", card)) return;
    const b = document.createElement("button");
    b.className = "btn sm ghost"; b.dataset.lcmp = sid; b.textContent = "🔍 Comparar versiones";
    b.title = "Las mismas fotos con cada versión de su LoRA, lado a lado, para elegir la mejor";
    b.onclick = () => compareDialog(sid, s.name);
    ($(".lora-box", card) || $(".body", card))?.append(b);
  });
};

async function renderTrainingNow(box) {
  let list = [];
  try { list = await api("/api/granja/entrenando"); } catch { return; }
  if (!list.length) { box.innerHTML = ""; return; }
  const tr = list.filter((x) => x.status === "training"), q = list.filter((x) => x.status === "queued");
  box.innerHTML = `<div class="card tn-card"><div class="row" style="gap:8px"><b>🎓 Entrenando ahora${tr.length > 1 ? ` (${tr.length} a la vez)` : ""}</b></div>
    ${tr.map((x) => `<div class="tn-row"><span class="tn-name">${esc(x.name)}</span><div class="lora-bar grow"><i style="width:${Math.max(2, x.progress)}%"></i></div>
      <span class="small">${x.progress}%${x.eta_min != null ? ` · faltan ~${x.eta_min >= 60 ? Math.floor(x.eta_min / 60) + " h " + (x.eta_min % 60) + " min" : x.eta_min + " min"}` : ""}</span></div>`).join("") || `<div class="faint small">Preparando…</div>`}
    ${q.length ? `<div class="small" style="margin-top:6px">⏳ <b>En fila:</b> ${q.map((x) => esc(x.name)).join(", ")} <span class="faint">(empiezan solas cuando se libere un lugar)</span></div>` : ""}</div>`;
}

// ------------------------------------------------------------------ Comparador de versiones
async function compareDialog(sid, name) {
  let d;
  try { d = await api(`/api/subjects/${sid}/lora/compare`); } catch (e) { return toast(esc(e.message), "bad"); }
  const cands = d.candidates || [];
  if (!cands.length) return toast("Todavía no hay versiones guardadas de esta LoRA", "warn");
  const pre = new Set([...cands.filter((c) => c.active).map((c) => c.key), ...cands.slice(0, 4).map((c) => c.key)].slice(0, 4));
  const box = modal(`🔍 Comparar versiones · ${esc(name)}`, `
    <div class="hint">Shelton hace <b>las mismas 4 fotos</b> (misma escena y semilla) con cada versión que elijas. Así se ve con los ojos cuál sale mejor. <b>No cambia nada</b> hasta que aprietes «Usar esta».</div>
    <div class="small muted" style="font-weight:700">Versiones (hasta 6)</div>
    <div class="cmp-list">${cands.map((c) => `<label class="cmp-opt"><input type="checkbox" value="${esc(c.key)}" ${pre.has(c.key) ? "checked" : ""}>
      <b>${c.step.toLocaleString("es")} pasos</b> <span class="faint small">${esc(c.kind)}</span>${c.active ? ' <span class="chip good">en uso</span>' : ""}</label>`).join("")}</div>
    <div class="small muted" style="font-weight:700;margin-top:8px">Fuerza de la LoRA</div>
    <div class="cmp-list">${[0.7, 0.8, 0.9, 1.0].map((v) => `<label class="cmp-opt"><input type="checkbox" class="cmp-st" value="${v}" ${v === 1.0 ? "checked" : ""}> ${v}</label>`).join("")}</div>
    <div class="faint small">Tip: primero comparen versiones con fuerza 1.0; después, con la mejor versión, comparen fuerzas (0.8 suele verse más natural).</div>
    <div id="cmp-out"></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cerrar</button><button class="btn primary" id="cmp-go">Comparar</button>`, true);
  const run = async () => {
    const keys = $$(".cmp-opt input:not(.cmp-st):checked", box).map((i) => i.value);
    const strengths = $$(".cmp-st:checked", box).map((i) => +i.value);
    try { const job = await api(`/api/subjects/${sid}/lora/compare`, { method: "POST", body: { keys, strengths } }); poll(job.id); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  const poll = async (id) => {
    let job;
    try { job = await api(`/api/subjects/${sid}/lora/compare/${id}`); } catch { return; }
    if (!document.body.contains(box)) return;
    renderCompare($("#cmp-out", box), job, sid);
    if (job.status === "queued" || job.status === "running") setTimeout(() => poll(id), 3000);
  };
  $("#cmp-go", box).onclick = run;
  if (d.latest && (d.latest.status === "running" || d.latest.status === "queued" || d.latest.status === "done")) poll(d.latest.id);
}

function renderCompare(out, job, sid) {
  const done = job.cells.filter((c) => c.status === "done" || c.status === "failed").length;
  const rows = [];
  job.versions.forEach((v) => job.strengths.forEach((st) => rows.push({ v, st, cells: job.cells.filter((c) => c.key === v.key && c.strength === st) })));
  out.innerHTML = `<div class="row" style="gap:8px;margin:10px 0"><b>${job.status === "done" ? "Listo" : job.status === "failed" ? "Falló" : "Generando…"}</b>
      <span class="faint small">${done} de ${job.cells.length} fotos${job.status === "queued" || (job.status === "running" && !done) ? " · si la GPU está entrenando, empieza cuando termine" : ""}</span>${job.error ? `<span class="small" style="color:#fca5a5">${esc(job.error)}</span>` : ""}</div>
    <div class="cmp-grid">${rows.map((r) => `<div class="cmp-row"><div class="cmp-head"><b>${r.v.step.toLocaleString("es")} pasos</b><span class="faint small">fuerza ${r.st}</span>
        <button class="btn sm primary" data-choose="${esc(r.v.key)}" data-st="${r.st}" ${r.cells.every((c) => c.status === "done") ? "" : "disabled"}>Usar esta</button></div>
      <div class="cmp-imgs">${r.cells.map((c) => c.image ? `<img src="${esc(c.image)}" loading="lazy" data-zoom="${esc(c.image)}">` : `<div class="cmp-ph">${c.status === "failed" ? "⚠" : c.status === "running" ? '<div class="spinner"></div>' : "…"}</div>`).join("")}</div></div>`).join("")}</div>`;
  $$("[data-zoom]", out).forEach((im) => im.onclick = () => window.open(im.dataset.zoom, "_blank"));
  $$("[data-choose]", out).forEach((b) => b.onclick = async () => {
    if (!confirm(`¿Usar la versión de ${b.closest(".cmp-row").querySelector("b").textContent} con fuerza ${b.dataset.st}? Las demás siguen guardadas.`)) return;
    try {
      await api(`/api/subjects/${sid}/lora/choose`, { method: "POST", body: { key: b.dataset.choose, strength: +b.dataset.st } });
      closeModal(); toast("Listo: esa versión queda en uso (y el próximo «seguir entrenando» sigue desde ahí).", "good", "🎓"); route();
    } catch (e) { toast(esc(e.message), "bad"); }
  });
}

// ------------------------------------------------------------------ Versión SDXL / Flux de un modelo
function variantDialog(sid, name) {
  const box = modal(`➕ Versión de ${esc(name)} para otro motor`, `
    <div class="hint">Cada motor necesita su propia LoRA: la de Z-Image no sirve en SDXL ni en Flux. Esto crea <b>${esc(name)} · SDXL</b> o <b>${esc(name)} · Flux</b>
      con las mismas fotos y descripción. Después se entrena con el botón de siempre. La original no cambia.</div>
    <div class="grid g2" style="gap:10px">
      <button class="card q-row" data-vb="sdxl" style="text-align:left"><div><b>SDXL</b><div class="faint small">Hiperrealismo (RealVisXL) y miles de estilos. Entrena bien en la 3090.</div></div></button>
      <button class="card q-row" data-vb="qwen" style="text-align:left"><div><b>Qwen-Image · Profesional ⭐</b><div class="faint small">Fotos de nivel profesional (20B, uso comercial). Se entrena SOLO en la RTX PRO 6000; después genera también en las 3090 de la placa (motor «Calidad máxima»).</div></div></button>
      <button class="card q-row" data-vb="flux" style="text-align:left"><div><b>Flux.1 Dev</b><div class="faint small">El más realista, manos y texto. Ideal entrenarla en la RTX PRO 6000. Pide aceptar la licencia de Flux en Hugging Face.</div></div></button>
    </div>
    <div class="faint small">Para generar con ella: en la conversación, elige el motor «SDXL» o «Flux» (o pídeselo a Shelton) y nombra a «${esc(name)} · SDXL» / «· Flux».</div>`,
    `<button class="btn ghost" onclick="closeModal()">Cerrar</button>`);
  $$("[data-vb]", box).forEach((b) => b.onclick = async () => {
    try { const r = await api(`/api/subjects/${sid}/lora/variante`, { method: "POST", body: { base: b.dataset.vb } });
      closeModal(); toast(`Creada <b>${esc(r.name)}</b>. Ahora entrénala con «🎓 Entrenar LoRA».`, "good", "➕"); route(); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
}
