// Shelton Studio — Estudio privado (solo administradores): contenido adulto con modelos del banco INVENTADAS y
// MAYORES DE 18, generado 100% en la placa. No pasa por agentes ni por la nube, no se publica y no se ve en /media.
"use strict";

ICONS.lock = '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>';
(function addPrivadoNav(tries = 0) {
  if (S.authOn === undefined && tries < 20) return setTimeout(() => addPrivadoNav(tries + 1), 300);
  const admin = !S.authOn || S.me?.role === "admin";
  if (!admin || NAV.some((n) => Array.isArray(n) && n[0] === "privado")) return;
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "banco");
  NAV.splice(i >= 0 ? i + 1 : NAV.length, 0, ["privado", "lock", "Shelton Hot"]);
  renderRail(); markNav();
  if (S.wl && S.wl.product_name) applyWL();
})();

let privTimer = null;
VIEWS.privado = async (el) => {
  let st;
  try { st = await api("/api/privado/estado"); } catch (e) { el.innerHTML = `<div class="card">${empty("🔒", esc(e.message))}</div>`; return; }
  const models = st.models || [];
  const opt = (m) => `<option value="${esc(m.id)}" ${m.usable ? "" : "disabled"}>${esc(m.name)}${m.usable ? ((m.lora_ready ? ` · cara ${m.base.toUpperCase()}` : " · sin LoRA") + (m.adult && m.adult.ready ? " + 🔞 adulta" : "")) : " · falta confirmar"}</option>`;
  el.innerHTML = pageHead("🔞", "Shelton Studio · Shelton Hot", "Contenido adulto con las modelos del banco. Todo se genera en la placa: no pasa por la nube, no se publica y solo lo ven los administradores.") + `
    <div class="card pv-rules"><b>Reglas:</b> solo modelos <b>inventadas</b> (no personas reales) y <b>mayores de 18</b>. Cualquier pedido que mencione menores se bloquea.</div>
    <div class="card pv-chat" style="margin-top:14px">
      <div class="row" style="gap:8px;margin-bottom:8px"><select id="pv-thread" class="grow" title="Conversaciones de Shelton Hot"></select>
        <button class="btn sm" id="pv-thread-new" title="Empezar una conversación nueva, de cero">➕ Nueva</button>
        <button class="btn sm ghost" id="pv-thread-del" title="Borrar esta conversación (las imágenes quedan en la galería)">🗑</button></div>
      <div id="pv-msgs" class="pv-msgs"><div class="pv-msg bot">Hola, soy Shelton en Shelton Hot. Pídeme como siempre, por ejemplo: <i>«3 fotos de ${esc((models.find((m) => m.usable) || {}).name || "Luna")} en un loft, luz de ventana, formato historia»</i>. Si nombras a una modelo confirmada, uso su LoRA.</div></div>
      <form id="pv-form" class="row" style="gap:8px;margin-top:10px"><label class="btn" title="Subir una imagen hecha con IA para ponerle la cara o el cuerpo de una modelo (también Ctrl+V o arrastrar)">📎 Subir imagen<input type="file" id="pv-chatup" accept="image/*" hidden></label><select id="pv-chatsub" style="width:auto;max-width:180px" title="Modelo para este pedido (si no la nombras en el mensaje)"><option value="">Modelo: la que nombre</option>${models.filter((m) => m.usable && !m.variant_of && !m.adult_twin).map((m) => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("")}</select><select id="pv-chatckpt" style="width:auto;max-width:200px" title="Checkpoint SDXL / Pony para este pedido"><option value="">Checkpoint: el mejor</option>${(st.checkpoints || []).map((c) => `<option>${esc(c)}</option>`).join("")}</select><input id="pv-text" class="grow" placeholder="Escríbele a Shelton… (o sube 📎 una imagen IA y dile «ponle la cara»)" autocomplete="off"><button class="btn primary">Enviar</button></form>
    </div>
    <details class="card" style="margin-top:14px"><summary style="cursor:pointer"><b>Opciones avanzadas</b> <span class="faint small">(modelos, motor, checkpoint, cantidad, formato)</span></summary>
    <div class="grid g2" style="margin-top:14px;align-items:start">
      <div class="card">
        <b>Modelos del banco</b><div class="faint small" style="margin-bottom:8px">Antes de usar una modelo acá, un administrador confirma que es inventada y mayor de 18.</div>
        <div class="pv-models">${models.filter((m) => !m.variant_of && !m.adult_twin).map((m) => `<div class="row" style="gap:8px;padding:6px 0;border-bottom:1px solid var(--line);flex-wrap:wrap">
          <b class="grow">${esc(m.name)}</b>
          ${m.lora_ready ? `<span class="chip" title="Su LoRA de siempre (la cara)">${esc(m.base.toUpperCase())}</span>` : ""}
          ${m.usable ? `<button class="btn sm ghost" data-pvphotos="${esc(m.id)}" data-pvname="${esc(m.name)}" title="Sus fotos privadas: súbelas aquí para que su LoRA adulta aprenda de ellas">📷 Sus fotos (${m.private_photos || 0})${m.new_photos ? ` · ${m.new_photos} nuevas` : ""}</button>` : ""}
          ${!m.usable ? "" : m.adult && ["training", "queued"].includes(m.adult.status) ? `<span class="chip warn">🔞 LoRA adulta: ${m.adult.status === "queued" ? "en fila" : `aprendiendo ${m.adult.progress}%`}${m.adult.where ? (/placa|^local/i.test(m.adult.where) ? " · 🦎 placa" : ` · ⚡ ${esc(m.adult.where)}`) : ""}</span><button class="btn sm ghost" data-pvadultx="${esc(m.id)}" title="Cancelar (lo ya aprendido queda guardado)">✕ Cancelar</button>`
            : m.adult && m.adult.ready ? `<span class="chip good" title="Su LoRA adulta: se usa sola al nombrarla. Vive solo aquí">🔞 LoRA adulta lista</span><button class="btn sm ghost" data-pvadult="${esc(m.id)}" title="Seguir entrenando (suma sobre lo aprendido)">🎓 Seguir</button>`
            : `<button class="btn sm" data-pvadult="${esc(m.id)}" title="Entrena su LoRA adulta con sus fotos y sus fotos privadas (en la RTX si está prendida; si no, en la placa). Queda solo en Shelton Hot; su LoRA normal no se toca">🎓 Entrenar LoRA adulta</button>`}
          ${m.usable ? '<span class="chip good">Confirmada</span>' : m.confirmed ? `<span class="chip bad" title="${esc(m.why)}">Bloqueada</span>`
            : `<button class="btn sm" data-pvok="${esc(m.id)}" data-pvname="${esc(m.name)}">Confirmar +18 e inventada</button>`}</div>`).join("") || empty("👥", "No hay modelos de tipo persona en el banco.")}</div>
        <div class="faint small" style="margin-top:8px">Igual que en el Shelton normal: <b>nombra a la modelo</b> y listo. Si tiene su <b>🔞 LoRA adulta</b>, se usa sola (su cuerpo + el estilo de Shelton Hot) y después, <b>automático</b>, una segunda pasada con <b>Z-Image + su LoRA de siempre</b> le pone <b>su cara tal cual</b>. Si todavía no: <b>1)</b> toca <b>📷 Sus fotos</b> y sube las fotos que quieras que aprenda, <b>2)</b> toca <b>🎓 Entrenar LoRA adulta</b>. Después, <b>cada 5 fotos nuevas que le subas, sigue aprendiendo sola</b> (suma a lo que ya sabe, nunca empieza de cero). Se entrena como siempre (en la RTX si está prendida, si no en la placa; en la RTX sus fotos se borran al terminar) con sus fotos + sus 🔞 fotos privadas, y <b>queda solo en Shelton Hot</b>: no aparece en el Banco ni en el Shelton normal, y su LoRA normal no se toca.</div>
      </div>
      <div class="card">
        <b>Generar</b>
        <label class="f">Modelo<select id="pv-sub"><option value="">— Sin modelo del banco —</option>${models.filter((m) => !m.adult_twin).map(opt).join("")}</select></label>
        <div class="card" style="padding:10px;margin:6px 0">
          <div class="row" style="gap:8px"><b class="small grow">Imagen base (opcional · hecha con IA)</b>
            <label class="btn sm">📎 Subir imagen<input type="file" id="pv-base" accept="image/*" hidden></label>
            <button class="btn sm ghost" id="pv-base-x" style="display:none">Quitar</button></div>
          <div id="pv-base-show" class="faint small" style="margin-top:6px">Si subes una, «Generar» le pone a esa imagen la cara (o la cara y el cuerpo) de la modelo elegida.</div>
          <div class="row" id="pv-base-opts" style="gap:8px;margin-top:6px;display:none">
            <label class="f grow">Qué hacer<select id="pv-base-mode"><option value="cara">Solo la cara (todo lo demás igual)</option><option value="cuerpo">Cara y cuerpo</option></select></label>
            <label class="f grow">Cambio de cuerpo<select id="pv-base-lvl"><option value="suave">Suave</option><option value="medio">Medio</option><option value="fuerte">Fuerte</option></select></label></div>
        </div>
        <div class="row" style="gap:8px">
          <label class="f grow">Motor<select id="pv-pre"><option value="sdxl">SDXL</option><option value="flux">Flux.1 Dev</option><option value="zimage">Z-Image (LoRA del Banco)</option></select></label>
          <label class="f grow">Checkpoint (SDXL)<select id="pv-ckpt"><option value="">Por defecto</option>${(st.checkpoints || []).map((c) => `<option>${esc(c)}</option>`).join("")}</select></label>
        </div>
        <div class="pv-tr">
          <label class="f">Pedido <span class="faint small">(en español)</span><textarea id="pv-prompt" rows="5" placeholder="Describe la escena: pose, lugar, luz, cámara…"></textarea></label>
          <label class="f" id="pv-en-box">En inglés <span class="faint small" id="pv-en-how">(lo que recibe SDXL · puedes corregirlo)</span><textarea id="pv-en" rows="5" placeholder="Aquí aparece la traducción mientras escribes"></textarea></label>
        </div>
        <label class="f">Evitar (opcional)<input id="pv-neg" placeholder="Ej: tatuajes, ropa roja…"><span class="faint small" id="pv-neg-en"></span></label>
        <div class="row" style="gap:8px">
          <label class="f grow">Formato<select id="pv-size"><option value="vertical">Vertical</option><option value="historia">Historia 9:16</option><option value="cuadrado">Cuadrado</option><option value="horizontal">Horizontal</option></select></label>
          <label class="f" style="width:110px">Cantidad<select id="pv-n">${[1, 2, 3, 4].map((n) => `<option>${n}</option>`).join("")}</select></label>
          <label class="f" style="width:110px">Fuerza LoRA<input id="pv-st" type="number" min="0.3" max="1.5" step="0.05" placeholder="auto"></label>
        </div>
        <button class="btn primary block" id="pv-go">Generar en la placa</button>
        <div class="faint small" style="margin-top:6px">Si la GPU está entrenando, espera su turno. Para desnudos rinden mejor checkpoints SDXL como Pony o CyberRealistic.</div>
      </div>
    </div></details>
    <div class="card" id="pv-style" style="margin-top:16px"></div>
    <div class="card" style="margin-top:16px"><div class="row" style="gap:8px;flex-wrap:wrap"><b>Galería privada</b> <span class="faint small grow">(solo administradores · no se publica)</span>
      <label class="btn sm" title="Solo imágenes hechas con IA, de personas inventadas y mayores de 18">⬆ Subir imagen IA<input type="file" id="pv-aiup" accept="image/*" multiple hidden></label>
      <select id="pv-filter" style="width:auto"><option value="">Todas</option><option value="pending">Por revisar</option><option value="approved">✔ Aprobadas</option><option value="rejected">✖ Rechazadas</option></select></div>
      <div class="pv-grid" id="pv-grid"></div></div>`;
  let lastList = st.entries || [];
  const paintGrid = (all) => {
    const g = $("#pv-grid", el); if (!g) return;
    lastList = all;
    const f = $("#pv-filter", el)?.value || "";
    const list = !f ? all : all.filter((e) => f === "pending" ? e.status === "done" && !e.review : e.review === f);
    g.innerHTML = list.length ? list.map((e) => `<div class="pv-item">${e.status === "done"
      ? `<img src="/api/privado/imagen/${esc(e.id)}?t=1" loading="lazy" decoding="async" data-pvzoom="${esc(e.id)}">`
      : `<div class="pv-ph">${e.status === "failed" ? `⚠ ${esc(e.error || "falló")}` : `<div><div class="spinner"></div>${e.status === "waiting" ? esc(e.note || "Esperando la GPU…") : e.status === "queued" ? esc(e.note || "En fila…") : "Generando…"}</div>`}</div>`}
      ${e.status === "done" ? `<div class="row small" style="gap:6px;margin-top:6px">
        ${e.review === "approved" ? '<span class="chip good">✔ Aprobada</span>' : e.review === "rejected" ? '<span class="chip bad">✖ Rechazada</span>' : '<span class="chip">Por revisar</span>'}
        <span class="grow"></span>
        <button class="btn sm ${e.review === "approved" ? "" : "ghost"}" data-pvrev="${esc(e.id)}" data-ok="${e.review === "approved" ? "" : "1"}" title="Aprobar">✔</button>
        <button class="btn sm ${e.review === "rejected" ? "" : "ghost"}" data-pvrev="${esc(e.id)}" data-ok="${e.review === "rejected" ? "" : "0"}" title="Rechazar">✖</button></div>` : ""}
      <div class="row small" style="gap:6px;margin-top:4px"><span class="grow faint ellipsis" title="${esc(e.prompt || "")}">${esc(e.subject || "—")} · ${esc((e.preset || "").toUpperCase())}</span>
      ${e.status === "done" ? `<a class="btn sm ghost" href="/api/privado/imagen/${esc(e.id)}" download="privado-${esc(e.id)}.png">${icon("download", 13)}</a>` : ""}
      ${e.status === "done" && e.subject_id ? `<button class="btn sm ghost" data-pvtoref="${esc(e.id)}" data-pvsid="${esc(e.subject_id)}" title="Guardarla en sus fotos privadas (Banco)">➕</button>` : ""}
      ${e.status === "done" ? `<button class="btn sm ghost" data-pvremake="${esc(e.id)}" title="🔁 Rehacer con una modelo (su cara, o su cara y su cuerpo)">🔁</button>` : ""}
      <button class="btn sm ghost" data-pvdel="${esc(e.id)}">${icon("trash", 13)}</button></div></div>`).join("") : `<div class="faint small" style="padding:10px 0">Todavía no hay imágenes.</div>`;
    $$("[data-pvzoom]", g).forEach((im) => im.onclick = () => window.open(`/api/privado/imagen/${im.dataset.pvzoom}`, "_blank"));
    $$("[data-pvrev]", g).forEach((b) => b.onclick = async () => {
      const ok = b.dataset.ok === "1" ? true : b.dataset.ok === "0" ? false : null;
      try { await api(`/api/privado/imagen/${b.dataset.pvrev}/revisar`, { method: "POST", body: { ok } }); refresh(); }
      catch (e) { toast(esc(e.message), "bad"); }
    });
    $$("[data-pvtoref]", g).forEach((b) => b.onclick = async () => {
      try { await api(`/api/privado/modelo/${b.dataset.pvsid}/fotos`, { method: "POST", body: { entry_id: b.dataset.pvtoref } }); toast("Guardada en sus fotos privadas (Banco)", "good", "🔞"); }
      catch (e) { toast(esc(e.message), "bad"); }
    });
    $$("[data-pvremake]", g).forEach((b) => b.onclick = () => remakeDialog(b.dataset.pvremake));
    $$("[data-pvdel]", g).forEach((b) => b.onclick = async () => {
      if (!confirm(b.closest(".pv-item").querySelector("img") ? "¿Borrar esta imagen? No se puede recuperar." : "¿Cancelar esta foto?")) return;
      await api(`/api/privado/imagen/${b.dataset.pvdel}`, { method: "DELETE" }); refresh();
    });
  };
  const remakeDialog = (id) => {
    const usable = models.filter((m) => m.usable && !m.variant_of && !m.adult_twin);
    if (!usable.length) return toast("Primero confirma una modelo (inventada y +18) en Opciones avanzadas", "warn");
    const box = modal("🔁 Rehacer con una modelo", `
      <label class="f">Modelo<select id="rh-sub">${usable.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}${m.adult && m.adult.ready ? " · 🔞 LoRA adulta" : ""}</option>`).join("")}</select></label>
      <div class="seg" id="rh-mode" style="margin:8px 0"><button data-m="cara" class="on">Solo la cara (todo lo demás igual)</button><button data-m="cuerpo">Cara y cuerpo</button></div>
      <label class="f" id="rh-ckpt-l" style="display:none">Checkpoint para el cuerpo (SDXL / Pony…)<select id="rh-ckpt"><option value="">El mejor que hay</option>${(st.checkpoints || []).map((c) => `<option>${esc(c)}</option>`).join("")}</select></label>
      <label class="f" id="rh-lvl-l" style="display:none">Cuánto cambia la imagen al poner su cuerpo<select id="rh-lvl">
        <option value="suave">Suave · la misma imagen, solo su figura</option><option value="medio">Medio</option><option value="fuerte">Fuerte · más ella, puede cambiar detalles</option></select></label>
      <div class="hint">La cara sale de su LoRA de siempre. El cuerpo, de su 🔞 LoRA adulta (si la tiene) o de su LoRA de siempre.</div>`,
      `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="rh-go">🔁 Rehacer</button>`);
    let mode = "cara";
    $$("#rh-mode button", box).forEach((b) => b.onclick = () => { mode = b.dataset.m; $$("#rh-mode button", box).forEach((x) => x.classList.toggle("on", x === b)); $("#rh-lvl-l", box).style.display = mode === "cuerpo" ? "" : "none"; $("#rh-ckpt-l", box).style.display = mode === "cuerpo" ? "" : "none"; });
    $("#rh-go", box).onclick = async () => {
      try { await api(`/api/privado/imagen/${id}/rehacer`, { method: "POST", body: { subject_id: $("#rh-sub", box).value, mode, level: $("#rh-lvl", box).value, ckpt: $("#rh-ckpt", box).value } }); closeModal(); toast("Rehaciendo en la placa…", "good", "🔁"); refresh(); }
      catch (e) { toast(esc(e.message), "bad"); }
    };
  };
  $("#pv-aiup", el).onchange = async (ev) => {
    const files = [...ev.target.files]; ev.target.value = "";
    if (!files.length) return;
    if (!confirm("Solo se admiten imágenes HECHAS CON IA.\n\nConfirmo que estas imágenes están generadas con IA, que muestran personas INVENTADAS (no reales) y MAYORES DE 18.\n\n¿Aceptas?")) return;
    const fd = new FormData(); files.forEach((f) => fd.append("files", f)); fd.append("confirm", "1");
    try { const r = await api("/api/privado/rehacer/subir", { method: "POST", body: fd }); toast(`Subidas ${r.ids.length}: toca 🔁 en una para rehacerla con una modelo`, "good", "⬆"); refresh(); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  const refresh = async () => { try { paintGrid((await api("/api/privado/estado")).entries || []); } catch {} };
  paintGrid(st.entries || []);
  $("#pv-filter", el).onchange = () => paintGrid(lastList);
  const paintStyle = async () => {        // 🎓 LoRA propia de Shelton Hot
    const c = $("#pv-style", el); if (!c) return;
    let s; try { s = await api("/api/privado/estilo"); } catch { c.remove(); return; }
    const busy = ["training", "queued"].includes(s.status);
    c.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap"><b class="grow">🎓 LoRA de Shelton Hot</b>
        ${s.ready ? '<span class="chip good">activa en SDXL</span>' : '<span class="chip">todavía sin entrenar</span>'}
        <label class="btn sm" title="Tus propias fotos (como el + Foto del Banco)">⬆ Subir mis fotos<input type="file" id="pv-style-up" accept="image/*" multiple hidden></label>
        <button class="btn sm ${busy ? "" : "primary"}" id="pv-style-go" ${busy || s.approved < s.min ? "disabled" : ""}>${busy ? (s.status === "queued" ? "En fila…" : `Aprendiendo… ${s.progress}%`) : `Entrenar (${s.approved} fotos)`}</button></div>
      ${s.n_uploads ? `<div class="pv-grid" style="margin-top:8px">${s.uploads.map((f) => `<div class="pv-item"><img src="/api/privado/ref/${esc(f)}" loading="lazy"><button class="btn sm ghost" data-stq="${esc(f)}">Quitar</button></div>`).join("")}</div>` : ""}
      <div class="faint small" style="margin-top:6px">${s.n_uploads} subidas por ti + ${Math.max(0, s.approved - s.n_uploads)} aprobadas con ✔ = ${s.approved} (mínimo ${s.min}).</div>
      <div class="faint small" style="margin-top:6px">Aprende el estilo y la anatomía de las fotos que aprobaste con ✔ (mínimo ${s.min}). Se suma sola a cada foto con SDXL, junto con la LoRA de la modelo. Todo en la placa: nunca va a la nube. Cada vez que la entrenas sigue sumando sobre lo aprendido.${busy && s.phase ? ` · ${esc(s.phase)}` : ""}</div>`;
    const up = $("#pv-style-up", c);
    if (up) up.onchange = async (ev) => {
      if (!ev.target.files.length) return;
      if (!confirm("Confirmo que estas fotos son imágenes GENERADAS CON IA, de personas INVENTADAS (no personas reales) y MAYORES DE 18.")) { ev.target.value = ""; return; }
      const fd = new FormData(); [...ev.target.files].forEach((f) => fd.append("files", f)); fd.append("confirm", "1");
      try { const r = await api("/api/privado/estilo/subir", { method: "POST", body: fd }); toast(`Subidas: ya hay ${r.count} fotos tuyas`, "good", "⬆"); }
      catch (e) { toast(esc(e.message), "bad"); }
      paintStyle();
    };
    $$("[data-stq]", c).forEach((q) => q.onclick = async () => { await api("/api/privado/estilo/quitar", { method: "POST", body: { file: q.dataset.stq } }); paintStyle(); });
    const b = $("#pv-style-go", c);
    if (b) b.onclick = async () => { try { await api("/api/privado/estilo/entrenar", { method: "POST" }); toast("Entrenando la LoRA de Shelton Hot", "good", "🎓"); paintStyle(); } catch (e) { toast(esc(e.message), "bad"); } };
    if (busy) setTimeout(() => { if (document.body.contains(c)) paintStyle(); }, 15000);
  };
  paintStyle();
  $$("[data-pvphotos]", el).forEach((b) => b.onclick = () => privateDialog(b.dataset.pvphotos, b.dataset.pvname));
  $$("[data-pvadultx]", el).forEach((b) => b.onclick = async () => {
    if (!confirm("¿Cancelar el entrenamiento de su LoRA adulta? Lo que ya aprendió queda guardado.")) return;
    try { await api(`/api/privado/modelo/${b.dataset.pvadultx}/lora-adulta/cancelar`, { method: "POST" }); toast("Entrenamiento cancelado", "good", "✕"); route(); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
  $$("[data-pvadult]", el).forEach((b) => b.onclick = async () => {
    const sid = b.dataset.pvadult, name = (models.find((m) => m.id === sid) || {}).name || "";
    let twin;
    try { twin = (await api(`/api/privado/modelo/${sid}/lora-adulta/preparar`, { method: "POST" })).twin; }
    catch (e) { return toast(esc(e.message), "bad"); }
    const box = modal(`🎓 Entrenar la 🔞 LoRA adulta de ${esc(name)}`,
      `<div class="hint">Aprende con sus fotos y sus 🔞 fotos privadas. Queda solo en Shelton Hot; su LoRA normal no se toca.</div>
       <label class="f">¿Dónde entrenar?<select id="pv-where"><option value="auto">Automático (la RTX si está prendida; si no, la placa)</option><option value="placa">🦎 La placa (3090)</option></select></label>
       <div id="pv-brief"></div>`,
      `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="pv-train-go">🎓 Empezar</button>`);
    api("/api/privado/tarjetas").then((cards) => cards.forEach((c) => $("#pv-where", box)?.insertAdjacentHTML("beforeend", `<option value="${esc(c.id)}">⚡ ${esc(c.label)} (cobra por hora)</option>`))).catch(() => {});
    let bp = null;
    if (typeof briefPanel === "function") { bp = await briefPanel(twin, { photos: false }); /* sus fotos privadas van por «📷 Sus fotos» (nunca a /media) */ $("#pv-brief", box).append(bp.el); }
    $("#pv-train-go", box).onclick = async (ev) => {
      ev.target.disabled = true;
      try { if (bp) await bp.save(); } catch { ev.target.disabled = false; return; }
      try { const r = await api(`/api/privado/modelo/${sid}/lora-adulta`, { method: "POST", body: { where: $("#pv-where", box).value } }); closeModal(); toast(`Entrenando ${esc(r.name)} en ${esc(r.where)}`, "good", "🎓"); route(); }
      catch (e) { toast(esc(e.message), "bad"); ev.target.disabled = false; }
    };
  });
  $$("[data-pvok]", el).forEach((b) => b.onclick = async () => {
    if (!confirm(`Confirmo que «${b.dataset.pvname}» es una modelo INVENTADA (no es una persona real) y MAYOR DE 18 años.`)) return;
    try { await api(`/api/privado/modelos/${b.dataset.pvok}/confirmar`, { method: "POST", body: { inventada: true, mayor: true } }); route(); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
  // Traducción en vivo, como un traductor lado a lado (en la placa, sin nube). Solo para SDXL/Flux.
  let enEdited = false; const trTimer = {}, trSeq = {};
  const needsEn = () => ["sdxl", "flux"].includes($("#pv-pre", el).value);
  const paintEnBox = () => { $("#pv-en-box", el).style.display = needsEn() ? "" : "none"; };
  const translateLive = (src, dst, isNeg) => {
    clearTimeout(trTimer[src]);
    trTimer[src] = setTimeout(async () => {
      const text = $(src, el).value.trim(), seq = trSeq[src] = (trSeq[src] || 0) + 1;
      if (!needsEn()) return;
      if (!text) { if (isNeg) $(dst, el).textContent = ""; else $(dst, el).value = ""; return; }
      if (!isNeg) $("#pv-en-how", el).textContent = "(traduciendo…)";
      try {
        const r = await api("/api/privado/traducir", { method: "POST", body: { text } });
        if (seq !== trSeq[src]) return;
        if (isNeg) $(dst, el).textContent = r.how ? `→ ${r.en}` : "";
        else {
          $(dst, el).value = r.en; enEdited = false;
          $("#pv-en-how", el).textContent = r.how === "glosario" ? `(básica: ${r.status?.why || "el traductor no está listo"})`
            : "(lo que recibe SDXL · puedes corregirlo)";
        }
      } catch { if (!isNeg) $("#pv-en-how", el).textContent = "(no pude traducir)"; }
    }, 600);
  };
  $("#pv-prompt", el).addEventListener("input", () => translateLive("#pv-prompt", "#pv-en", false));
  $("#pv-neg", el).addEventListener("input", () => translateLive("#pv-neg", "#pv-neg-en", true));
  $("#pv-en", el).addEventListener("input", () => { enEdited = true; });
  $("#pv-pre", el).addEventListener("change", () => { paintEnBox(); translateLive("#pv-prompt", "#pv-en", false); });
  paintEnBox();
  let baseId = "";
  const showBase = () => {
    $("#pv-base-x", el).style.display = baseId ? "" : "none";
    $("#pv-base-opts", el).style.display = baseId ? "" : "none";
    $("#pv-base-show", el).innerHTML = baseId ? `<img src="/api/privado/imagen/${esc(baseId)}?t=1" style="max-width:140px;border-radius:10px">`
      : "Si subes una, «Generar» le pone a esa imagen la cara (o la cara y el cuerpo) de la modelo elegida.";
  };
  $("#pv-base", el).onchange = async (ev) => {
    const f = ev.target.files[0]; ev.target.value = "";
    if (!f) return;
    if (!confirm("Solo se admiten imágenes HECHAS CON IA.\n\nConfirmo que esta imagen está generada con IA, que muestra una persona INVENTADA (no real) y MAYOR DE 18.\n\n¿Aceptas?")) return;
    const fd = new FormData(); fd.append("files", f); fd.append("confirm", "1");
    try { const r = await api("/api/privado/rehacer/subir", { method: "POST", body: fd }); baseId = r.ids[0]; showBase(); refresh(); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  $("#pv-base-x", el).onclick = () => { baseId = ""; showBase(); };
  $("#pv-go", el).onclick = async () => {
    if (baseId) {                        // con imagen base: su cara (o cara y cuerpo) sobre esa imagen
      const sid = $("#pv-sub", el).value;
      if (!sid) return toast("Elige la modelo arriba", "warn");
      try { await api(`/api/privado/imagen/${baseId}/rehacer`, { method: "POST", body: { subject_id: sid, mode: $("#pv-base-mode", el).value, level: $("#pv-base-lvl", el).value, ckpt: $("#pv-ckpt", el).value } }); toast("Rehaciendo en la placa…", "good", "🔁"); refresh(); }
      catch (e) { toast(esc(e.message), "bad"); }
      return;
    }
    try {
      const r = await api("/api/privado/generar", { method: "POST", body: {
        subject_id: $("#pv-sub", el).value, preset: $("#pv-pre", el).value, ckpt: $("#pv-ckpt", el).value,
        prompt: (needsEn() && $("#pv-en", el).value.trim()) || $("#pv-prompt", el).value, negative: $("#pv-neg", el).value, count: +$("#pv-n", el).value,
        size: $("#pv-size", el).value, strength: +$("#pv-st", el).value || null } });
      toast(`Generando ${r.count} en la placa${r.with_lora ? " con su LoRA" : ""}.${r.note ? " " + esc(r.note) : ""}`, r.note ? "warn" : "good", "🔒");
      refresh();
    } catch (e) { toast(esc(e.message), "bad"); }
  };
  // 💬 Conversaciones: varias, como en el otro Shelton. Se guardan en la placa (privadas).
  let threadId = localStorage.getItem("pv-thread") || "";
  const welcomeHTML = $("#pv-msgs", el).innerHTML;
  const loadThreads = async (pick) => {
    let list = [];
    try { list = await api("/api/privado/conversaciones"); } catch { return; }
    if (!list.length) { try { const t = await api("/api/privado/conversaciones", { method: "POST" }); list = [t]; } catch { return; } }
    threadId = pick || (list.some((t) => t.id === threadId) ? threadId : list[0].id);
    localStorage.setItem("pv-thread", threadId);
    $("#pv-thread", el).innerHTML = list.map((t) => `<option value="${esc(t.id)}" ${t.id === threadId ? "selected" : ""}>${esc(t.title)} · ${esc((t.updated || "").replace("T", " ").slice(0, 16))}</option>`).join("");
    let t = { messages: [] };
    try { t = await api(`/api/privado/conversaciones/${threadId}`); } catch {}
    $("#pv-msgs", el).innerHTML = welcomeHTML + (t.messages || []).map((m) => `<div class="pv-msg ${esc(m.who)}">${esc(m.text)}</div>`).join("");
    $("#pv-msgs", el).scrollTop = 1e9;
  };
  $("#pv-thread", el).onchange = (ev) => loadThreads(ev.target.value);
  $("#pv-thread-new", el).onclick = async () => { try { const t = await api("/api/privado/conversaciones", { method: "POST" }); loadThreads(t.id); } catch (e) { toast(esc(e.message), "bad"); } };
  $("#pv-thread-del", el).onclick = async () => {
    if (!threadId || !confirm("¿Borrar esta conversación? Las imágenes que salieron de ella quedan en la galería.")) return;
    try { await api(`/api/privado/conversaciones/${threadId}`, { method: "DELETE" }); threadId = ""; loadThreads(); toast("Conversación borrada", "good", "🗑"); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  loadThreads();
  const say = (who, html) => { const m = document.createElement("div"); m.className = `pv-msg ${who}`; m.innerHTML = html; $("#pv-msgs", el).append(m); $("#pv-msgs", el).scrollTop = 1e9; };
  let chatImage = "";
  const chatUpload = async (f) => {
    if (!f || !f.type.startsWith("image/")) return;
    if (!confirm("Solo se admiten imágenes HECHAS CON IA.\n\nConfirmo que esta imagen está generada con IA, que muestra una persona INVENTADA (no real) y MAYOR DE 18.\n\n¿Aceptas?")) return;
    const fd = new FormData(); fd.append("files", f); fd.append("confirm", "1");
    try {
      const r = await api("/api/privado/rehacer/subir", { method: "POST", body: fd });
      chatImage = r.ids[0];
      say("me", `<img src="/api/privado/imagen/${esc(chatImage)}?t=1" style="max-width:160px;border-radius:10px">`);
      say("bot", "Recibida. Dime qué hago: «ponle la cara de Valentina» o «ponle el cuerpo y la cara de Valentina» (suave, medio o fuerte).");
      refresh();
    } catch (e) { say("bot err", "⚠ " + esc(e.message)); }
  };
  $("#pv-chatup", el).onchange = (ev) => { const f = ev.target.files[0]; ev.target.value = ""; chatUpload(f); };
  $("#pv-text", el).addEventListener("paste", (ev) => {           // pegar con Ctrl+V
    const it = [...(ev.clipboardData?.items || [])].find((x) => x.type.startsWith("image/"));
    if (it) { ev.preventDefault(); chatUpload(it.getAsFile()); }
  });
  const chatBox = $(".pv-chat", el);                                 // arrastrar y soltar sobre el chat
  chatBox.addEventListener("dragover", (ev) => { ev.preventDefault(); chatBox.style.outline = "2px dashed var(--accent)"; });
  chatBox.addEventListener("dragleave", () => { chatBox.style.outline = ""; });
  chatBox.addEventListener("drop", (ev) => { ev.preventDefault(); chatBox.style.outline = ""; chatUpload(ev.dataTransfer.files[0]); });
  $("#pv-form", el).onsubmit = async (ev) => {
    ev.preventDefault();
    const t = $("#pv-text", el).value.trim(); if (!t) return;
    say("me", esc(t)); $("#pv-text", el).value = "";
    const pick = $("#pv-chatsub", el).value;
    let send = t;
    if (pick && !t.toLowerCase().includes(pick.toLowerCase())) {   // la modelo elegida arriba, si no la nombró
      send = /\b(cara|rostro|cuerpo)\b/i.test(t) ? `${t} de ${pick}` : `${pick}: ${t}`;
    }
    try { const r = await api("/api/privado/chat", { method: "POST", body: { thread_id: threadId, text: send, image_id: chatImage, ckpt: $("#pv-chatckpt", el).value } }); say("bot", esc(r.reply)); if (r.batch === "rehacer") chatImage = ""; refresh(); }
    catch (e) { say("bot err", "⚠ " + esc(e.message)); }
  };
  if (privTimer) clearInterval(privTimer);
  privTimer = setInterval(() => { if (!document.getElementById("pv-grid")) { clearInterval(privTimer); privTimer = null; return; } refresh(); }, 5000);
};

// ------------------------------------------------------------------ Banco: fotos privadas de cada modelo (solo admin)
const _bancoPriv = VIEWS.banco;
VIEWS.banco = async (el) => {
  await _bancoPriv(el);
  const admin = !S.authOn || S.me?.role === "admin";
  if (!admin) return;
  let subs = [];
  try { subs = await api("/api/subjects"); } catch { return; }
  const byId = Object.fromEntries(subs.map((s) => [s.id, s]));
  $$(".subject-card").forEach((card) => {
    const sid = $("[data-del]", card)?.dataset.del, s = byId[sid];
    if (!s || s.kind !== "person" || $("[data-pvbank]", card)) return;
    const info = s.lora?.info || {}, n = (info.private_refs || []).length;
    const b = document.createElement("button");
    b.className = "btn sm ghost"; b.dataset.pvbank = sid;
    b.textContent = `🔞 Fotos privadas${n ? ` (${n})` : ""}`;
    b.title = "Solo administradores: sus fotos adultas (no se publican ni van a la nube) y usarlas al entrenar su LoRA";
    b.onclick = () => privateDialog(sid, s.name);
    ($(".lora-box", card) || $(".body", card))?.append(b);
  });
};

async function privateDialog(sid, name) {
  let d;
  try { d = await api(`/api/privado/modelo/${sid}`); } catch (e) { return toast(esc(e.message), "bad"); }
  const box = modal(`🔞 Fotos privadas · ${esc(name)}`, d.confirmed ? `
    <div class="hint">Solo administradores. Estas fotos <b>no se publican, no van a /media y nunca se mandan a ninguna IA de la nube</b>. Se guardan en la placa; si entrena en tu RTX, allá se borran apenas termina.</div>
    <div class="hint">🎓 Estas fotos le enseñan <b>solo a su 🔞 LoRA adulta</b> (botón «Entrenar LoRA adulta» en Shelton Hot). Su LoRA normal nunca las ve.</div>
    <div class="row" style="gap:8px"><b class="grow">Sus fotos privadas (${d.refs.length})</b>
      <label class="btn sm">⬆ Subir fotos<input type="file" id="pv-up" accept="image/*" multiple hidden></label></div>
    <div class="pv-grid">${d.refs.map((f) => `<div class="pv-item"><img src="/api/privado/ref/${esc(f)}" loading="lazy">
      <button class="btn sm ghost" data-pvrm="${esc(f)}">Quitar</button></div>`).join("") || '<div class="faint small">Todavía no tiene. Súbelas o agrégalas desde lo generado abajo.</div>'}</div>
    <b style="margin-top:10px">Generadas en Shelton Hot (${d.generated.length})</b>
    <div class="pv-grid">${d.generated.map((e) => `<div class="pv-item"><img src="/api/privado/imagen/${esc(e.id)}?t=1" loading="lazy" decoding="async">
      ${d.refs.includes(e.file) ? '<span class="chip good">En sus fotos</span>' : `<button class="btn sm" data-pvadd="${esc(e.id)}">➕ A sus fotos</button>`}</div>`).join("") || '<div class="faint small">Todavía no hay. Pídelas en Shelton Hot nombrándola.</div>'}</div>`
    : `<div class="card pv-rules">Primero confirma en <b>Shelton Hot</b> (Opciones avanzadas) que <b>${esc(name)}</b> es una modelo <b>inventada y mayor de 18</b>.</div>`,
    `<button class="btn ghost" onclick="closeModal()">Cerrar</button>`, true);
  if (!d.confirmed) return;
  const tp = $("#pv-trainp", box);
  if (tp) tp.onchange = async (ev) => { await api(`/api/privado/modelo/${sid}/entrenar-con-privadas`, { method: "POST", body: { on: ev.target.checked } }); };
  $("#pv-up", box).onchange = async (ev) => {
    const fd = new FormData(); [...ev.target.files].forEach((f) => fd.append("files", f));
    try { const r = await api(`/api/privado/modelo/${sid}/subir`, { method: "POST", body: fd }); if (r.auto) toast(`${esc(name)}: ${esc(r.auto)}`, "good", "🎓"); privateDialog(sid, name); route(); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  $$("[data-pvadd]", box).forEach((b) => b.onclick = async () => { try { await api(`/api/privado/modelo/${sid}/fotos`, { method: "POST", body: { entry_id: b.dataset.pvadd } }); privateDialog(sid, name); } catch (e) { toast(esc(e.message), "bad"); } });
  $$("[data-pvrm]", box).forEach((b) => b.onclick = async () => { await api(`/api/privado/modelo/${sid}/quitar`, { method: "POST", body: { file: b.dataset.pvrm } }); privateDialog(sid, name); });
}
