// Shelton Studio — LoRA propia de cada modelo del banco: entrenar en la GPU local y ver el avance.
"use strict";

const _bancoLora = VIEWS.banco;
VIEWS.banco = async (el) => {
  await _bancoLora(el);
  let subs = [];
  try { subs = await api("/api/subjects"); } catch { return; }
  const byId = Object.fromEntries(subs.map((s) => [s.id, s]));
  $$(".subject-card").forEach((card) => {
    const sid = $("[data-del]", card)?.dataset.del, s = byId[sid];
    if (!s || $(".lora-box", card)) return;
    const L = s.lora || {}, info = L.info || {};
    const box = document.createElement("div"); box.className = "lora-box";
    if (L.status === "queued") {
      box.innerHTML = `<div class="row" style="gap:8px"><span class="chip warn">⏳ En fila${info.position ? ` #${info.position}` : ""}</span>
        <span class="faint small">Entrena solo cuando termine el anterior (la GPU entrena uno a la vez)</span></div>
        <button class="btn sm ghost" data-lcancel="${sid}">Sacar de la fila</button>`;
    } else if (L.status === "training") {
      box.innerHTML = `<div class="row" style="gap:8px"><span class="chip accent">🎓 Aprendiendo… ${Math.round(L.progress || 0)}%</span>
        <span class="faint small">${esc(info.phase || "")}${info.eta_min != null ? ` · faltan ~${info.eta_min} min` : ""}</span></div>
        <div class="lora-bar"><i style="width:${Math.max(2, L.progress || 0)}%"></i></div>
        <button class="btn sm ghost" data-lcancel="${sid}">Cancelar</button>`;
    } else if (L.status === "ready") {
      box.innerHTML = `<div class="row" style="gap:8px"><span class="chip good">🎓 LoRA lista</span>
        <label class="small"><input type="checkbox" data-lon="${sid}" ${L.enabled ? "checked" : ""}> Usar en modo Local</label>
        <span class="faint small">${info.images || "?"} fotos · ${info.steps || "?"} pasos${info.minutes ? ` · ${info.minutes} min` : ""}</span></div>
        <button class="btn sm ghost" data-ltrain="${sid}" data-lname="${esc(s.name)}">Volver a entrenar</button>`;
    } else {
      const bad = L.status === "failed" || L.status === "canceled";
      box.innerHTML = `${bad ? `<div class="small" style="color:#fca5a5">🎓 ${L.status === "canceled" ? "Cancelado" : "No se pudo entrenar"}: ${esc(info.error || "")}</div>` : ""}
        <button class="btn sm" data-ltrain="${sid}" data-lname="${esc(s.name)}" title="La GPU local aprende a este modelo de verdad">🎓 Entrenar LoRA</button>`;
    }
    $(".body", card)?.append(box);
  });
  $$("[data-ltrain]").forEach((b) => b.onclick = () => loraDialog(b.dataset.ltrain, b.dataset.lname));
  $$("[data-lcancel]").forEach((b) => b.onclick = async () => { if (confirm("¿Cancelar? (si está en la fila, solo sale de la fila)")) { await api(`/api/subjects/${b.dataset.lcancel}/lora/cancel`, { method: "POST" }); route(); } });
  $$("[data-lon]").forEach((c) => c.onchange = () => api(`/api/subjects/${c.dataset.lon}/lora/enabled`, { method: "POST", body: { enabled: c.checked } })
    .then(() => toast(c.checked ? "Se usará su LoRA en modo Local" : "Se usarán sus fotos de referencia", "good", "🎓")));
};

async function loraDialog(sid, name) {
  let st;
  try { st = await api(`/api/subjects/${sid}/lora`); } catch (e) { return toast(esc(e.message), "bad"); }
  const enough = st.images >= st.min_images, has = st.saved_step > 0;
  const box = modal(`🎓 Entrenar la LoRA de ${esc(name)}`, `<div class="hint">La GPU local <b>aprende a ${esc(name)} de verdad</b>: después, en modo <b>Local</b>, sus fotos salen con Z-Image Turbo + su LoRA.</div>
    <div class="card" style="padding:12px">📸 Fotos disponibles: <b>${st.images}</b> (mínimo ${st.min_images}; estudia hasta ${st.max_images} por sesión).
      ${has && st.images_new ? `<div style="margin-top:6px">📚 <b>${st.images_new} fotos nuevas</b> que todavía no estudió: entran en la próxima sesión.</div>` : ""}
      ${has ? `<div style="margin-top:6px">🧠 Ya aprendió <b>${st.saved_step} pasos</b>. Velocidad medida en esta placa: <b>${st.sec_per_step} s por paso</b>.</div>` : ""}
      ${enough ? "" : `<div style="color:#fca5a5;margin-top:6px">Faltan fotos: agrega más a su ficha (+ Foto) o aprueba posts donde aparezca.</div>`}</div>
    ${st.trainer_ready ? "" : `<div class="card" style="padding:12px;color:#fcd34d">⚠ ${esc(st.trainer_msg)}</div>`}
    ${has ? `<div class="seg" id="lmode"><button data-m="continue" class="on">➕ Seguir entrenando (suma a lo aprendido)</button><button data-m="restart">↺ Empezar de cero</button></div>` : ""}
    <label class="f">¿Cuánto más?<select id="lamount">
      <option value="m:30">30 minutos</option><option value="m:60" ${has ? "selected" : ""}>1 hora</option><option value="m:120">2 horas</option>
      <option value="m:240">4 horas</option><option value="m:360">6 horas</option>
      <option value="s:${st.default_steps}" ${has ? "" : "selected"}>${st.default_steps} pasos (normal para empezar)</option><option value="s:custom">Otra cantidad de pasos…</option></select></label>
    <input id="lcustom" type="number" min="100" max="${st.max_session_steps}" placeholder="pasos" style="display:none">
    <div class="card" style="padding:12px" id="lplan">Calculando…</div>
    <div class="hint">Más pasos no siempre es mejor: si se pasa, la LoRA "copia" demasiado las fotos. Prueben fotos entre sesiones.</div>
    ${(st.versions || []).length ? `<details class="card" style="padding:12px"><summary style="cursor:pointer">🗂️ Respaldos de la misma LoRA (${st.versions.length}) · nunca se borran</summary>
      <div class="grid" style="gap:6px;margin-top:8px">${st.versions.map((v) => `<div class="row" style="gap:8px"><span class="grow small mono ellipsis">${esc(v.file)}</span>
        <span class="faint small">${v.steps ? v.steps + " pasos" : ""}</span>${(st.info || {}).active_version === v.file ? '<span class="chip good">En uso</span>' : `<button class="btn sm" data-lver="${esc(v.file)}">Usar esta</button>`}</div>`).join("")}</div></details>` : ""}`,
    `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="lgo" ${enough && st.trainer_ready ? "" : "disabled"}>Empezar</button>`);
  let mode = has ? "continue" : "restart";
  const params = () => {
    const v = $("#lamount", box).value;
    if (v === "s:custom") return { steps: +$("#lcustom", box).value || null };
    return v.startsWith("m:") ? { minutes: +v.slice(2) } : { steps: +v.slice(2) };
  };
  const refresh = async () => {
    $("#lcustom", box).style.display = $("#lamount", box).value === "s:custom" ? "" : "none";
    const q = new URLSearchParams({ mode, ...Object.fromEntries(Object.entries(params()).filter(([, x]) => x)) });
    try {
      const p = await api(`/api/subjects/${sid}/lora/plan?${q}`);
      $("#lplan", box).innerHTML = p.mode === "continue"
        ? `Sigue desde el paso <b>${p.from_step}</b>, suma <b>${p.add_steps}</b> pasos → total <b>${p.total_steps}</b>. Tiempo estimado: <b>~${p.eta_min} min</b>${p.measured ? "" : " (estimado; se ajusta con la velocidad real)"}.`
        : `Empieza de cero con <b>${p.add_steps}</b> pasos. Tiempo estimado: <b>~${p.eta_min} min</b>${p.measured ? "" : " (estimado)"}.${has ? " Lo aprendido antes se guarda aparte, no se borra." : ""}`;
    } catch (e) { $("#lplan", box).textContent = e.message; }
  };
  $$("#lmode button", box).forEach((btn) => btn.onclick = () => { mode = btn.dataset.m; $$("#lmode button", box).forEach((x) => x.classList.toggle("on", x === btn)); refresh(); });
  $("#lamount", box).onchange = refresh; $("#lcustom", box).oninput = refresh;
  $$("[data-lver]", box).forEach((b) => b.onclick = async () => {
    try { await api(`/api/subjects/${sid}/lora/version`, { method: "POST", body: { file: b.dataset.lver } }); closeModal(); toast("Listo: se usa esa versión y el próximo «seguir entrenando» sigue desde ahí. Las demás siguen guardadas.", "good", "🗂️"); route(); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
  refresh();
  $("#lgo", box).onclick = async () => {
    try {
      const r = await api(`/api/subjects/${sid}/lora/train`, { method: "POST", body: { mode, ...params() } }); closeModal();
      const what = r.mode === "continue" ? `sigue desde el paso ${r.from_step} (+${r.add_steps}, ~${r.eta_min} min)` : `${r.add_steps} pasos (~${r.eta_min} min)`;
      toast(r.queued ? `<b>${esc(name)}</b> quedó en la fila (#${r.position}): ${what}.` : `Entrenando a <b>${esc(name)}</b>: ${what}.`, "good", "🎓"); route();
    }
    catch (e) { toast(esc(e.message), "bad"); }
  };
}

// Aviso cuando termina
const _extraLora = window.onExtraEvent;
window.onExtraEvent = (ev) => {
  if (ev.type === "lora_ready") {
    const msg = `🎓 <b>${esc(ev.name)}</b> ya tiene su LoRA (${ev.minutes} min). En modo Local sus fotos salen con su cara aprendida.`;
    toast(msg, "good", "🎓", `<button class="btn sm" onclick="go('banco')">Ver</button>`); note(msg, "good", "banco");
    return;
  }
  if (_extraLora) _extraLora(ev);
};
