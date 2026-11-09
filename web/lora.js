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
    if (L.status === "training") {
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
  $$("[data-lcancel]").forEach((b) => b.onclick = async () => { if (confirm("¿Cancelar el entrenamiento?")) { await api(`/api/subjects/${b.dataset.lcancel}/lora/cancel`, { method: "POST" }); } });
  $$("[data-lon]").forEach((c) => c.onchange = () => api(`/api/subjects/${c.dataset.lon}/lora/enabled`, { method: "POST", body: { enabled: c.checked } })
    .then(() => toast(c.checked ? "Se usará su LoRA en modo Local" : "Se usarán sus fotos de referencia", "good", "🎓")));
};

async function loraDialog(sid, name) {
  let st;
  try { st = await api(`/api/subjects/${sid}/lora`); } catch (e) { return toast(esc(e.message), "bad"); }
  const enough = st.images >= st.min_images;
  const box = modal(`🎓 Entrenar la LoRA de ${esc(name)}`, `<div class="hint">La GPU local <b>aprende a ${esc(name)} de verdad</b>: después, en modo <b>Local</b>, sus fotos salen con Z-Image Turbo + su LoRA (más rápido y con su cara aprendida, sin depender de la nube).</div>
    <div class="card" style="padding:12px">📸 Fotos disponibles: <b>${st.images}</b> (mínimo ${st.min_images}, ideal 15-${st.max_images}). Se usan las de su ficha y las de los posts <b>aprobados</b> donde aparece sola, hechos con cualquier IA.
      ${enough ? "" : `<div style="color:#fca5a5;margin-top:6px">Faltan fotos: agrega más a su ficha (+ Foto) o aprueba posts donde aparezca.</div>`}</div>
    ${st.trainer_ready ? "" : `<div class="card" style="padding:12px;color:#fcd34d">⚠ ${esc(st.trainer_msg)}</div>`}
    <label class="f">Intensidad<select id="lsteps"><option value="1200">Rápida · ~40 min (pocas fotos)</option><option value="${st.default_steps}" selected>Normal · ~1-1,5 h (recomendada)</option><option value="3000">Fuerte · ~2 h (muchas fotos)</option></select></label>
    <div class="hint">Mientras entrena, las fotos en modo Local esperan su turno (la nube sigue funcionando). Se puede cancelar.</div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="lgo" ${enough && st.trainer_ready ? "" : "disabled"}>Empezar a entrenar</button>`);
  $("#lgo", box).onclick = async () => {
    try { const r = await api(`/api/subjects/${sid}/lora/train`, { method: "POST", body: { steps: +$("#lsteps").value } }); closeModal(); toast(`Entrenando a <b>${esc(name)}</b> con ${r.images} fotos. Lo ves en el Banco y en En vivo.`, "good", "🎓"); route(); }
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
