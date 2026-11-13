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
      <button class="btn sm ghost" id="am-change" title="Cambiar los modelos de cada ecosistema">Cambiar</button>`;
    $("#am-change", bar).onclick = () => openCloudModels();
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
