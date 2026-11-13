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
      <div><b>${m.best ? "⭐ " : ""}${esc(m.name)}</b><div class="faint small">${esc(m.good || "")}</div><code class="faint small">${esc(m.id)}</code></div></label>`;
  $(".m-body", box).innerHTML = `<div class="row" style="gap:6px;flex-wrap:wrap">${tabs}</div>
    <div class="hint">Lo que elijas aquí es lo que usa <b>${esc(d.name)}</b> en todo Shelton cuando lo eliges como Motor del chat (o cuando le toca trabajar). ⭐ = recomendado. Cada imagen o video tiene costo: lo ves en Consumo.</div>
    ${Object.entries(d.roles).map(([role, r]) => `<div class="cm-role"><div class="row" style="gap:8px;align-items:center"><b class="grow">${esc(r.label)}</b>
        ${r.custom ? `<button class="btn sm ghost" data-cmreset="${role}">Volver al recomendado</button>` : ""}</div>
      ${r.models.length > 8 ? `<input class="cm-search" data-cmsearch="${role}" placeholder="Buscar entre ${r.models.length} modelos…">` : ""}
      <div class="cm-list" data-cmlist="${role}">${r.models.map((m) => card(role, m, r.current)).join("")}
        ${r.models.some((m) => m.id === r.current) ? "" : card(role, { id: r.current, name: "Elegido a mano", good: "" }, r.current)}</div>
      ${r.live_count ? `<div class="faint small">Incluye ${r.live_count} modelos del catálogo en vivo de fal.</div>` : ""}
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
