// ------------------------------------------------------------------ 🎨 Modelos: elegir el modelo de cada ecosistema (Higgsfield, fal)
const _chatModelos = VIEWS.chat;
VIEWS.chat = async (el, convId) => {
  await _chatModelos(el, convId);
  const after = $("#video-models", el);
  if (!after || $("#cloud-models", el)) return;
  const b = document.createElement("button");
  b.className = "btn sm"; b.id = "cloud-models"; b.textContent = "🎨 Modelos";
  b.title = "Elegir qué modelo usa cada ecosistema en la nube (Higgsfield, fal) para generar, editar y video";
  b.onclick = () => openCloudModels();
  after.after(b);
};

async function openCloudModels(cid) {
  let conns = [];
  try { conns = await api("/api/catalogo/conexiones"); } catch (e) { return toast(esc(e.message), "bad"); }
  if (!conns.length) {
    return modal("🎨 Modelos", `<div class="hint">Todavía no conectaste ningún ecosistema en la nube. Ve a <a href="#/conexiones" onclick="closeModal()">Conexiones</a> → Agregar conexión → Higgsfield o fal.ai y pega su clave.</div>`);
  }
  cid = cid || conns[0].id;
  const box = modal("🎨 Modelos de cada ecosistema", `<div class="empty"><div class="spinner"></div>Cargando modelos…</div>`, "", true);
  let d;
  try { d = await api(`/api/catalogo/${cid}`); } catch (e) { $(".m-body", box).innerHTML = `<div class="qa-box" style="color:#fca5a5">${esc(e.message)}</div>`; return; }
  const tabs = conns.map((c) => `<button class="btn sm ${c.id === cid ? "primary" : "ghost"}" data-cmtab="${esc(c.id)}">${esc(c.name)}</button>`).join("");
  const card = (role, m, cur) => `<label class="cm-item ${m.id === cur ? "on" : ""}"><input type="radio" name="cm-${role}" value="${esc(m.id)}" ${m.id === cur ? "checked" : ""}>
      <div class="grow"><div class="row" style="gap:6px;flex-wrap:wrap"><b>${m.best ? "⭐ " : ""}${esc(m.name)}</b>${m.price ? `<span class="chip">${esc(m.price)}</span>` : ""}${m.date ? `<span class="faint small">${esc(m.date)}</span>` : ""}</div><div class="faint small">${esc(m.good || "")}</div><code class="faint small">${esc(m.id)}</code></div></label>`;
  $(".m-body", box).innerHTML = `<div class="row" style="gap:6px;flex-wrap:wrap">${tabs}</div>
    <div class="hint">Lo que elijas aquí es lo que usa <b>${esc(d.name)}</b> en todo Shelton cuando lo eliges como Motor del chat (o cuando le toca trabajar). ⭐ = recomendado. Cada imagen o video tiene costo: lo ves en Consumo.</div>
    ${Object.entries(d.roles).map(([role, r]) => `<div class="cm-role"><div class="row" style="gap:8px;align-items:center"><b class="grow">${esc(r.label)}</b>
        ${r.custom ? `<button class="btn sm ghost" data-cmreset="${role}">Volver al recomendado</button>` : ""}</div>
      ${r.models.length > 8 ? `<input class="cm-search" data-cmsearch="${role}" placeholder="Buscar entre ${r.models.length} modelos…">` : ""}
      <div class="cm-list" data-cmlist="${role}">${r.models.map((m) => card(role, m, r.current)).join("")}
        ${r.models.some((m) => m.id === r.current) ? "" : card(role, { id: r.current, name: "Elegido a mano", good: "" }, r.current)}</div>
      <div class="faint small">${r.models.length} modelos${r.live_count ? ` (${r.live_count} del catálogo completo de fal, al día)` : " (catálogo oficial completo de la API)"}.</div>
      <div class="row" style="gap:6px;margin-top:6px"><input class="grow" data-cmcustom="${role}" placeholder="¿Otro modelo? Pega su id (ej. fal-ai/…)"><button class="btn sm" data-cmuse="${role}">Usar</button></div></div>`).join("")}`;
  const pick = async (role, model) => {
    try { await api(`/api/catalogo/${cid}`, { method: "POST", body: { role, model } }); toast(model ? `Listo: ${esc(d.name)} usa <b>${esc(model)}</b>` : "Vuelve al recomendado", "good", "🎨"); S.engines = null; openCloudModels(cid); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  $$("[data-cmtab]", box).forEach((b) => b.onclick = () => openCloudModels(b.dataset.cmtab));
  $$(".cm-list input[type=radio]", box).forEach((i) => i.onchange = () => pick(i.name.slice(3), i.value));
  $$("[data-cmreset]", box).forEach((b) => b.onclick = () => pick(b.dataset.cmreset, ""));
  $$("[data-cmuse]", box).forEach((b) => b.onclick = () => { const v = $(`[data-cmcustom="${b.dataset.cmuse}"]`, box).value.trim(); if (v) pick(b.dataset.cmuse, v); });
  $$("[data-cmsearch]", box).forEach((inp) => inp.oninput = () => {
    const q = inp.value.toLowerCase();
    $$(`[data-cmlist="${inp.dataset.cmsearch}"] .cm-item`, box).forEach((it) => { it.style.display = it.textContent.toLowerCase().includes(q) ? "" : "none"; });
  });
}


// ------------------------------------------------------------------ Motor ordenado + modelo activo arriba del chat
function orderEngineSelect(sel) {
  if (!sel || sel.dataset.ordered || !S.engines?.image) return;
  sel.dataset.ordered = "1";
  const cur = sel.value;
  const groups = { "🦎 Placa (gratis)": [], "⚡ Tarjeta RTX": [], "☁ Nube (con costo)": [] };
  for (const e of S.engines.image) {
    const g = e.id.startsWith("rtx:") ? "⚡ Tarjeta RTX" : e.local ? "🦎 Placa (gratis)" : "☁ Nube (con costo)";
    groups[g].push(e);
  }
  sel.innerHTML = Object.entries(groups).filter(([, l]) => l.length).map(([g, l]) =>
    `<optgroup label="${esc(g)}">${l.map((e) => `<option value="${esc(e.id)}">${esc(e.label)}</option>`).join("")}</optgroup>`).join("");
  sel.value = cur;
}

async function paintActiveModel() {
  const head = $(".chat-head");
  if (!head) return;
  let bar = $("#active-model");
  if (!bar) { bar = document.createElement("div"); bar.id = "active-model"; bar.className = "active-model"; head.after(bar); }
  try {
    const a = await api(`/api/catalogo/activo/ahora?choice=${encodeURIComponent(S.chatEngine || "")}`);
    bar.innerHTML = `<span class="faint small" title="Un motor por tarea: fotos nuevas, ediciones y videos">Motor por tarea</span>
      <span class="am-chip" title="Con este se crean las fotos nuevas"><b>Fotos:</b> ${esc(a.generate || "—")}</span>
      <span class="am-chip am-sub" title="Solo cuando pides editar una foto, o una modelo que todavía no tiene LoRA"><b>Editar:</b> ${esc(a.edit || "—")}</span>
      <span class="am-chip am-sub" title="Solo cuando pides un video"><b>Video:</b> ${esc(a.video || "—")}</span>
      <button class="btn sm ghost" id="am-change" title="Cambiar el motor">Cambiar</button>`;
    // En la nube (Higgsfield / fal) «Cambiar» abre SUS modelos; en la placa o la RTX abre el menú Motor
    const cloud = (S.chatEngine || "").startsWith("conn:") && (S.engines?.image || []).some((e) => e.id === S.chatEngine && !e.local);
    $("#am-change", bar).onclick = () => {
      if (cloud) return openCloudModels(S.chatEngine.slice(5));
      const sel = $("#engine-sel"); sel?.focus(); try { sel?.showPicker?.(); } catch { /* algunos navegadores no lo permiten */ }
      toast("Elige el motor en el menú «Motor» de arriba. Los modelos de Higgsfield y fal se eligen con «🎨 Modelos».", "", "🎛️");
    };
  } catch { bar.remove(); }
}

const _chatT2 = VIEWS.chat;
VIEWS.chat = async (el, convId) => {
  await _chatT2(el, convId);
  const sel = $("#engine-sel", el);
  orderEngineSelect(sel);
  if (sel && !sel.dataset.am) { sel.dataset.am = "1"; sel.addEventListener("change", paintActiveModel); }
  paintActiveModel();
};
// al elegir un modelo en «🎨 Modelos» o al cambiar de conversación, se actualiza la barra
const _openCloudModelsT2 = openCloudModels;
openCloudModels = async (...a) => { const r = await _openCloudModelsT2(...a); paintActiveModel(); return r; };
const _loadConvT2 = typeof loadConv === "function" ? loadConv : null;
if (_loadConvT2) loadConv = async (...a) => { const r = await _loadConvT2(...a); orderEngineSelect($("#engine-sel")); paintActiveModel(); return r; };


// ------------------------------------------------------------------ ⚙️ Ajustes de foto (antes de generar; queda guardado)
async function openPhotoSettings() {
  let q;
  try { q = await api("/api/calidad"); } catch (e) { return toast(esc(e.message), "bad"); }
  const num = (id, label, val, help, step = 1, min = 0, max = 100) => `<label class="f">${label}<input id="${id}" type="number" step="${step}" min="${min}" max="${max}" value="${val || ""}" placeholder="Automático"><span class="faint small">${help}</span></label>`;
  const box = modal("⚙️ Ajustes de foto", `
    <div class="hint">Se aplican a las fotos que se hacen en la <b>placa</b> y en la <b>RTX</b> (no a la nube). Vacío = <b>automático</b>, lo que mejor rinde cada modelo. Queda guardado para todos los pedidos hasta que lo cambies.</div>
    <div class="ps-grid">
      ${num("ps-steps", "Pasos (steps)", q.steps, "Más pasos = más detalle y más lento. Z-Image rinde con 8-12; SDXL con 25-40.", 1, 0, 80)}
      ${num("ps-cfg", "CFG (SDXL)", q.cfg, "Qué tan al pie de la letra sigue el pedido. 4-7 es lo normal.", 0.5, 0, 20)}
      ${num("ps-guidance", "Guidance (Flux)", q.guidance, "Lo mismo que CFG pero para Flux. 3-4 es lo normal.", 0.5, 0, 10)}
      ${num("ps-lora", "Fuerza de la LoRA", q.lora_strength, "Cuánto se parece a la modelo. 0.8-1.0 es lo normal; más alto puede deformar.", 0.05, 0, 1.5)}
      ${num("ps-seed", "Semilla fija", q.seed, "Vacío = una distinta cada vez. Con un número fijo, el mismo pedido sale igual (sirve para comparar ajustes).", 1, 0, 2147483647)}
      <label class="f">Evitar siempre<input id="ps-neg" value="${esc(q.negative_extra || "")}" placeholder="Ej: tatuajes, texto, marca de agua"><span class="faint small">Se suma a lo que ya evita cada motor (SDXL).</span></label>
    </div>
    <h4 style="margin:14px 0 6px">✨ Calidad máxima</h4>
    <label class="q-row"><input type="checkbox" id="ps-max" ${q.max_quality ? "checked" : ""}><div><b>Activada</b><div class="faint small">Segunda pasada, retoque de cara y la mejor de varias. Más lento, más nítido.</div></div></label>
    <div class="ps-grid">
      ${num("ps-rscale", "Agrandar en la 2.ª pasada (×)", q.refine_scale, "1.5 = 50% más grande antes de retocar.", 0.1, 1, 2)}
      ${num("ps-rden", "Cuánto cambia la 2.ª pasada", q.refine_denoise, "0.3-0.4: mejora sin cambiar la foto. Más alto cambia más.", 0.05, 0.1, 0.6)}
      ${num("ps-cand", "Fotos para elegir la mejor", q.candidates, "Hace varias y el juez elige. 1 = más rápido.", 1, 1, 6)}
      <label class="q-row"><input type="checkbox" id="ps-face" ${q.face ? "checked" : ""}><div><b>Retoque de cara</b><div class="faint small">Corrige ojos y rasgos.</div></div></label>
    </div>`,
    `<button class="btn ghost" id="ps-reset">Volver a automático</button><button class="btn primary" id="ps-save">Guardar</button>`, true);
  const v = (id) => { const x = $(id, box).value; return x === "" ? 0 : +x; };
  $("#ps-save", box).onclick = async () => {
    try {
      await api("/api/calidad", { method: "POST", body: { steps: v("#ps-steps"), cfg: v("#ps-cfg"), guidance: v("#ps-guidance"), lora_strength: v("#ps-lora"),
        seed: v("#ps-seed"), negative_extra: $("#ps-neg", box).value.trim(), max_quality: $("#ps-max", box).checked,
        refine_scale: v("#ps-rscale") || 1.5, refine_denoise: v("#ps-rden") || 0.35, candidates: v("#ps-cand") || 1, face: $("#ps-face", box).checked } });
      closeModal(); toast("Ajustes de foto guardados: se usan desde la próxima foto", "good", "⚙️");
    } catch (e) { toast(esc(e.message), "bad"); }
  };
  $("#ps-reset", box).onclick = async () => {
    await api("/api/calidad", { method: "POST", body: { steps: 0, cfg: 0, guidance: 0, lora_strength: 0, seed: 0, negative_extra: "" } });
    closeModal(); toast("Volvió a automático", "good", "⚙️");
  };
}
const _chatPS = VIEWS.chat;
VIEWS.chat = async (el, convId) => {
  await _chatPS(el, convId);
  const after = $("#cloud-models", el) || $("#video-models", el);
  if (!after || $("#photo-settings", el)) return;
  const b = document.createElement("button");
  b.className = "btn sm"; b.id = "photo-settings"; b.textContent = "⚙️ Ajustes";
  b.title = "Pasos, CFG, semilla, fuerza de la LoRA, calidad máxima… (queda guardado)";
  b.onclick = openPhotoSettings;
  after.after(b);
};
