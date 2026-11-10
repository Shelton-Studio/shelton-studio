// Shelton Studio — el chat queda donde lo dejaste: al ir a otro panel y volver (o recargar la página) sigue la misma
// conversación, con la respuesta que se estaba escribiendo y en la misma posición.
"use strict";

const lastConvKey = () => `lastConv:${S.chatAgent || "shelton"}`;
const _chatKeep = VIEWS.chat;
// Mientras estás en otro panel, el chat queda escondido en la página (no se destruye): así una respuesta que se
// está escribiendo sigue llegando y no se corta.
const chatHolder = document.createElement("div");
chatHolder.id = "chat-holder";
chatHolder.style.display = "none";
document.body.append(chatHolder);

function onChatPage() { return (location.hash.slice(2).split("/")[0] || "inicio") === "chat"; }

VIEWS.chat = async (el, convId) => {
  const kept = S.chatDom;
  const sameAgent = kept && kept.dataset.agent === (S.chatAgent || "shelton");
  // 1) Volver al chat que estaba abierto: se reengancha tal cual
  if (!S.freshChat && kept && sameAgent && (!convId || convId === S.conv)) {
    el.innerHTML = "";
    el.append(kept);
    const msgs = $("#msgs", kept);
    if (msgs && kept.dataset.scroll) msgs.scrollTop = +kept.dataset.scroll;
    if (S.conv && !location.hash.includes(S.conv)) history.replaceState(null, "", `#/chat/${S.conv}`);
    try { renderThreads(); } catch {}
    return;
  }
  // 2) Otra conversación o una nueva: se descarta la guardada
  chatHolder.innerHTML = "";
  if (S.freshChat) S.busy = false;
  // 3) Recién entra (o recargó la página): abre la última conversación de este agente
  if (!convId && !S.conv && !S.freshChat) {
    try { convId = localStorage.getItem(lastConvKey()) || undefined; } catch {}
  }
  S.freshChat = false;
  await _chatKeep(el, convId);
  const wrap = el.firstElementChild;
  if (!wrap) return;
  wrap.dataset.agent = S.chatAgent || "shelton";
  S.chatDom = wrap;
  if (S.conv && !location.hash.includes(S.conv)) history.replaceState(null, "", `#/chat/${S.conv}`);
  const msgs = $("#msgs", wrap);
  if (msgs) msgs.addEventListener("scroll", () => { wrap.dataset.scroll = msgs.scrollTop; }, { passive: true });
  // «Nueva conversación» y cambiar de agente sí empiezan de cero
  const nb = $("#new-chat", wrap);
  if (nb) { const old = nb.onclick; nb.onclick = (e) => { S.freshChat = true; S.chatDom = null; old && old(e); }; }
  const sel = $("#agent-sel", wrap);
  if (sel) { const old = sel.onchange; sel.onchange = (e) => { S.freshChat = true; S.chatDom = null; old && old(e); }; }
  // Abrir otra conversación de la lista: se carga esa
  const threads = $("#th-list", wrap);
  if (threads) threads.addEventListener("click", () => { S.chatDom = null; }, true);
};

// Al salir del chat, se guarda escondido (sigue recibiendo la respuesta en curso)
window.addEventListener("hashchange", () => {
  if (!onChatPage() && S.chatDom && !S.chatDom.isConnected) chatHolder.append(S.chatDom);
});

// Recuerda la conversación abierta (sobrevive a recargar la página)
setInterval(() => {
  try { if (S.conv) localStorage.setItem(lastConvKey(), S.conv); } catch {}
}, 1500);
