// Shelton Studio — Estudio privado (solo administradores): contenido adulto con modelos del banco INVENTADAS y
// MAYORES DE 18, generado 100% en la placa. No pasa por agentes ni por la nube, no se publica y no se ve en /media.
"use strict";

ICONS.lock = '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>';
(function addPrivadoNav(tries = 0) {
  if (S.authOn === undefined && tries < 20) return setTimeout(() => addPrivadoNav(tries + 1), 300);
  const admin = !S.authOn || S.me?.role === "admin";
  if (!admin || NAV.some((n) => Array.isArray(n) && n[0] === "privado")) return;
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "banco");
  NAV.splice(i >= 0 ? i + 1 : NAV.length, 0, ["privado", "lock", "Adult Zone"]);
  renderRail(); markNav();
  if (S.wl && S.wl.product_name) applyWL();
})();

let privTimer = null;
VIEWS.privado = async (el) => {
  let st;
  try { st = await api("/api/privado/estado"); } catch (e) { el.innerHTML = `<div class="card">${empty("🔒", esc(e.message))}</div>`; return; }
  const models = st.models || [];
  const opt = (m) => `<option value="${esc(m.id)}" ${m.usable ? "" : "disabled"}>${esc(m.name)}${m.usable ? (m.lora_ready ? ` · LoRA ${m.base.toUpperCase()}` : " · sin LoRA") : " · falta confirmar"}</option>`;
  el.innerHTML = pageHead("🔞", "Shelton Studio · Adult Zone", "Contenido adulto con las modelos del banco. Todo se genera en la placa: no pasa por la nube, no se publica y solo lo ven los administradores.") + `
    <div class="card pv-rules"><b>Reglas:</b> solo modelos <b>inventadas</b> (no personas reales) y <b>mayores de 18</b>. Cualquier pedido que mencione menores se bloquea.</div>
    <div class="card pv-chat" style="margin-top:14px">
      <div id="pv-msgs" class="pv-msgs"><div class="pv-msg bot">Hola, soy Shelton en la Adult Zone. Pídeme como siempre, por ejemplo: <i>«3 fotos de ${esc((models.find((m) => m.usable) || {}).name || "Luna")} en un loft, luz de ventana, formato historia»</i>. Si nombras a una modelo confirmada, uso su LoRA.</div></div>
      <form id="pv-form" class="row" style="gap:8px;margin-top:10px"><input id="pv-text" class="grow" placeholder="Escríbele a Shelton…" autocomplete="off"><button class="btn primary">Enviar</button></form>
    </div>
    <details class="card" style="margin-top:14px"><summary style="cursor:pointer"><b>Opciones avanzadas</b> <span class="faint small">(modelos, motor, checkpoint, cantidad, formato)</span></summary>
    <div class="grid g2" style="margin-top:14px;align-items:start">
      <div class="card">
        <b>Modelos del banco</b><div class="faint small" style="margin-bottom:8px">Antes de usar una modelo acá, un administrador confirma que es inventada y mayor de 18.</div>
        <div class="pv-models">${models.map((m) => `<div class="row" style="gap:8px;padding:6px 0;border-bottom:1px solid var(--line)">
          <b class="grow">${esc(m.name)}</b>
          ${m.lora_ready ? `<span class="chip">${esc(m.base.toUpperCase())}</span>` : ""}
          ${m.usable ? '<span class="chip good">Confirmada</span>' : m.confirmed ? `<span class="chip bad" title="${esc(m.why)}">Bloqueada</span>`
            : `<button class="btn sm" data-pvok="${esc(m.id)}" data-pvname="${esc(m.name)}">Confirmar +18 e inventada</button>`}</div>`).join("") || empty("👥", "No hay modelos de tipo persona en el banco.")}</div>
        <div class="faint small" style="margin-top:8px">Para que salga <b>con su cara</b>, la modelo necesita una LoRA del mismo motor: en el Banco, «➕ Versión SDXL / Flux» y entrenarla.</div>
      </div>
      <div class="card">
        <b>Generar</b>
        <label class="f">Modelo<select id="pv-sub"><option value="">— Sin modelo del banco —</option>${models.map(opt).join("")}</select></label>
        <div class="row" style="gap:8px">
          <label class="f grow">Motor<select id="pv-pre"><option value="sdxl">SDXL</option><option value="flux">Flux.1 Dev</option></select></label>
          <label class="f grow">Checkpoint (SDXL)<select id="pv-ckpt"><option value="">Por defecto</option>${(st.checkpoints || []).map((c) => `<option>${esc(c)}</option>`).join("")}</select></label>
        </div>
        <label class="f">Pedido<textarea id="pv-prompt" rows="4" placeholder="Describe la escena: pose, lugar, luz, cámara…"></textarea></label>
        <label class="f">Evitar (opcional)<input id="pv-neg" placeholder="Ej: tatuajes, ropa roja…"></label>
        <div class="row" style="gap:8px">
          <label class="f grow">Formato<select id="pv-size"><option value="vertical">Vertical</option><option value="historia">Historia 9:16</option><option value="cuadrado">Cuadrado</option><option value="horizontal">Horizontal</option></select></label>
          <label class="f" style="width:110px">Cantidad<select id="pv-n">${[1, 2, 3, 4].map((n) => `<option>${n}</option>`).join("")}</select></label>
          <label class="f" style="width:110px">Fuerza LoRA<input id="pv-st" type="number" min="0.3" max="1.5" step="0.05" placeholder="auto"></label>
        </div>
        <button class="btn primary block" id="pv-go">Generar en la placa</button>
        <div class="faint small" style="margin-top:6px">Si la GPU está entrenando, espera su turno. Para desnudos rinden mejor checkpoints SDXL como Pony o CyberRealistic.</div>
      </div>
    </div></details>
    <div class="card" style="margin-top:16px"><b>Galería privada</b> <span class="faint small">(solo administradores · no se publica)</span>
      <div class="pv-grid" id="pv-grid"></div></div>`;
  const paintGrid = (list) => {
    const g = $("#pv-grid", el); if (!g) return;
    g.innerHTML = list.length ? list.map((e) => `<div class="pv-item">${e.status === "done"
      ? `<img src="/api/privado/imagen/${esc(e.id)}" loading="lazy" data-pvzoom="${esc(e.id)}">`
      : `<div class="pv-ph">${e.status === "failed" ? `⚠ ${esc(e.error || "falló")}` : '<div class="spinner"></div>Generando…'}</div>`}
      <div class="row small" style="gap:6px;margin-top:4px"><span class="grow faint ellipsis" title="${esc(e.prompt || "")}">${esc(e.subject || "—")} · ${esc((e.preset || "").toUpperCase())}</span>
      ${e.status === "done" ? `<a class="btn sm ghost" href="/api/privado/imagen/${esc(e.id)}" download="privado-${esc(e.id)}.png">${icon("download", 13)}</a>` : ""}
      <button class="btn sm ghost" data-pvdel="${esc(e.id)}">${icon("trash", 13)}</button></div></div>`).join("") : `<div class="faint small" style="padding:10px 0">Todavía no hay imágenes.</div>`;
    $$("[data-pvzoom]", g).forEach((im) => im.onclick = () => window.open(im.src, "_blank"));
    $$("[data-pvdel]", g).forEach((b) => b.onclick = async () => {
      if (!confirm("¿Borrar esta imagen? No se puede recuperar.")) return;
      await api(`/api/privado/imagen/${b.dataset.pvdel}`, { method: "DELETE" }); refresh();
    });
  };
  const refresh = async () => { try { paintGrid((await api("/api/privado/estado")).entries || []); } catch {} };
  paintGrid(st.entries || []);
  $$("[data-pvok]", el).forEach((b) => b.onclick = async () => {
    if (!confirm(`Confirmo que «${b.dataset.pvname}» es una modelo INVENTADA (no es una persona real) y MAYOR DE 18 años.`)) return;
    try { await api(`/api/privado/modelos/${b.dataset.pvok}/confirmar`, { method: "POST", body: { inventada: true, mayor: true } }); route(); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
  $("#pv-go", el).onclick = async () => {
    try {
      const r = await api("/api/privado/generar", { method: "POST", body: {
        subject_id: $("#pv-sub", el).value, preset: $("#pv-pre", el).value, ckpt: $("#pv-ckpt", el).value,
        prompt: $("#pv-prompt", el).value, negative: $("#pv-neg", el).value, count: +$("#pv-n", el).value,
        size: $("#pv-size", el).value, strength: +$("#pv-st", el).value || null } });
      toast(`Generando ${r.count} en la placa${r.with_lora ? " con su LoRA" : ""}.${r.note ? " " + esc(r.note) : ""}`, r.note ? "warn" : "good", "🔒");
      refresh();
    } catch (e) { toast(esc(e.message), "bad"); }
  };
  const say = (who, html) => { const m = document.createElement("div"); m.className = `pv-msg ${who}`; m.innerHTML = html; $("#pv-msgs", el).append(m); $("#pv-msgs", el).scrollTop = 1e9; };
  $("#pv-form", el).onsubmit = async (ev) => {
    ev.preventDefault();
    const t = $("#pv-text", el).value.trim(); if (!t) return;
    say("me", esc(t)); $("#pv-text", el).value = "";
    try { const r = await api("/api/privado/chat", { method: "POST", body: { text: t } }); say("bot", esc(r.reply)); refresh(); }
    catch (e) { say("bot err", "⚠ " + esc(e.message)); }
  };
  if (privTimer) clearInterval(privTimer);
  privTimer = setInterval(() => { if (!document.getElementById("pv-grid")) { clearInterval(privTimer); privTimer = null; return; } refresh(); }, 5000);
};
