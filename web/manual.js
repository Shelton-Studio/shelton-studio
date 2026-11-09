// Shelton Studio — crear un modelo del banco a mano (con tus datos y tus fotos) y entrenar su LoRA de una vez.
"use strict";

const _bancoManual = VIEWS.banco;
VIEWS.banco = async (el) => {
  await _bancoManual(el);
  const btn = $("#new-sub");
  if (btn) btn.onclick = () => manualSubject();
  // «+ Foto» acepta varias fotos a la vez
  $$("[data-photo]").forEach((i) => {
    i.multiple = true;
    i.onchange = async (e) => {
      const files = [...e.target.files]; if (!files.length) return;
      toast(`Subiendo ${files.length} foto(s)…`, "", "📸");
      let ok = 0;
      for (const f of files) {
        const fd = new FormData(); fd.append("file", f); fd.append("describe", "false");
        try { await api(`/api/subjects/${i.dataset.photo}/photos`, { method: "POST", body: fd }); ok++; } catch (err) { toast(esc(err.message), "bad"); }
      }
      toast(`${ok} foto(s) agregadas`, "good", "📸"); route();
    };
  });
};

async function manualSubject() {
  let trainer = { trainer_ready: false, trainer_msg: "" };
  try { trainer = await api("/api/lora/available"); } catch {}
  const box = modal("Nuevo modelo del banco", `<div class="hint">Créalo a mano con tus datos y tus fotos (por ejemplo las 20 que ya tienen). El Analista mira las fotos y arma su ficha. Con 8 o más fotos puedes entrenar su LoRA en la 3090 de una vez.</div>
    <div class="row"><label class="f grow">Nombre *<input id="mn" placeholder="Camila, Mustang Rojo…"></label>
      <label class="f" style="width:180px">Tipo<select id="mk">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === "person" ? "selected" : ""}>${v}</option>`).join("")}<option value="auto">Detectar solo</option></select></label></div>
    <div class="row person-only"><label class="f" style="width:110px">Edad<input id="ma" type="number" min="18" max="99" placeholder="21"></label>
      <label class="f" style="width:160px">Género<select id="mg"><option value="">—</option><option>mujer</option><option>hombre</option><option>otro</option></select></label>
      <label class="f grow">Origen / nacionalidad<input id="mo" placeholder="argentina, colombiana…"></label></div>
    <label class="f">Rasgos (cara, ojos, pelo, piel, estatura, señas)<textarea id="mf" rows="2" placeholder="Ojos verdes, pelo castaño largo con ondas, pecas suaves, 1,65 m, lunar en la mejilla izquierda…"></textarea></label>
    <label class="f">Estilo y ropa<input id="ms" placeholder="casual chic, tonos beige, jeans oscuros…"></label>
    <label class="f">Personalidad / historia (para los textos)<input id="mp" placeholder="tímida, le gusta salir a comer y de compras…"></label>
    <label class="f">Fotos (puedes elegir muchas a la vez; hasta 40)<input type="file" id="mph" accept="image/*" multiple></label>
    <div class="m-thumbs" id="mth"></div>
    <label class="checks"><label><input type="checkbox" id="mlora" ${trainer.trainer_ready ? "" : "disabled"}> 🎓 Entrenar su LoRA al guardar (necesita 8+ fotos)</label></label>
    ${trainer.trainer_ready ? "" : `<div class="faint small">${esc(trainer.trainer_msg || "El entrenador de LoRA se instala en la PC con la GPU.")}</div>`}
    <div class="row"><label class="f grow">Muestra de su voz (opcional)<input type="file" id="mv" accept="audio/*"></label><label class="f grow">Cómo suena<input id="mvs" placeholder="dulce, juvenil, acento rioplatense"></label></div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="msave">Guardar en el banco</button>`, true);
  const kindSel = $("#mk", box);
  const toggle = () => $$(".person-only", box).forEach((r) => { r.style.display = kindSel.value === "person" ? "" : "none"; });
  kindSel.onchange = toggle; toggle();
  $("#mph", box).onchange = (e) => {
    const files = [...e.target.files];
    $("#mth", box).innerHTML = files.slice(0, 40).map((f) => `<img src="${URL.createObjectURL(f)}">`).join("") + (files.length ? `<span class="chip">${files.length} fotos</span>` : "");
    $("#mlora", box).checked = trainer.trainer_ready && files.length >= 8;
  };
  $("#msave", box).onclick = async () => {
    const name = $("#mn").value.trim(); if (!name) return toast("Ponle un nombre", "bad");
    const files = [...$("#mph").files].slice(0, 40), kind = kindSel.value;
    const parts = [];
    if (kind === "person") {
      if ($("#ma").value) parts.push(`${$("#ma").value} años`);
      if ($("#mg").value) parts.push($("#mg").value);
      if ($("#mo").value) parts.push($("#mo").value);
    }
    if ($("#mf").value) parts.push(`Rasgos: ${$("#mf").value}`);
    if ($("#ms").value) parts.push(`Estilo: ${$("#ms").value}`);
    if ($("#mp").value) parts.push(`Personalidad: ${$("#mp").value}`);
    const notes = parts.join(". ");
    const saveBtn = $("#msave", box); saveBtn.disabled = true;
    try {
      let sub;
      if (files.length) {
        saveBtn.textContent = `Subiendo y analizando ${files.length} fotos…`;
        const fd = new FormData(); files.forEach((f) => fd.append("files", f));
        fd.append("name", name); fd.append("kind", kind); fd.append("notes", notes);
        sub = await api("/api/subjects/from-photos", { method: "POST", body: fd });
      } else {
        const r = await api("/api/subjects", { method: "POST", body: { name, kind: kind === "auto" ? "person" : kind, request: notes || name, generate: true } });
        sub = { id: r.id, name, references: [] };
      }
      if ($("#mv").files[0] || $("#mvs").value) {
        const fv = new FormData(); if ($("#mv").files[0]) fv.append("file", $("#mv").files[0]);
        await api(`/api/subjects/${sub.id}/voice?style=${encodeURIComponent($("#mvs").value)}`, { method: "POST", body: fv });
      }
      const train = $("#mlora").checked && files.length >= 8;
      closeModal();
      if (train) {
        try {
          const r = await api(`/api/subjects/${sub.id}/lora/train`, { method: "POST", body: {} });
          toast(`<b>${esc(name)}</b> guardado con ${files.length} fotos y su LoRA ya se está entrenando (${r.steps} pasos). Lo ves en el Banco y en En vivo.`, "good", "🎓");
        } catch (e) { toast(`<b>${esc(name)}</b> guardado. No se pudo empezar la LoRA: ${esc(e.message)}`, "warn", "🎓"); }
      } else {
        toast(files.length ? `<b>${esc(name)}</b> guardado con ${files.length} fotos. Ya puedes pedir contenido con su nombre.` : `El diseñador está creando la imagen de referencia de <b>${esc(name)}</b>…`, "good", "👤");
      }
      route();
    } catch (err) { saveBtn.disabled = false; saveBtn.textContent = "Guardar en el banco"; toast(esc(err.message), "bad"); }
  };
}
