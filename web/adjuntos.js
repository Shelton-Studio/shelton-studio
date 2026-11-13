// ------------------------------------------------------------------ Fotos en el chat al estilo Higgsfield
// Pegar (Ctrl+V), arrastrar o usar el clip → miniaturas numeradas image1, image2… con su X.
// Escribir @ abre el menú de fotos; las menciones @imageN se validan y se renumeran solas.
const ATT_MAX_MB = 20;
S.uploading = 0;

function attEls() { return { box: $("#attachments"), input: $("#input"), send: $("#send") }; }

function attRender() {
  const { box, send } = attEls();
  if (!box) return;
  let wrap = $("#att-imgs", box);
  if (!wrap) { wrap = document.createElement("div"); wrap.id = "att-imgs"; wrap.className = "att-imgs"; box.prepend(wrap); }
  // las miniaturas viejas (sin número) que dejan otros módulos se reemplazan por las nuestras
  $$(":scope > img", box).forEach((im) => im.remove());
  wrap.innerHTML = S.uploads.map((p, i) => `<div class="att-item" title="image${i + 1}"><img src="${esc(p.image_url)}" alt="image${i + 1}">
      <span class="att-tag">image${i + 1}</span><button type="button" class="att-x" data-attx="${i}" title="Quitar image${i + 1}">✕</button></div>`).join("")
    + Array.from({ length: S.uploading }, () => '<div class="att-item att-loading"><div class="spinner"></div></div>').join("");
  $$("[data-attx]", wrap).forEach((b) => b.onclick = (e) => { e.preventDefault(); attRemove(+b.dataset.attx); });
  if (send) send.disabled = S.uploading > 0 || S.busy;
  attMentions();
}

function attRemove(idx) {
  const { input } = attEls();
  S.uploads.splice(idx, 1);
  if (input) {          // @imageK: la borrada queda marcada (?) y las siguientes bajan un número
    const n = idx + 1;
    input.value = input.value.replace(/@image(\d+)\b/gi, (m, k) => {
      k = +k;
      if (k === n) return "@image?";
      return k > n ? `@image${k - 1}` : m;
    });
  }
  attRender();
}

async function attAdd(files) {
  const imgs = [...files].filter((f) => {
    if (!f.type.startsWith("image/")) { toast(`«${esc(f.name || "archivo")}» no es una imagen: no se adjuntó`, "bad"); return false; }
    if (f.size > ATT_MAX_MB * 1024 * 1024) { toast(`«${esc(f.name || "imagen")}» pesa más de ${ATT_MAX_MB} MB: no se adjuntó`, "bad"); return false; }
    return true;
  });
  for (const f of imgs) {
    S.uploading++; attRender();
    const fd = new FormData(); fd.append("file", f, f.name || "pegada.png");
    try { const p = await api("/api/upload", { method: "POST", body: fd }); S.uploads.push(p); S.posts.set(p.id, p); }
    catch (err) { toast(esc(err.message), "bad"); }
    finally { S.uploading--; attRender(); }
  }
}

function attMentions() {
  const { box, input } = attEls();
  if (!box || !input) return;
  let strip = $("#att-ment", box);
  const found = [...new Set((input.value.match(/@image(\d+|\?)/gi) || []).map((m) => m.toLowerCase()))];
  if (!found.length) { strip?.remove(); return; }
  if (!strip) { strip = document.createElement("div"); strip.id = "att-ment"; strip.className = "att-ment"; box.append(strip); }
  strip.innerHTML = found.map((m) => {
    const k = +m.replace("@image", ""), p = S.uploads[k - 1];
    return p ? `<span class="ment ok" title="${m}"><img src="${esc(p.image_url)}">${m}</span>`
      : `<span class="ment bad" title="${m === "@image?" ? "Esa foto se borró: corrige la mención" : `No hay ${m.slice(1)}: tienes ${S.uploads.length} foto(s)`}">${m}</span>`;
  }).join("");
}

function attBadMentions(text) {
  return [...new Set((text.match(/@image(\d+|\?)/gi) || []).map((m) => m.toLowerCase()))]
    .filter((m) => { const k = +m.replace("@image", ""); return !(k >= 1 && k <= S.uploads.length); });
}

// ----------------------------------------------------------- menú de @
let attMenu = null, attSel = 0;
function attCloseMenu() { attMenu?.remove(); attMenu = null; }
function attOpenMenu() {
  const { input } = attEls();
  if (!input || !S.uploads.length) return attCloseMenu();
  const before = input.value.slice(0, input.selectionStart);
  const m = before.match(/@(image)?(\d*)$/i);
  if (!m) return attCloseMenu();
  const q = m[2] || "";
  const opts = S.uploads.map((p, i) => ({ p, n: i + 1 })).filter((o) => String(o.n).startsWith(q));
  if (!opts.length) return attCloseMenu();
  if (!attMenu) { attMenu = document.createElement("div"); attMenu.className = "att-menu"; input.closest(".composer-box").append(attMenu); attSel = 0; }
  attSel = Math.min(attSel, opts.length - 1);
  attMenu.innerHTML = opts.map((o, i) => `<div class="att-opt ${i === attSel ? "on" : ""}" data-n="${o.n}"><img src="${esc(o.p.image_url)}"><b>@image${o.n}</b></div>`).join("");
  $$(".att-opt", attMenu).forEach((el) => el.onmousedown = (e) => { e.preventDefault(); attInsert(+el.dataset.n); });
}
function attInsert(n) {
  const { input } = attEls();
  const pos = input.selectionStart, before = input.value.slice(0, pos).replace(/@(image)?\d*$/i, `@image${n} `);
  input.value = before + input.value.slice(pos);
  input.selectionStart = input.selectionEnd = before.length;
  attCloseMenu(); attMentions(); input.focus();
}

// ----------------------------------------------------------- engancharse al chat (sin tocar el resto)
const _chatAdj = VIEWS.chat;
VIEWS.chat = async (el, convId) => {
  await _chatAdj(el, convId);
  const { input } = attEls();
  if (!input || input.dataset.att) return;
  input.dataset.att = "1";
  const fileIn = $("#file-in", el);
  if (fileIn) fileIn.onchange = async (e) => { await attAdd(e.target.files); e.target.value = ""; };
  input.addEventListener("paste", (e) => {          // Ctrl+V: fotos (capturas, desde otra web o archivos)
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;                       // si es texto, se pega como texto normal
    e.preventDefault(); e.stopPropagation();         // que no la adjunte también el pegado viejo
    attAdd(files);
  });
  const boxEl = input.closest(".composer-box");
  boxEl.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); boxEl.classList.add("drag"); } });
  boxEl.addEventListener("dragleave", () => boxEl.classList.remove("drag"));
  boxEl.addEventListener("drop", (e) => { if (!e.dataTransfer?.files?.length) return; e.preventDefault(); boxEl.classList.remove("drag"); attAdd(e.dataTransfer.files); });
  input.addEventListener("input", () => { attOpenMenu(); attMentions(); });
  input.addEventListener("keydown", (e) => {
    if (attMenu) {
      const n = $$(".att-opt", attMenu).length;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); e.stopImmediatePropagation(); attSel = (attSel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; attOpenMenu(); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); e.stopImmediatePropagation(); const o = $$(".att-opt", attMenu)[attSel]; if (o) attInsert(+o.dataset.n); return; }
      if (e.key === "Escape") { attCloseMenu(); return; }
    }
    if (e.key === "Backspace" && !input.value && S.uploads.length) { e.preventDefault(); attRemove(S.uploads.length - 1); }
  }, true);
  input.addEventListener("blur", () => setTimeout(attCloseMenu, 150));
  $("#composer", el)?.addEventListener("submit", (e) => {   // antes de enviar: nada subiendo y menciones válidas
    const bad = attBadMentions(input.value);
    if (S.uploading) { e.preventDefault(); e.stopImmediatePropagation(); return toast("Espera a que terminen de subir las fotos", "warn"); }
    if (bad.length) { e.preventDefault(); e.stopImmediatePropagation(); return toast(`${bad.join(", ")} no existe: tienes ${S.uploads.length} foto(s). Corrige la mención antes de enviar.`, "bad"); }
    setTimeout(() => { attCloseMenu(); attRender(); }, 0);
  }, true);
  attRender();
};

// En los mensajes ya enviados, las fotos conservan su número (image1, image2…)
const _addMsgAdj = addMsg;
addMsg = function (role, text, postIds = []) {
  const body = _addMsgAdj(role, text, postIds);
  if (role === "user" && postIds.length) $(".thumbs", body)?.classList.add("numbered");
  if (role === "user" && text && /@image\d+/i.test(text)) {
    const b = $(".bubble", body);
    if (b) b.innerHTML = esc(text).replace(/@image(\d+)/gi, '<span class="ment ok">@image$1</span>');
  }
  return body;
};
