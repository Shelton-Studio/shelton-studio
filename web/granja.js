// Shelton Studio — Granja de GPUs (solo administradores): las tarjetas de la placa y las alquiladas, qué está
// entrenando cada una y cómo conectar una nueva. Los operadores no ven esta pantalla.
"use strict";

ICONS.server = '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>';
(function addGranjaNav(tries = 0) {
  if (S.authOn === undefined && tries < 20) return setTimeout(() => addGranjaNav(tries + 1), 300);
  const admin = !S.authOn || S.me?.role === "admin";
  if (!admin || NAV.some((n) => Array.isArray(n) && n[0] === "granja")) return;
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "consumo");
  NAV.splice(i >= 0 ? i + 1 : NAV.length, 0, ["granja", "server", "Granja de GPUs"]);
  renderRail(); markNav();
  if (S.wl && S.wl.product_name) applyWL();
})();

let granjaTimer = null;
VIEWS.granja = async (el) => {
  el.innerHTML = pageHead("🖥️", "Granja de GPUs", "Todas las tarjetas que trabajan para Shelton: las de la placa y las alquiladas. La fila de entrenamientos se reparte sola: primero la placa, después las alquiladas.",
    `<button class="btn" onclick="go('conexiones')">${icon("plus", 15)} Conectar una tarjeta</button>`) + `<div id="gj-body"><div class="card">${empty("⏳", "Leyendo las tarjetas…")}</div></div>`;
  const load = async () => { try { renderGranja($("#gj-body"), await api("/api/granja")); } catch (e) { const b = $("#gj-body"); if (b) b.innerHTML = `<div class="card">${empty("⚠️", esc(e.message))}</div>`; } };
  await load();
  if (granjaTimer) clearInterval(granjaTimer);
  granjaTimer = setInterval(() => { if (!document.getElementById("gj-body")) { clearInterval(granjaTimer); granjaTimer = null; return; } load(); }, 10000);
};

function gpuLine(g) {
  const mem = g.mem_total_mb ? Math.round(g.mem_used_mb / g.mem_total_mb * 100) : 0;
  return `<div class="cs-meter" style="grid-template-columns:160px 1fr 150px"><span class="ellipsis" title="${esc(g.name)}">${esc(g.name)}</span><div class="cs-track"><i style="width:${mem}%"></i></div>
    <b>${g.mem_total_mb ? (g.mem_used_mb / 1024).toFixed(1) + " / " + (g.mem_total_mb / 1024).toFixed(0) + " GB" : ""}${g.util != null ? " · " + Math.round(g.util) + "%" : ""}</b></div>`;
}

function renderGranja(box, d) {
  if (!box) return;
  const tr = d.training || [];
  box.innerHTML = `
    <div class="card"><b>🎓 Entrenamientos</b>${tr.length ? tr.map((x) => `<div class="tn-row"><span class="tn-name">${esc(x.name)}</span>
      ${x.status === "training" ? `<div class="lora-bar grow"><i style="width:${Math.max(2, x.progress)}%"></i></div><span class="small">${[x.progress + "%", x.where ? (/placa/i.test(x.where) ? "🦎 " : "⚡ ") + esc(x.where) : "", x.eta_min != null ? `~${x.eta_min} min` : ""].filter(Boolean).join(" · ")}</span>`
        : `<span class="chip warn">En fila #${x.position || "?"}</span><span class="faint small grow">${esc(x.phase || "")}</span>`}</div>`).join("") : `<div class="faint small" style="margin-top:6px">Nada entrenando ahora.</div>`}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><div class="row" style="gap:8px"><b>🏠 Placa</b>${d.local.trainer_ready ? '<span class="chip good">entrenador listo</span>' : '<span class="chip warn">sin entrenador</span>'}
        <span class="faint small grow">perfil ${esc(d.profiles[d.local.profile] || d.local.profile)} · ${d.local.slots.length} lugar(es) de entrenamiento</span></div>
        <div style="margin-top:8px">${(d.local.gpus || []).map(gpuLine).join("") || '<div class="faint small">No veo la GPU desde aquí.</div>'}</div>
        ${d.local.trainer_ready ? "" : `<div class="faint small" style="margin-top:6px">${esc(d.local.trainer_msg || "")}</div>`}
        <div class="faint small" style="margin-top:6px">¿Más 3090 en la placa? Agrega <code>LORA_GPUS=0,1,2</code> al .env y una conexión ComfyUI por GPU (campo «GPU de la placa»).</div></div>
      ${(d.nodes || []).map((n) => `<div class="card"><div class="row" style="gap:8px"><b>☁️ ${esc(n.label)}</b>
          ${n.health?.ok ? '<span class="chip good">conectado</span>' : '<span class="chip bad">apagado / sin respuesta</span>'}
          <span class="faint small grow">${esc(d.profiles[n.profile] || n.profile)} · ${n.slots} a la vez${n.cost_hour ? ` · US$ ${n.cost_hour}/h` : ""}</span>
          <button class="btn sm ghost" data-gtest="${esc(n.conn_id)}">Probar</button></div>
        <div style="margin-top:8px">${(n.health?.gpus || []).map(gpuLine).join("") || `<div class="faint small">${esc(n.health?.error || "")}</div>`}</div>
        ${n.health?.ok ? `<div class="faint small" style="margin-top:6px">Libres: ${n.health.free} de ${n.health.slots}${n.health.trainer ? "" : " · ⚠ falta ai-toolkit (corre node_setup.sh)"}${(n.health.running || []).length ? " · entrenando: " + n.health.running.map((r) => esc(r.name) + (r.total ? ` (${r.step}/${r.total})` : "")).join(", ") : ""}</div>` : ""}</div>`).join("")}
    </div>
    <details class="card" style="margin-top:16px"><summary style="cursor:pointer"><b>Cómo conectar una tarjeta alquilada (RunPod / Vast)</b></summary><div class="small" style="line-height:1.8;margin-top:8px">
      1. Alquila la tarjeta (recomendado: RunPod · Secure Cloud · RTX PRO 6000) con un <b>Network Volume</b> en <code>/workspace</code>.<br>
      2. En Tailscale crea una clave: Settings → Keys → <i>Generate auth key</i> (reutilizable).<br>
      3. En la terminal de la tarjeta (root):<br>
      <code>export WORKER_TOKEN="inventa-una-clave-larga"</code><br>
      <code>export TS_AUTHKEY="tskey-auth-…"</code><br>
      <code>bash &lt;(curl -fsSL https://raw.githubusercontent.com/Shelton-Studio/shelton-studio/main/scripts/node_setup.sh)</code><br>
      Opcional: <code>WORKER_GPUS=…</code> (una PRO 6000 dividida en 3) y <code>NODE_COMFY=1</code> (para que también genere fotos).<br>
      4. Al final muestra la URL. En Shelton: <b>Conexiones → Agregar → «Nodo de GPU (trabajador)»</b> con esa URL, el token, cuántos entrenamientos a la vez y el costo por hora.<br>
      5. Listo: los operadores siguen apretando «Entrenar» como siempre y la fila usa también esta tarjeta. Al terminar, la LoRA se copia sola a la placa y las fotos se borran de la tarjeta alquilada.<br>
      <b>Importante:</b> al terminar de usarla, apágala en RunPod (cobra mientras está prendida). Guía completa: <code>docs/GRANJA_GPU.md</code>.</div></details>`;
  $$("[data-gtest]", box).forEach((b) => b.onclick = async () => {
    try { const h = await api(`/api/granja/nodos/${b.dataset.gtest}/probar`, { method: "POST" }); toast(h.ok ? "El nodo responde bien" : esc(h.error || "No responde"), h.ok ? "good" : "bad", "🖥️"); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
}
