// Shelton Studio — 🪪 Cara por foto: 1 a 3 fotos de la cara de una modelo del Banco y salen fotos nuevas con esa cara
// en segundos, sin entrenar (InstantID sobre SDXL + retoque de la cara). La LoRA sigue igual: esto es aparte.

const _idPrevBanco = VIEWS.banco;
VIEWS.banco = async (el) => {
  await _idPrevBanco(el);
  let subs = [];
  try { subs = await api("/api/subjects"); } catch { return; }
  const byId = Object.fromEntries(subs.map((s) => [s.id, s]));
  $$(".subject-card").forEach((card) => {
    const sid = $("[data-del]", card)?.dataset.del, s = byId[sid];
    const info = s?.lora?.info || {};
    if (!s || s.kind !== "person" || info.adult_twin || info.adult_style || $("[data-idcara]", card)) return;
    const b = document.createElement("button");
    b.className = "btn sm ghost"; b.dataset.idcara = sid; b.textContent = "🪪 Cara por foto";
    b.title = "Sube 1 a 3 fotos de su cara y genera fotos nuevas con esa cara al instante, sin entrenar";
    b.onclick = () => identityDialog(sid, s.name);
    ($(".lora-box", card) || $(".body", card))?.append(b);
  });
};

async function identityDialog(sid, name) {
  let d;
  try { d = await api(`/api/subjects/${sid}/identidad`); } catch (e) { return toast(esc(e.message), "bad"); }
  const box = modal(`🪪 Cara por foto · ${esc(name)}`, `
    <div class="hint">Sube <b>1 a ${d.max_refs} fotos claras de su cara</b> (de frente, buena luz). Shelton genera fotos nuevas con esa cara <b>en segundos, sin entrenar</b> (InstantID con el motor SDXL y un retoque final de la cara). Su LoRA no se toca.</div>
    <div class="row" id="id-refs" style="gap:8px;flex-wrap:wrap;margin:10px 0"></div>
    <label class="btn sm" id="id-up-l">📤 Subir foto de la cara<input type="file" id="id-up" accept="image/*" hidden></label>
    <div class="row" style="gap:16px;flex-wrap:wrap;margin-top:12px">
      <label class="small">Parecido de la cara: <b id="id-wv">${d.weight}</b><br><input type="range" id="id-w" min="0.3" max="1.5" step="0.05" value="${d.weight}"></label>
      <label class="small">Pose: <b id="id-pv">${poseLabel(d.pose)}</b><br><input type="range" id="id-p" min="0" max="1" step="0.05" value="${d.pose}"></label>
    </div>
    <div class="faint small">Más parecido = la cara sale más igual, con menos libertad de estilo. Más pose = copia la pose de la foto; menos = pose libre.</div>
    <label style="display:block;margin-top:12px">Qué foto quieres<textarea id="id-prompt" rows="3" placeholder="Ej: ${esc(name)} en un café de París, luz de ventana, vestido rojo"></textarea></label>
    <div class="row" style="gap:8px;margin-top:8px">
      <div class="seg" id="id-size"><button data-sz="vertical" class="on">Vertical</button><button data-sz="cuadrada">Cuadrada</button><button data-sz="horizontal">Horizontal</button></div>
      <div class="seg" id="id-count"><button data-n="1" class="on">1</button><button data-n="2">2</button><button data-n="4">4</button></div>
    </div>
    <div class="faint small" id="id-en" style="margin-top:6px"></div>
    <div class="post-grid" id="id-out" style="margin-top:12px"></div>`,
    `<button class="btn" id="id-close">Cerrar</button><button class="btn primary" id="id-go">🪪 Generar con su cara</button>`, true);
  let size = "vertical", count = 1;
  const draw = (info) => {
    d = info;
    $("#id-refs", box).innerHTML = info.refs.length ? info.refs.map((r) => `<div style="position:relative">
        <img src="${r.url}" style="width:96px;height:96px;object-fit:cover;border-radius:10px">
        <button class="icon-btn" data-idrm="${esc(r.file)}" title="Quitar esta foto" style="position:absolute;top:2px;right:2px">✕</button></div>`).join("")
      : `<div class="faint small">Todavía no tiene fotos de la cara.</div>`;
    $("#id-up-l", box).style.display = info.refs.length >= info.max_refs ? "none" : "";
    $$("[data-idrm]", box).forEach((b) => b.onclick = async () => {
      try { draw(await api(`/api/subjects/${sid}/identidad/${encodeURIComponent(b.dataset.idrm)}`, { method: "DELETE" })); } catch (e) { toast(esc(e.message), "bad"); }
    });
    const out = $("#id-out", box);
    if (!out.dataset.fresh) out.innerHTML = info.tests.map((t) => `<a href="${t.url}" target="_blank"><img src="${t.url}" style="width:100%;border-radius:10px"></a>`).join("");
  };
  draw(d);
  $("#id-close", box).onclick = closeModal;
  $("#id-up", box).onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f, f.name || "cara.jpg");
    $("#id-up-l", box).classList.add("loading");
    try { draw(await api(`/api/subjects/${sid}/identidad`, { method: "POST", body: fd })); toast("Foto de la cara guardada", "good", "🪪"); }
    catch (err) { toast(esc(err.message), "bad"); }
    finally { $("#id-up-l", box).classList.remove("loading"); e.target.value = ""; }
  };
  const saveOpts = async () => {
    try { await api(`/api/subjects/${sid}/identidad`, { method: "PUT", body: { weight: +$("#id-w", box).value, pose: +$("#id-p", box).value } }); } catch (e) { toast(esc(e.message), "bad"); }
  };
  $("#id-w", box).oninput = (e) => { $("#id-wv", box).textContent = e.target.value; };
  $("#id-p", box).oninput = (e) => { $("#id-pv", box).textContent = poseLabel(+e.target.value); };
  $("#id-w", box).onchange = saveOpts; $("#id-p", box).onchange = saveOpts;
  $$("#id-size button", box).forEach((b) => b.onclick = () => { size = b.dataset.sz; $$("#id-size button", box).forEach((x) => x.classList.toggle("on", x === b)); });
  $$("#id-count button", box).forEach((b) => b.onclick = () => { count = +b.dataset.n; $$("#id-count button", box).forEach((x) => x.classList.toggle("on", x === b)); });
  $("#id-go", box).onclick = async () => {
    const prompt = $("#id-prompt", box).value.trim();
    if (!d.refs.length) return toast("Primero sube una foto de su cara", "warn", "🪪");
    if (!prompt) return toast("Escribe qué foto quieres", "warn", "🪪");
    const go = $("#id-go", box); go.disabled = true; go.textContent = `Generando ${count === 1 ? "1 foto" : `${count} fotos`}…`;
    try {
      const r = await api(`/api/subjects/${sid}/identidad/generar`, { method: "POST", body: { prompt, count, size } });
      $("#id-en", box).textContent = r.prompt_en ? `En inglés: ${r.prompt_en}` : "";
      const out = $("#id-out", box); out.dataset.fresh = "1";
      out.innerHTML = r.images.map((t) => `<a href="${t.url}" target="_blank"><img src="${t.url}" style="width:100%;border-radius:10px"></a>`).join("") + out.innerHTML;
    } catch (e) { toast(esc(e.message), "bad"); }
    finally { go.disabled = false; go.textContent = "🪪 Generar con su cara"; }
  };
}

function poseLabel(v) { return v >= 0.7 ? "copia la de la foto" : v <= 0.3 ? "libre" : "intermedia"; }
