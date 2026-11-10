// Shelton Studio — copiar y pegar bien en el chat:
// · botón «Copiar» en cada mensaje y «Copiar conversación» arriba;
// · al seleccionar varios mensajes y copiar (Ctrl+C) sale solo el texto limpio: «Tú: … / Shelton: …»
//   (sin avatares, pasos de «Pensando…» ni etiquetas de las fotos);
// · Ctrl+V de una imagen (captura de pantalla) la adjunta al mensaje;
// · Ctrl+Z con la caja vacía recupera lo último que enviaste.
"use strict";

function _who(msgEl) {
  return msgEl.classList.contains("user") ? "Tú" : (($(".who", msgEl) || {}).textContent || agentName(S.chatAgent));
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const t = document.createElement("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
    document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
  }
  toast("Copiado", "good", "📋");
}

function conversationText(root) {
  return $$(".msg", root).map((m) => {
    const parts = $$(".bubble", m).map((b) => b.textContent.trim()).filter(Boolean);
    return parts.length ? `${_who(m)}: ${parts.join("\n")}` : "";
  }).filter(Boolean).join("\n\n");
}

// Botón «Copiar» en cada globo
function addCopyButtons(root) {
  $$(".bubble", root).forEach((b) => {
    if (b.dataset.cp || !b.textContent.trim()) return;
    b.dataset.cp = "1";
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "copy-btn"; btn.title = "Copiar este mensaje"; btn.textContent = "📋 Copiar";
    btn.onclick = (e) => { e.stopPropagation(); copyText(b.textContent.trim()); };
    b.after(btn);
    // Mensajes tuyos muy largos (una conversación pegada, un documento): se ven recogidos, con «Ver todo»
    if (b.closest(".msg.user") && b.textContent.length > 1200) {
      const label = () => `Ver todo (${b.textContent.length.toLocaleString("es")} caracteres)`;
      b.classList.add("long");
      const more = document.createElement("button");
      more.type = "button"; more.className = "copy-btn more-btn"; more.textContent = label();
      more.onclick = (e) => { e.stopPropagation(); more.textContent = b.classList.toggle("long") ? label() : "Ver menos"; };
      btn.after(more);
    }
  });
}

// Ctrl+A en el chat (fuera de la caja de texto): selecciona solo la conversación, no toda la página
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "a" || e.shiftKey) return;
  const t = e.target, msgs = document.getElementById("msgs");
  if (!msgs || !msgs.isConnected || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return;
  e.preventDefault();
  const r = document.createRange(); r.selectNodeContents(msgs);
  const sel = getSelection(); sel.empty(); sel.addRange(r);
});

// Copiar varios mensajes seleccionados: texto limpio
document.addEventListener("copy", (e) => {
  const sel = getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const msgs = document.getElementById("msgs");
  const r = sel.getRangeAt(0);
  if (!msgs || !(msgs.contains(r.commonAncestorContainer) || r.intersectsNode(msgs))) return;
  const frag = r.cloneContents();
  if (!msgs.contains(r.commonAncestorContainer) && $$(".msg", frag).length < 1) return;
  if ($$(".msg", frag).length < 2) {
    // Dentro de un solo mensaje: solo el texto de los globos (sin «Pensando…» ni etiquetas)
    const bubbles = $$(".bubble", frag);
    if (!bubbles.length) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", bubbles.map((b) => b.textContent.trim()).join("\n"));
    return;
  }
  e.preventDefault();
  e.clipboardData.setData("text/plain", conversationText(frag));
});

// Pegar una imagen (captura) en la caja del chat: se adjunta igual que con el clip
document.addEventListener("paste", async (e) => {
  if (e.target.id !== "input") return;
  const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (!imgs.length) return;
  e.preventDefault();
  for (const f of imgs) {
    const fd = new FormData(); fd.append("file", f, f.name || "pegada.png");
    try {
      const p = await api("/api/upload", { method: "POST", body: fd });
      S.uploads.push(p); S.posts.set(p.id, p);
      const im = document.createElement("img"); im.src = p.image_url; $("#attachments")?.append(im);
      toast("Imagen pegada: va con tu próximo mensaje", "good", "🖼️");
    } catch (err) { toast(esc(err.message), "bad"); }
  }
});

// Ctrl+Z con la caja vacía: vuelve lo último que enviaste (por si lo querías corregir)
let _lastSent = "";
document.addEventListener("submit", (e) => {
  if (e.target.id === "composer") _lastSent = ($("#input")?.value || "").trim() || _lastSent;
}, true);
document.addEventListener("keydown", (e) => {
  if (e.target.id !== "input" || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z" || e.shiftKey) return;
  if (e.target.value === "" && _lastSent) {
    e.preventDefault();
    e.target.value = _lastSent;
    e.target.dispatchEvent(new Event("input"));
  }
});

// Botones en los mensajes (también los que van llegando) y «Copiar conversación» arriba
let _cpPending = false;
const _cpObs = new MutationObserver(() => {
  if (_cpPending) return;
  _cpPending = true;
  requestAnimationFrame(() => { _cpPending = false; _cpRun(); });
});
function _cpRun() {
  const msgs = document.getElementById("msgs");
  if (!msgs) return;
  addCopyButtons(msgs);
  const head = $(".chat-head");
  if (head && !$("#copy-conv", head)) {
    const b = document.createElement("button");
    b.className = "btn sm"; b.id = "copy-conv"; b.type = "button"; b.title = "Copiar toda la conversación como texto";
    b.textContent = "📋 Copiar conversación";
    b.onclick = () => { const t = conversationText(document.getElementById("msgs")); if (t) copyText(t); else toast("Todavía no hay mensajes", "warn"); };
    head.append(b);
  }
}
_cpObs.observe(document.body, { childList: true, subtree: true });
