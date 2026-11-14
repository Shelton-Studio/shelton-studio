// Shelton Studio — 📸 Muestras del entrenamiento: cada 100 pasos la LoRA dibuja una foto y sigue entrenando.
// Cada foto tiene su número para siempre («Fiorela #0012 · paso 1.200»). Con ⭐ (o diciéndole a Shelton «usa la 12»)
// queda activa esa versión exacta. Solo agrega una pantalla: no cambia nada de lo demás.
"use strict";

ICONS.camera = '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>';
(function addMuestrasNav() {
  if (NAV.some((n) => Array.isArray(n) && n[0] === "muestras")) return;
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "banco");
  NAV.splice(i >= 0 ? i + 1 : NAV.length, 0, ["muestras", "camera", "Muestras LoRA"]);
  renderRail(); markNav();
})();

const fmtSteps = (n) => Number(n || 0).toLocaleString("es");
let muestrasTimer = null;
VIEWS.muestras = async (el) => {
  el.innerHTML = pageHead("📸", "Muestras del entrenamiento",
    "Mientras una LoRA entrena, cada 100 pasos dibuja una foto de prueba y sigue entrenando. Cada foto lleva su número y sus pasos: compara y quédate con la que te guste.") +
    `<div id="mu-body"><div class="card">${empty("⏳", "Cargando…")}</div></div>`;
  const pick = localStorage.getItem("mu-model") || "";
  const load = async () => {
    const body = document.getElementById("mu-body");
    if (!body) { clearInterval(muestrasTimer); muestrasTimer = null; return; }
    let d;
    try { d = await api("/api/muestras"); } catch (e) { body.innerHTML = `<div class="card">${empty("⚠️", esc(e.message))}</div>`; return; }
    const models = d.models || [];
    if (!models.length) {
      body.innerHTML = `<div class="card">${empty("📸", `Todavía no hay muestras. Aparecen solas mientras una modelo entrena (una cada ${d.every || 100} pasos). Los entrenamientos que empezaron antes de esta actualización no las tienen: empiezan a salir en el próximo.`)}</div>`;
      return;
    }
    const cur = models.find((m) => m.id === (localStorage.getItem("mu-model") || pick)) || models[0];
    const showArch = localStorage.getItem("mu-arch") === "1";
    const list = cur.samples.filter((x) => showArch ? x.archived : !x.archived);
    body.innerHTML = `<div class="seg" style="margin-bottom:12px">${models.map((m) => `<button data-mu="${esc(m.id)}" class="${m.id === cur.id ? "on" : ""}">${esc(m.name)}${m.training ? " · 🎓 entrenando" : ""} (${m.count})</button>`).join("")}</div>
      <div class="row" style="gap:8px;margin-bottom:8px"><div class="seg"><button data-muarch="0" class="${showArch ? "" : "on"}">Muestras (${cur.count})</button><button data-muarch="1" class="${showArch ? "on" : ""}">📦 Archivadas (${cur.archived || 0})</button></div></div>
      <div class="hint">Toca una foto para verla grande. Las que no te gusten: <b>📦</b> archívala (se esconde y la puedes recuperar) o <b>🗑</b> elimínala. <b>⭐ Usar esta</b> deja activa la LoRA exacta de ese paso (y el próximo entrenamiento sigue desde ahí). También puedes decirle a Shelton: «usa la foto ${cur.samples[0]?.n || 12} de ${esc(cur.name.replace("🔒 ", ""))}».</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:12px">
        ${list.length ? "" : `<div class="faint small">${showArch ? "No hay fotos archivadas." : "No hay fotos para mostrar."}</div>`}
        ${list.map((x) => `<div class="card" style="padding:8px">
          <div style="position:relative"><img src="${esc(x.url)}" loading="lazy" decoding="async" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px;cursor:zoom-in;display:block" data-muzoom="${esc(x.url)}">
            <span style="position:absolute;left:6px;bottom:6px;padding:3px 8px;border-radius:99px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;font-weight:700">#${String(x.n).padStart(4, "0")} · ${fmtSteps(x.step)} pasos</span>
            <span style="position:absolute;right:6px;top:6px;display:flex;gap:6px">
              <button class="icon-btn" data-muarc="${x.n}" data-on="${x.archived ? 0 : 1}" title="${x.archived ? "Sacar de archivadas" : "Archivar (se esconde, se puede recuperar)"}" style="width:34px;height:34px;background:rgba(0,0,0,.65);color:#fff">${x.archived ? "↩" : "📦"}</button>
              <button class="icon-btn" data-mudel="${x.n}" title="Eliminar esta foto" style="width:34px;height:34px;background:rgba(0,0,0,.65);color:#fff">🗑</button></span></div>
          <div class="row" style="gap:6px;margin-top:6px;flex-wrap:nowrap"><b style="font-size:16px">#${String(x.n).padStart(4, "0")}</b>
            <span class="chip accent" title="Pasos de entrenamiento con los que se hizo">${fmtSteps(x.step)} pasos</span></div>
          <div class="faint small" style="margin:4px 0 6px">${esc((x.at || "").replace("T", " ").slice(0, 16))}</div>
          <button class="btn sm" data-muuse="${x.n}" style="width:100%">⭐ Usar esta</button></div>`).join("")}
      </div>`;
    $$("[data-mu]", body).forEach((b) => b.onclick = () => { localStorage.setItem("mu-model", b.dataset.mu); load(); });
    $$("[data-muarch]", body).forEach((b) => b.onclick = () => { localStorage.setItem("mu-arch", b.dataset.muarch); load(); });
    $$("[data-muarc]", body).forEach((b) => b.onclick = async (e) => {
      e.stopPropagation(); b.disabled = true;
      try { await api(`/api/subjects/${cur.id}/lora/muestras/${b.dataset.muarc}/archivar?on=${b.dataset.on}`, { method: "POST" }); toast(b.dataset.on === "1" ? "Archivada" : "Recuperada", "good", "📦"); load(); }
      catch (err) { toast(esc(err.message), "bad"); b.disabled = false; }
    });
    $$("[data-mudel]", body).forEach((b) => b.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`¿Eliminar la foto #${String(b.dataset.mudel).padStart(4, "0")}? No se puede deshacer (el número no se vuelve a usar).`)) return;
      b.disabled = true;
      try { await api(`/api/subjects/${cur.id}/lora/muestras/${b.dataset.mudel}`, { method: "DELETE" }); toast("Eliminada", "good", "🗑"); load(); }
      catch (err) { toast(esc(err.message), "bad"); b.disabled = false; }
    });
    $$("[data-muzoom]", body).forEach((im) => im.onclick = () => window.open(im.dataset.muzoom, "_blank"));
    $$("[data-muuse]", body).forEach((b) => b.onclick = async () => {
      b.disabled = true;
      try { const r = await api(`/api/subjects/${cur.id}/lora/muestras/${b.dataset.muuse}/usar`, { method: "POST" }); toast(`Listo: ${esc(r.label)} es la versión activa`, "good", "⭐"); }
      catch (e) { toast(esc(e.message), "bad"); b.disabled = false; }
    });
  };
  await load();
  if (muestrasTimer) clearInterval(muestrasTimer);
  muestrasTimer = setInterval(load, 20000);
};

// Aviso en vivo cuando sale una muestra nueva
const _extraMuestras = window.onExtraEvent;
window.onExtraEvent = (ev) => {
  if (ev.type === "lora_sample") {
    if (!ev.private) toast(`📸 <b>${esc(ev.name)} #${String(ev.n).padStart(4, "0")}</b> · ${fmtSteps(ev.step)} pasos`, "", "📸",
      `<button class="btn sm" onclick="go('muestras')">Ver</button>`);
    if (location.hash.startsWith("#/muestras")) VIEWS.muestras($("#view"));
    return;
  }
  if (_extraMuestras) _extraMuestras(ev);
};

// ------------------------------------------------------------------ 🎯 Indicaciones al entrenar
// Panel «qué quieres que aprenda» + «cómo ver las muestras». Se usa en el diálogo de entrenar de siempre (se suma, no
// cambia lo que ya tenía) y en «⚡ Entrenar en la RTX». Se guarda solo mientras escribes.
async function briefPanel(sid) {
  let d = { brief: "", sample_prompt: "" };
  try { d = await api(`/api/subjects/${sid}/lora/indicaciones`); } catch {}
  const panel = document.createElement("div");
  panel.className = "card"; panel.id = "lbrief"; panel.style.cssText = "padding:12px;border-color:var(--accent)";
  panel.innerHTML = `<b>🎯 Indicaciones para este entrenamiento</b>
    <div class="faint small" style="margin:4px 0 8px">Cuéntale a Shelton cómo la quieres. Con esto describe cada foto al entrenar (qué tiene que aprender y qué no) y arma las fotos de muestra.</div>
    <label class="f">¿Qué quieres que aprenda?<textarea id="lb-brief" rows="4" placeholder="Ej: que aprenda su cara tal cual: lunar en la mejilla izquierda, ojos verdes, pecas suaves. Que NO aprenda la ropa ni los fondos de las fotos. La voy a usar para fotos de Instagram estilo lifestyle.">${esc(d.brief || "")}</textarea></label>
    <label class="f">📸 ¿Cómo quieres ver las fotos de muestra? (una cada 100 pasos)<input id="lb-sample" placeholder="Ej: retrato de frente, luz natural de ventana, fondo neutro, sonriendo" value="${esc(d.sample_prompt || "")}"></label>
    <div class="row" style="gap:8px;margin-top:4px"><label class="btn sm">📎 Agregar fotos de referencia (opcional)<input type="file" id="lb-photos" accept="image/*" multiple hidden></label>
      <span class="faint small" id="lb-photos-state">Se suman a sus fotos para aprender (por ejemplo, primeros planos de su cara o del rasgo que quieres que aprenda).</span></div>
    <div class="faint small" id="lb-state"></div>`;
  let t = null;
  const save = async () => {
    clearTimeout(t);
    $("#lb-state", panel).textContent = "Guardando…";
    try { await api(`/api/subjects/${sid}/lora/indicaciones`, { method: "PUT", body: { brief: $("#lb-brief", panel).value, sample_prompt: $("#lb-sample", panel).value } }); $("#lb-state", panel).textContent = "✓ Guardado: se usa al entrenar"; }
    catch (e) { $("#lb-state", panel).textContent = "⚠ " + e.message; throw e; }
  };
  $("#lb-photos", panel).onchange = async (ev) => {
    const files = [...ev.target.files]; if (!files.length) return;
    const st = $("#lb-photos-state", panel); let ok = 0;
    for (const f of files) {
      st.textContent = `Subiendo ${ok + 1} de ${files.length}…`;
      const fd = new FormData(); fd.append("file", f); fd.append("describe", "false");
      try { await api(`/api/subjects/${sid}/photos`, { method: "POST", body: fd }); ok++; } catch (e) { toast(esc(e.message), "bad"); }
    }
    st.textContent = `✓ ${ok} foto(s) agregada(s): entran en este entrenamiento.`;
    ev.target.value = "";
  };
  ["#lb-brief", "#lb-sample"].forEach((q) => { $(q, panel).oninput = () => { clearTimeout(t); t = setTimeout(() => save().catch(() => {}), 500); }; $(q, panel).onblur = () => save().catch(() => {}); });
  return { el: panel, save };
}

if (typeof loraDialog === "function") {
  const _loraDialog = loraDialog;
  loraDialog = async (sid, name) => {
    await _loraDialog(sid, name);
    const box = $("#modal .box");
    const plan = box && $("#lplan", box);
    if (!plan || $("#lbrief", box)) return;
    const bp = await briefPanel(sid);
    plan.before(bp.el);
    const go = $("#lgo", box);           // al tocar «Empezar», primero se guardan las indicaciones
    if (go) go.addEventListener("pointerdown", () => { bp.save().catch(() => {}); }, true);
  };
}

// ⚡ Entrenar en la RTX: antes de empezar, la misma ventana de indicaciones (en vez del aviso simple)
async function rtxTrainDialog(sid, name, node) {
  const box = modal(`⚡ Entrenar a ${esc(name)} en ${esc(node.label)}`,
    `<div class="hint">Entrena en la tarjeta rápida y sigue donde quedó su LoRA. <b>Cobra por hora mientras esté prendida.</b></div><div id="rtx-brief"></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="rtx-go">⚡ Empezar</button>`);
  const bp = await briefPanel(sid);
  $("#rtx-brief", box).append(bp.el);
  $("#rtx-go", box).onclick = async (e) => {
    e.target.disabled = true;
    try { await bp.save(); } catch { e.target.disabled = false; return; }
    try { await api(`/api/subjects/${sid}/lora/entrenar-en`, { method: "POST", body: { slot: node.id } }); closeModal(); toast(`${esc(name)}: entrenando en ${esc(node.label)}`, "good", "⚡"); route(); }
    catch (err) { toast(esc(err.message), "bad"); e.target.disabled = false; }
  };
}
