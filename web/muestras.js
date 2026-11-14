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
    body.innerHTML = `<div class="seg" style="margin-bottom:12px">${models.map((m) => `<button data-mu="${esc(m.id)}" class="${m.id === cur.id ? "on" : ""}">${esc(m.name)}${m.training ? " · 🎓 entrenando" : ""} (${m.count})</button>`).join("")}</div>
      <div class="hint">Toca una foto para verla grande. <b>⭐ Usar esta</b> deja activa la LoRA exacta de ese paso (y el próximo entrenamiento sigue desde ahí). También puedes decirle a Shelton: «usa la foto ${cur.samples[0]?.n || 12} de ${esc(cur.name.replace("🔒 ", ""))}».</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:12px">
        ${cur.samples.map((x) => `<div class="card" style="padding:8px">
          <div style="position:relative"><img src="${esc(x.url)}" loading="lazy" decoding="async" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px;cursor:zoom-in;display:block" data-muzoom="${esc(x.url)}">
            <span style="position:absolute;left:6px;top:6px;padding:3px 8px;border-radius:99px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;font-weight:700">#${String(x.n).padStart(4, "0")} · ${fmtSteps(x.step)} pasos</span></div>
          <div class="row" style="gap:6px;margin-top:6px;flex-wrap:nowrap"><b style="font-size:16px">#${String(x.n).padStart(4, "0")}</b>
            <span class="chip accent" title="Pasos de entrenamiento con los que se hizo">${fmtSteps(x.step)} pasos</span></div>
          <div class="faint small" style="margin:4px 0 6px">${esc((x.at || "").replace("T", " ").slice(0, 16))}</div>
          <button class="btn sm" data-muuse="${x.n}" style="width:100%">⭐ Usar esta</button></div>`).join("")}
      </div>`;
    $$("[data-mu]", body).forEach((b) => b.onclick = () => { localStorage.setItem("mu-model", b.dataset.mu); load(); });
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
