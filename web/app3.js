// Shelton Studio — extras: robot optimizador (tócalo y háblale como a Siri), dictado por voz, biblioteca de audios,
// copiar videos de TikTok/Reels con tus modelos, memoria por modelo y exportar todo.
"use strict";

// ------------------------------------------------------------------ Menú: vistas nuevas
(function addNav() {
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "voces");
  NAV.splice(i + 1, 0, ["audios", "wave", "Audios"], ["referencias", "film", "Copiar videos"]);
  renderRail(); markNav();
  if (S.wl && S.wl.product_name) applyWL();
})();

// ------------------------------------------------------------------ Eventos nuevos
const _renderWorking = renderWorking;
window.renderWorking = renderWorking = function () { _renderWorking(); if (window.Bot) Bot.renderLive(); };
window.onExtraEvent = (ev) => {
  if (ev.type === "network_review") {
    if (ev.status === "done") toast(`🌐 <b>Revisión de la red lista</b>: ${ev.findings || 0} novedad(es) de los scouts${ev.timing ? " y mejor hora para publicar actualizada" : ""}.`, "good", "🌐", `<button class="btn sm" onclick="Bot.toggle(true)">Ver</button>`);
    if (window.Bot) Bot.refresh();
    return;
  }
  if (ev.type === "team_learned") {
    const msg = `<b>Shelton pasó la lección</b> «${esc(ev.title)}» de ${esc(ev.from)} al equipo: ${ev.agents.map((a) => `<span class="chip ${ev.applied.includes(a) ? "good" : "warn"}">${esc(a)}</span>`).join(" ")}`;
    toast(msg, "good", "🧠"); note(msg, "good", "memoria");
  } else if (ev.type === "audio") {
    if (location.hash.startsWith("#/audios")) VIEWS.audios($("#view"));
  } else if (ev.type === "reference") {
    if (location.hash.startsWith("#/referencias")) VIEWS.referencias($("#view"));
  }
};

// ------------------------------------------------------------------ Voz: dictado como Siri / Gemini
const Voice = {
  rec: null, busy: false,
  supported() { return !!(navigator.mediaDevices && window.MediaRecorder); },
  secureMsg() {
    return "El micrófono solo funciona con <b>https</b> o en <b>localhost</b>. Abre Shelton con <code>tailscale serve</code> " +
      "(https) o con un túnel a localhost. Está explicado en el README.";
  },
  // Graba hasta que dejas de hablar (o tocas otra vez) y transcribe. onState(estado, nivel 0-1)
  async listen(onState = () => {}) {
    if (!window.isSecureContext) { toast(this.secureMsg(), "warn", "🎙️"); return null; }
    if (!this.supported()) { toast("Este navegador no permite grabar audio. Prueba con Chrome, Edge o Safari actualizados.", "warn", "🎙️"); return null; }
    if (this.busy) { this.stop(); return null; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch { toast("No tengo permiso para usar el micrófono. Actívalo en el candado de la barra de direcciones.", "warn", "🎙️"); return null; }
    this.busy = true;
    const ctx = new (window.AudioContext || window.webkitAudioContext)(), src = ctx.createMediaStreamSource(stream), an = ctx.createAnalyser();
    an.fftSize = 1024; src.connect(an);
    const buf = new Uint8Array(an.fftSize);
    const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported?.(t)) || "";
    const rec = new MediaRecorder(stream, type ? { mimeType: type } : {}); this.rec = rec;
    const chunks = []; rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    let spoke = false, silentSince = performance.now(), peak = 0; const t0 = performance.now();
    onState("listening", 0);
    const tick = () => {
      if (rec.state !== "recording") return;
      an.getByteTimeDomainData(buf);
      let sum = 0; for (const v of buf) sum += (v - 128) ** 2;
      const level = Math.min(1, Math.sqrt(sum / buf.length) / 40); peak = Math.max(peak, level);
      onState("listening", level);
      const now = performance.now();
      if (level > 0.12) { spoke = true; silentSince = now; }
      if ((spoke && now - silentSince > 1600) || now - t0 > 30000 || (!spoke && now - t0 > 7000)) rec.stop();
      else requestAnimationFrame(tick);
    };
    const done = new Promise((res) => { rec.onstop = res; });
    rec.start(250); requestAnimationFrame(tick);
    await done;
    stream.getTracks().forEach((t) => t.stop()); ctx.close(); this.busy = false; this.rec = null;
    if (!spoke || peak < 0.08) { onState("error", 0); return { text: "", clear: false, message: "Casi no te escuché 🙉 Habla un poco más alto o acércate al micrófono y repítelo." }; }
    onState("thinking", 0);
    const fd = new FormData(); fd.append("file", new Blob(chunks, { type: rec.mimeType || "audio/webm" }), "voz." + ((rec.mimeType || "").includes("mp4") ? "m4a" : (rec.mimeType || "").includes("ogg") ? "ogg" : "webm"));
    try { const r = await api("/api/transcribe", { method: "POST", body: fd }); onState(r.clear ? "done" : "error", 0); return r; }
    catch (e) { onState("error", 0); return { text: "", clear: false, message: e.message }; }
  },
  stop() { if (this.rec && this.rec.state === "recording") this.rec.stop(); },
  // Respuesta hablada (cuando le hablaste tú primero, como Siri)
  say(text) {
    if (!window.speechSynthesis || !text) return;
    const clean = text.replace(/[*_#`>]/g, "").replace(/https?:\S+/g, "").slice(0, 400);
    const u = new SpeechSynthesisUtterance(clean); u.lang = "es-ES"; u.rate = 1.05;
    const es = speechSynthesis.getVoices().find((v) => v.lang?.startsWith("es")); if (es) u.voice = es;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  },
};

// Micrófono en el chat principal: dictas, se transcribe y se envía solo (si no se entendió, te lo dice)
const _chatView = VIEWS.chat;
VIEWS.chat = async (el, arg) => {
  await _chatView(el, arg);
  const fi = $("#file-in");
  if (fi) {
    fi.accept = "image/*,audio/*,video/*,.wav,.mp3,.m4a,.mp4,.mov";
    fi.closest("label").title = "Subir imagen, audio (WAV/MP3) o video de referencia (TikTok, Reel…)";
    fi.onchange = async (e) => { for (const f of e.target.files) await attachFile(f); e.target.value = ""; };
  }
  const row = $(".composer-row");
  if (row && !$("#mic-btn")) {
    const b = document.createElement("button"); b.type = "button"; b.className = "icon-btn mic-btn"; b.id = "mic-btn";
    b.title = "Háblale (como a Siri): toca, habla y se envía solo"; b.innerHTML = icon("mic", 19);
    row.insertBefore(b, row.children[1]);
    b.onclick = async () => {
      const input = $("#input");
      const r = await Voice.listen((st, lvl) => { b.classList.toggle("rec", st === "listening"); b.style.setProperty("--lvl", lvl); if (st === "thinking") input.placeholder = "Transcribiendo…"; });
      input.placeholder = `Pídele a ${agentName(S.chatAgent)} lo que quieras…`; b.classList.remove("rec");
      if (!r) return;
      if (!r.clear) { toast(esc(r.message || "No te entendí bien, ¿lo repites?"), "warn", "🎙️"); return; }
      input.value = r.text; S.spoken = true; sendMessage();
    };
  }
  const box = $("#input");
  if (box) box.addEventListener("input", () => linkHint(box, $(".composer-box")));
  if (box) box.addEventListener("paste", (e) => { const files = [...(e.clipboardData?.files || [])].filter((f) => /^(audio|video)\//.test(f.type)); if (files.length) { e.preventDefault(); files.forEach(attachFile); } });
};
// Links de video pegados en el chat: aviso de que Shelton los va a trabajar
const VIDEO_SITES = [["tiktok.com", "TikTok"], ["instagram.com", "Instagram"], ["youtube.com", "YouTube"], ["youtu.be", "YouTube"],
  ["facebook.com", "Facebook"], ["fb.watch", "Facebook"], ["x.com", "X"], ["twitter.com", "X"], ["pinterest.", "Pinterest"], ["pin.it", "Pinterest"],
  ["threads.", "Threads"], ["snapchat.com", "Snapchat"], ["vimeo.com", "Vimeo"], ["kwai", "Kwai"], ["likee", "Likee"], ["twitch.tv", "Twitch"],
  ["reddit.com", "Reddit"], ["linkedin.com", "LinkedIn"], ["douyin.com", "Douyin"], ["capcut.com", "CapCut"]];
function videoLinks(text) {
  return (String(text).match(/https?:\/\/[^\s<>"']+/gi) || []).map((u) => {
    const low = u.toLowerCase(), site = VIDEO_SITES.find(([d]) => low.includes(d));
    return site || /\.(mp4|mov|webm|m4v)(\?|$)/.test(low) ? { url: u, site: site ? site[1] : "video" } : null;
  }).filter(Boolean);
}
function linkHint(input, box) {
  const links = videoLinks(input.value);
  let h = $(".link-hint", box);
  if (!links.length) { h?.remove(); return; }
  if (!h) { h = document.createElement("div"); h.className = "link-hint"; box.prepend(h); }
  const sites = [...new Set(links.map((l) => l.site))].join(", ");
  h.innerHTML = `🎞️ <b>Link de ${esc(sites)} detectado.</b> Shelton lo baja, guarda su audio, lo desarma por escenas y lo recrea con tu modelo. Dile qué quieres: «igual con Valentina», «algo parecido de noche», «solo el audio»…`;
}
async function attachFile(f) {
  const kind = /^audio\//.test(f.type) || /\.(wav|mp3|m4a|ogg|flac)$/i.test(f.name) ? "audio" : /^video\//.test(f.type) || /\.(mp4|mov|webm|m4v)$/i.test(f.name) ? "reference" : "image";
  const box = $("#attachments");
  if (kind === "image") {
    const fd = new FormData(); fd.append("file", f);
    try { const p = await api("/api/upload", { method: "POST", body: fd }); S.uploads.push(p); S.posts.set(p.id, p); const im = document.createElement("img"); im.src = p.image_url; box.append(im); }
    catch (err) { toast(esc(err.message), "bad"); }
    return;
  }
  const chip = document.createElement("span"); chip.className = "att-chip"; chip.innerHTML = `<span class="spinner sm"></span> ${kind === "audio" ? "Subiendo audio" : "Analizando video"}: ${esc(f.name)}…`;
  box.append(chip);
  const fd = new FormData(); fd.append("file", f);
  try {
    const r = await api(kind === "audio" ? "/api/audio" : "/api/references/upload", { method: "POST", body: fd });
    S.attach = S.attach || []; S.attach.push({ kind, id: r.id });
    chip.innerHTML = kind === "audio" ? `🎵 ${esc(r.name)} · ${Math.round(r.duration)} s` : `🎞️ ${esc(r.title || f.name)} · ${r.frames.length} escenas${r.audio_id ? " · 🎵 audio guardado" : ""}`;
    if (kind === "reference" && !$("#input").value) $("#input").value = "Hazme algo igual a este video con mi modelo ";
  } catch (err) { chip.remove(); toast(esc(err.message), "bad"); }
}

// Respuesta en voz alta si la orden fue por voz
const _send = sendMessage;
window.sendMessage = sendMessage = async function () {
  const spoken = S.spoken; S.spoken = false;
  $(".composer-box .link-hint")?.remove();
  await _send();
  if (spoken) { const last = $$("#msgs .msg.bot .bubble"); const t = last[last.length - 1]?.textContent; if (t) Voice.say(t); }
};

// ------------------------------------------------------------------ Robot optimizador (tócalo)
const BOT_SVG = `<svg viewBox="0 0 100 100" class="bot-svg" aria-hidden="true">
  <line x1="50" y1="10" x2="50" y2="22" stroke="#c4b5fd" stroke-width="4" stroke-linecap="round"/><circle class="b-ant" cx="50" cy="9" r="6" fill="#22d3ee"/>
  <rect x="14" y="22" width="72" height="60" rx="24" fill="url(#bg1)" stroke="#a78bfa" stroke-width="2.5"/>
  <rect x="6" y="44" width="10" height="20" rx="5" fill="#7c3aed"/><rect x="84" y="44" width="10" height="20" rx="5" fill="#7c3aed"/>
  <rect x="23" y="33" width="54" height="36" rx="16" fill="#0b0a12"/>
  <g class="b-eyes"><ellipse class="b-eye" cx="39" cy="50" rx="7" ry="8.5" fill="#67e8f9"/><ellipse class="b-eye" cx="61" cy="50" rx="7" ry="8.5" fill="#67e8f9"/>
  <circle class="b-glint" cx="41.5" cy="46.5" r="2.2" fill="#fff"/><circle class="b-glint" cx="63.5" cy="46.5" r="2.2" fill="#fff"/></g>
  <path class="b-mouth" d="M41 61 Q50 67 59 61" stroke="#67e8f9" stroke-width="3" fill="none" stroke-linecap="round"/>
  <circle cx="26" cy="74" r="3" fill="#f0abfc" opacity=".7"/><circle cx="74" cy="74" r="3" fill="#f0abfc" opacity=".7"/>
  <defs><linearGradient id="bg1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8b5cf6"/><stop offset="1" stop-color="#4c1d95"/></linearGradient></defs></svg>`;
const Bot = window.Bot = {
  data: null, open: false, conv: localStorage.getItem("buddyConv") || null, lastTipKey: "",
  mount() {
    const w = document.createElement("div"); w.className = "buddy"; w.id = "buddy";
    w.innerHTML = `<div class="b-bubble hidden" id="b-bubble"></div><button class="b-face" id="b-face" title="Shelton mini: tócame, dime lo que quieres y lo hago mientras sale lo demás">${BOT_SVG}<span class="b-badge hidden" id="b-badge"></span></button>`;
    document.body.append(w);
    $("#b-face").onclick = () => this.toggle();
    document.addEventListener("mousemove", (e) => this.look(e.clientX, e.clientY));
    setInterval(() => this.blink(), 3800);
    this.refresh(); setInterval(() => this.refresh(), 20000);
    setInterval(() => { if (this.open) api("/api/buddy").then((d) => { this.data = d; this.renderLive(); }).catch(() => {}); }, 5000);
  },
  look(x, y) {
    const f = $("#b-face"); if (!f) return; const r = f.getBoundingClientRect();
    const dx = Math.max(-1, Math.min(1, (x - (r.left + r.width / 2)) / 300)), dy = Math.max(-1, Math.min(1, (y - (r.top + r.height / 2)) / 300));
    $(".b-eyes", f).style.transform = `translate(${dx * 4}px, ${dy * 3}px)`;
  },
  blink() { const f = $("#b-face"); if (!f || f.classList.contains("listening")) return; f.classList.add("blink"); setTimeout(() => f.classList.remove("blink"), 160); },
  mood(m, lvl = 0) {
    const f = $("#b-face"); if (!f) return;
    f.classList.toggle("listening", m === "listening"); f.classList.toggle("thinking", m === "thinking"); f.classList.toggle("happy", m === "happy");
    f.style.setProperty("--lvl", lvl);
  },
  async refresh() {
    try { this.data = await api("/api/buddy"); } catch { return; }
    const tips = this.data.tips || [], badge = $("#b-badge");
    badge.textContent = tips.length || ""; badge.classList.toggle("hidden", !tips.length);
    $("#b-face").classList.toggle("busy", (this.data.busy || []).length > 0);
    // Burbuja temporal con la sugerencia más útil (solo cuando cambia)
    const top = tips[0], key = top ? top.text : "";
    if (!this.open && top && key !== this.lastTipKey) {
      this.lastTipKey = key;
      const b = $("#b-bubble"); b.innerHTML = `${esc(top.text)}<div class="faint small" style="margin-top:4px">Tócame para elegir</div>`; b.classList.remove("hidden");
      clearTimeout(this.bt); this.bt = setTimeout(() => b.classList.add("hidden"), 9000);
      b.onclick = () => this.toggle(true);
    }
    // No re-dibujar si estás escribiendo o hablando (no se pierde lo escrito)
    const typing = document.activeElement?.id === "b-text" || $("#b-text")?.value || Voice.busy || this.inflight;
    if (this.open && !typing) this.renderPanel(false);
  },
  toggle(force) {
    this.open = force ?? !this.open; $("#b-bubble").classList.add("hidden");
    $("#b-panel")?.remove();
    if (this.open) { this.renderPanel(true); this.mood("happy"); setTimeout(() => this.mood(""), 900); }
  },
  renderPanel(first) {
    const d = this.data || { tips: [], subjects: [], capacity: {} }, cap = d.capacity || {};
    let p = $("#b-panel");
    const log = p ? $("#b-log", p).innerHTML : "";
    if (!p) { p = document.createElement("div"); p.className = "b-panel"; p.id = "b-panel"; $("#buddy").append(p); }
    const mins = Math.max(1, Math.round((cap.queue_eta_seconds || 0) / 60));
    p.innerHTML = `<div class="b-head"><b>Shelton mini</b><span class="chip ${cap.running || cap.pending ? "accent" : "good"}">${cap.running || cap.pending ? `${(cap.running || 0) + (cap.pending || 0)} en marcha · ~${mins} min` : "Libre"}</span><span class="faint small">${cap.parallel_lanes || 1} a la vez</span><div class="grow"></div><button class="icon-btn" id="b-x">${icon("x", 16)}</button></div>
      <div id="b-live">${this.liveHTML()}</div>
      <div class="b-greet ${log ? "short" : ""}">👋 Soy <b>Shelton mini</b>. ¿Cómo usamos mejor el tiempo? <b>Escríbeme o háblame</b> (🎙️): dime qué modelo quieres usar, lo buscamos y hacemos contenido mientras sale todo lo demás. Puedes pedirme varias cosas seguidas: las hago en paralelo.</div>
      ${(d.tips || []).length ? `<div class="b-tips">${d.tips.slice(0, log ? 2 : 4).map((t, i) => `<button class="b-tip" data-tip="${i}">${esc(t.text)}</button>`).join("")}</div>` : ""}
      ${(d.subjects || []).length ? `<div class="faint small">Abrir un modelo:</div><div class="b-models">${d.subjects.map((s) => `<button class="b-model" data-sub="${esc(s.name)}" title="${s.busy ? "Ya está trabajando" : "Crear contenido de " + esc(s.name)}">${s.photo ? `<img src="${esc(s.photo)}">` : "👤"}<span>${esc(s.name)}</span>${s.busy ? '<i class="dot ok"></i>' : ""}</button>`).join("")}</div>` : ""}
      ${this.timingHTML()}
      <div class="b-log" id="b-log">${log}</div>
      <form class="b-input" id="b-form"><button type="button" class="b-mic" id="b-mic" title="Háblame">${icon("mic", 22)}</button><input id="b-text" placeholder="«Abre a Valentina y hazle 3 fotos en la playa»" autocomplete="off"><button class="btn primary sm">${icon("send", 15)}</button></form>
      <div class="row small" style="gap:8px"><button class="btn ghost sm" id="b-new">Otro chat en paralelo</button><button class="btn ghost sm" id="b-open">Ver esta conversación</button><div class="grow"></div><label class="faint"><input type="checkbox" id="b-say" ${localStorage.getItem("buddySay") !== "0" ? "checked" : ""}> Responder en voz</label></div>`;
    $("#b-x", p).onclick = () => this.toggle(false);
    if ($("#b-review", p)) $("#b-review", p).onclick = async () => { await api("/api/network-review", { method: "POST" }); toast("Shelton mini y los scouts salieron a revisar la red. Te aviso cuando terminen.", "", "🌐"); };
    $$("[data-tip]", p).forEach((b) => b.onclick = () => {
      const t = d.tips[+b.dataset.tip];
      if (t.route) { this.toggle(false); go(t.route); return; }
      if (t.message) this.send(t.message);
      else { $("#b-text").value = "Abre a "; $("#b-text").focus(); }
    });
    $$("[data-sub]", p).forEach((b) => b.onclick = () => { const n = b.dataset.sub; $("#b-text").value = `Abre a ${n} y hazle `; $("#b-text").focus(); this.say(`Perfecto, ${n}. ¿Qué le hacemos? ¿Fotos, un reel o un video?`, false); });
    $("#b-text", p).oninput = () => linkHint($("#b-text", p), $("#b-form", p).parentNode.querySelector(".b-linkbox") || (() => { const d = document.createElement("div"); d.className = "b-linkbox"; $("#b-form", p).before(d); return d; })());
    $("#b-form", p).onsubmit = (e) => { e.preventDefault(); const t = $("#b-text").value.trim(); if (t) this.send(t); };
    $("#b-mic", p).onclick = () => this.listen();
    $("#b-new", p).onclick = () => { this.conv = null; localStorage.removeItem("buddyConv"); $("#b-log").innerHTML = ""; this.say("Listo, abrí otra conversación en paralelo. ¿Qué modelo usamos?", false); };
    $("#b-open", p).onclick = () => { if (this.conv) { this.toggle(false); go("chat", this.conv); } else toast("Todavía no hablamos en esta conversación"); };
    $("#b-say", p).onchange = (e) => localStorage.setItem("buddySay", e.target.checked ? "1" : "0");
    this.bindLive();
    if (first) setTimeout(() => $("#b-text")?.focus(), 50);
    $("#b-log").scrollTop = 1e9;
  },
  // Métricas en vivo: qué procesos están vivos ahora mismo
  liveHTML() {
    const d = this.data || {}, act = d.activity || [], lh = d.last_hour || {}, w = S.working || d.working || [], jobs = d.jobs || [];
    const max = Math.max(1, ...act), W = 120, H = 26;
    const pts = act.map((v, i) => `${(i * W / Math.max(1, act.length - 1)).toFixed(1)},${(H - 2 - v / max * (H - 4)).toFixed(1)}`).join(" ");
    const running = jobs.filter((j) => j.status === "running"), pending = jobs.filter((j) => j.status === "pending");
    const lines = [
      ...w.map((x) => `<div class="b-line"><span class="b-dot" style="background:${agentColor(x.agent)}"></span><b>${esc(agentName(x.agent))}</b><span class="grow ellipsis muted">${esc(x.task)}</span>${x.model ? `<span class="chip">${esc(String(x.model).split("/").pop())}</span>` : ""}<span class="faint mono" data-since="${esc(x.since)}">${elapsed(x.since)}</span></div>`),
      ...running.filter((j) => !w.some((x) => x.post_id === j.post_id)).map((j) => `<div class="b-line"><span class="b-dot" style="background:var(--accent-2)"></span><b>${esc(j.kind)}</b><span class="grow ellipsis muted">${esc(S.progress.get(j.post_id) || j.idea)}</span><span class="faint mono" data-since="${esc(j.since)}">${elapsed(j.since)}</span></div>`),
    ];
    return `<details class="b-livebox" ${localStorage.getItem("buddyLive") === "0" ? "" : "open"}><summary><span class="b-pulse ${w.length || running.length ? "on" : ""}"></span><b>En vivo</b>
        <span class="muted small">${w.length} agente(s) · ${running.length} corriendo · ${pending.length} en cola</span><div class="grow"></div>
        <svg width="${W}" height="${H}" class="b-spark" viewBox="0 0 ${W} ${H}"><polyline points="${pts}" fill="none" stroke="var(--accent-2)" stroke-width="2" stroke-linejoin="round"/></svg></summary>
      <div class="b-lines">${lines.join("") || '<div class="faint small">Nadie trabajando en este momento.</div>'}</div>
      <div class="b-stats"><span>Última hora: <b>${lh.runs || 0}</b> llamadas</span><span><b>${lh.jobs_done || 0}</b> trabajos listos</span>${lh.jobs_failed || lh.run_errors ? `<span class="bad-t"><b>${(lh.jobs_failed || 0) + (lh.run_errors || 0)}</b> errores</span>` : ""}</div></details>`;
  },
  timingHTML() {
    const t = (this.data || {}).timing || {}, r = (this.data || {}).review || {};
    const next = r.at && r.every_hours ? Math.max(0, Math.round((new Date(r.at).getTime() + r.every_hours * 3600e3 - Date.now()) / 60000)) : null;
    const models = (t.models || []).map((m) => `<div class="b-line"><b>${esc(m.model)}</b><span class="chip">${esc(m.platform)}</span><span class="grow">${esc(m.best)}</span><span class="faint small" title="${esc(m.why)}">ⓘ</span></div>`).join("");
    const plats = (t.platforms || []).map((m) => `<span class="chip" title="${esc(m.why)}">${esc(m.platform)}: ${esc(m.best)}</span>`).join(" ");
    return `<details class="b-livebox"><summary><span>🕒</span><b>Mejor hora para publicar</b><span class="muted small">${t.updated ? "actualizado " + when(t.updated.replace(" ", "T")) : "pendiente"}</span></summary>
      ${t.summary ? `<div class="small">${esc(t.summary)}</div>` : '<div class="faint small">Todavía no hay análisis: sale en la próxima revisión de la red (o toca «Revisar ahora»).</div>'}
      ${plats ? `<div class="row" style="gap:6px">${plats}</div>` : ""}<div class="b-lines">${models}</div>
      <div class="row small" style="gap:8px"><span class="faint">🌐 Revisión de la red${r.at ? ` ${when(r.at)}` : ": aún no"}${next !== null ? ` · próxima en ~${next >= 60 ? Math.round(next / 60) + " h" : next + " min"}` : r.every_hours ? ` · cada ${r.every_hours} h` : " · apagada"}</span><div class="grow"></div><button class="btn sm" id="b-review">Revisar ahora</button></div>
      <div class="faint small">Mezcla tu historial (qué publicas y apruebas de cada modelo) con lo que encuentran los scouts afuera. Zona horaria: ${esc(t.timezone || "")}.</div></details>`;
  },
  renderLive() { const box = $("#b-live"); if (box && this.open) { box.innerHTML = this.liveHTML(); this.bindLive(); } },
  bindLive() { const d = $("#b-live details"); if (d) d.ontoggle = () => localStorage.setItem("buddyLive", d.open ? "1" : "0"); },
  say(text, mine) {
    const l = $("#b-log"); if (!l) return null;
    const m = document.createElement("div"); m.className = "b-msg " + (mine ? "me" : "bot"); m.textContent = text; l.append(m); l.scrollTop = 1e9; return m;
  },
  async listen() {
    const mic = $("#b-mic");
    const r = await Voice.listen((st, lvl) => { this.mood(st, lvl); mic?.classList.toggle("rec", st === "listening"); if (mic) mic.style.setProperty("--lvl", lvl); });
    mic?.classList.remove("rec"); this.mood("");
    if (!r) return;
    if (!r.clear) { this.say(r.message || "No te entendí muy bien. ¿Lo repites?", false); if ($("#b-say")?.checked) Voice.say(r.message); return; }
    this.send(r.text, true);
  },
  inflight: 0,
  async send(text, spoken = false) {
    // Si ya estoy con otro pedido, este va en paralelo en su propia conversación (no hay que esperar)
    const parallel = this.inflight > 0;
    this.inflight++; $("#b-text") && ($("#b-text").value = ""); $(".b-linkbox")?.remove();
    this.say(text, true); const status = this.say(parallel ? "Lo hago en paralelo…" : "Pensando…", false); status.classList.add("live");
    this.mood("thinking");
    let final = "", ids = [], conv = parallel ? null : this.conv;
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conv, agent: "shelton", engine: "" }) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!chunk.startsWith("data: ")) continue;
          const ev = JSON.parse(chunk.slice(6));
          if (ev.type === "conversation") { conv = ev.id; if (!parallel) { this.conv = ev.id; localStorage.setItem("buddyConv", ev.id); } }
          else if (ev.type === "status" && status) status.textContent = ev.text.slice(0, 160);
          else if (ev.type === "posts") ids.push(...ev.ids);
          else if (ev.type === "message") { final = ev.text; ids.push(...(ev.post_ids || [])); }
          else if (ev.type === "error") final = "⚠ " + ev.text;
        }
      }
    } catch (e) { final = "⚠ " + e.message; }
    status.classList.remove("live"); status.textContent = final || "Listo.";
    const uniq = [...new Set(ids)];
    if (uniq.length) { const t = thumbs(uniq.slice(0, 6)); t.classList.add("b-thumbs"); $("#b-log")?.append(t); }
    if (parallel && conv) { const a = document.createElement("a"); a.href = `#/chat/${conv}`; a.className = "small"; a.textContent = "Abrir esa conversación →"; $("#b-log")?.append(a); }
    if (spoken && $("#b-say")?.checked !== false && final) Voice.say(final);
    this.mood("happy"); setTimeout(() => this.mood(""), 1200);
    this.inflight--; this.refresh();
  },
};
setTimeout(() => Bot.mount(), 600);

// ------------------------------------------------------------------ AUDIOS (biblioteca)
VIEWS.audios = async (el) => {
  const list = await api("/api/audio");
  el.innerHTML = pageHead("🎵", "Audios", "Sube WAV o MP3 (canciones, voces, efectos) o guarda el audio de cualquier video. Shelton se los pone a tus posts y reels.",
    `<label class="btn primary">${icon("plus", 17)} Subir audio<input type="file" id="au-up" accept="audio/*,.wav,.mp3,.m4a,video/*" multiple hidden></label>`) +
    `<div class="hint" style="margin-bottom:12px">Arrastra archivos aquí. Si subes un video, se guarda solo su audio. Pídele a Shelton: «ponle el audio <i>nombre</i> al reel de Valentina».</div>
    <div class="grid" style="gap:10px" id="au-list">${list.map((a) => `<div class="card au-row"><div class="au-ico">🎵</div><div class="grow"><input class="au-name" data-ren="${a.id}" value="${esc(a.name)}">
      <div class="faint small">${Math.round(a.duration)} s · ${esc(a.source)} · ${when(a.created.replace(" ", "T"))}${a.origin && /^https?:/.test(a.origin) ? ` · <a href="${esc(a.origin)}" target="_blank" rel="noopener">origen</a>` : ""}</div></div>
      <audio controls preload="none" src="${esc(a.url)}"></audio>
      <button class="btn sm" data-useau="${esc(a.name)}" title="Usar en un post">Usar</button><a class="btn sm ghost" href="${esc(a.url)}" download title="Descargar">${icon("download", 15)}</a>
      <button class="btn sm ghost" data-delau="${a.id}" title="Eliminar">${icon("trash", 15)}</button></div>`).join("") || `<div class="card">${empty("🎵", "Aún no hay audios. Sube un WAV o MP3, o guarda el audio de un video desde su ficha.")}</div>`}</div>`;
  const upload = async (files) => { for (const f of files) { const fd = new FormData(); fd.append("file", f); try { const a = await api("/api/audio", { method: "POST", body: fd }); toast(`Guardado: <b>${esc(a.name)}</b> (${Math.round(a.duration)} s)`, "good", "🎵"); } catch (e) { toast(esc(e.message), "bad"); } } VIEWS.audios(el); };
  $("#au-up").onchange = (e) => upload([...e.target.files]);
  el.ondragover = (e) => e.preventDefault(); el.ondrop = (e) => { e.preventDefault(); upload([...e.dataTransfer.files]); };
  $$("[data-ren]").forEach((i) => i.onchange = () => api(`/api/audio/${i.dataset.ren}`, { method: "PATCH", body: { name: i.value } }).then(() => toast("Nombre guardado", "good")));
  $$("[data-delau]").forEach((b) => b.onclick = async () => { if (confirm("¿Eliminar este audio de la biblioteca?")) { await api(`/api/audio/${b.dataset.delau}`, { method: "DELETE" }); VIEWS.audios(el); } });
  $$("[data-useau]").forEach((b) => b.onclick = () => { S.chatAgent = "shelton"; go("chat"); setTimeout(() => { $("#input").value = `Ponle el audio «${b.dataset.useau}» al último video `; $("#input").focus(); }, 300); });
};

// ------------------------------------------------------------------ COPIAR VIDEOS (referencias de TikTok / Reels)
VIEWS.referencias = async (el) => {
  const [refs, subs] = await Promise.all([api("/api/references"), api("/api/subjects")]);
  el.innerHTML = pageHead("🎞️", "Copiar videos", "Pega el link de un TikTok, Reel o Short (o sube el video). El equipo lo desarma escena por escena y lo recrea con tu modelo: mismo fondo, mismo movimiento, otra cara.") +
    `<form class="card row" id="rf"><input id="r-url" class="grow" placeholder="https://www.tiktok.com/@…/video/…  ·  https://www.instagram.com/reel/…" style="min-width:260px">
      <button class="btn primary" id="r-go">${icon("search", 16)} Analizar</button><label class="btn">${icon("clip", 16)} Subir video<input type="file" id="r-file" accept="video/*" hidden></label></form>
    <div class="hint" style="margin:10px 0 16px">Solo reemplaza personas por modelos de tu banco (con permiso). Si el audio es música de otra persona, revisa los derechos antes de publicarlo fuera de esa red.</div>
    <div class="grid" style="gap:16px">${refs.map((r) => refCard(r, subs)).join("") || `<div class="card">${empty("🎞️", "Pega un link arriba para empezar.")}</div>`}</div>`;
  const busy = (t) => { $("#r-go").disabled = true; $("#r-go").innerHTML = `<span class="spinner sm"></span> ${t}`; };
  $("#rf").onsubmit = async (e) => { e.preventDefault(); const u = $("#r-url").value.trim(); if (!u) return; busy("Bajando y analizando…"); try { await api("/api/references", { method: "POST", body: { url: u } }); toast("Listo: video analizado", "good", "🎞️"); } catch (err) { toast(esc(err.message), "bad"); } VIEWS.referencias(el); };
  $("#r-file").onchange = async (e) => { const f = e.target.files[0]; if (!f) return; busy("Analizando…"); const fd = new FormData(); fd.append("file", f); try { await api("/api/references/upload", { method: "POST", body: fd }); } catch (err) { toast(esc(err.message), "bad"); } VIEWS.referencias(el); };
  $$("[data-rec]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.rec, box = b.closest(".ref-card");
    const subjects = $$("input[name=rs]:checked", box).map((i) => i.value);
    b.disabled = true; b.textContent = "Creando…";
    try {
      const r = await api(`/api/references/${id}/recreate`, { method: "POST", body: { subjects, mode: $("[name=rm]", box).value, changes: $("[name=rc]", box).value, keep_audio: $("[name=ra]", box)?.checked ?? false } });
      toast(`En marcha: ${r.scene_ids.length} escena(s) → ${r.kind === "reel" ? "reel" : "video"}${r.audio ? " con el audio original" : ""}. Lo ves en la Galería.`, "good", "🎬", `<button class="btn sm" onclick="openPost('${r.target_id}')">Ver</button>`);
    } catch (e) { toast(esc(e.message), "bad"); }
    b.disabled = false; b.textContent = "Recrear con mi modelo";
  });
};
function refCard(r, subs) {
  const a = r.analysis || {};
  return `<div class="card ref-card"><div class="ref-grid"><video src="${esc(r.video_url)}" controls preload="metadata" playsinline></video>
    <div class="col" style="gap:8px"><div class="row" style="gap:8px"><b>${esc(r.title || "Video de referencia")}</b><span class="chip">${Math.round(r.duration)} s</span>${r.audio_id ? '<span class="chip accent">🎵 audio guardado</span>' : ""}${r.url ? `<a class="small" href="${esc(r.url)}" target="_blank" rel="noopener">abrir original</a>` : ""}</div>
      <div>${esc(a.summary || "")}</div>${a.hook ? `<div class="small"><b>Gancho:</b> ${esc(a.hook)}</div>` : ""}${a.style ? `<div class="small muted">Estilo: ${esc(a.style)}</div>` : ""}
      <div class="refs">${(r.frame_post_ids || []).map((pid, i) => `<img src="/media/${esc((r.frames[i] || "").split("/").pop())}" onclick="openPost('${pid}')" title="Escena ${i + 1}">`).join("")}</div>
      <div class="faint small">Reemplazar por:</div><div class="checks">${subs.map((s) => `<label><input type="checkbox" name="rs" value="${esc(s.name)}"> ${esc(s.name)}</label>`).join("") || '<span class="faint small">Crea un modelo en el Banco primero.</span>'}</div>
      <div class="row" style="gap:8px"><select name="rm"><option value="same">Igual (mismo fondo y movimiento)</option><option value="similar">Algo similar (versión original)</option></select>
        <input name="rc" class="grow" placeholder="Cambios: ropa roja, de noche, en Miami…">${r.audio_id ? '<label class="small"><input type="checkbox" name="ra" checked> Audio original</label>' : ""}</div>
      <div><button class="btn primary" data-rec="${r.id}">Recrear con mi modelo</button></div></div></div></div>`;
}

// ------------------------------------------------------------------ Ficha del post: audio y descarga
const _openPost = openPost;
window.openPost = openPost = async function (id, ...rest) {
  await _openPost(id, ...rest);
  const p = S.posts.get(id), d = $("#drawer"); if (!p || !d || $("#d-extra", d)) return;
  const sec = document.createElement("div"); sec.className = "d-sec"; sec.id = "d-extra";
  const lib = await api("/api/audio").catch(() => []);
  sec.innerHTML = `<h4>Audio de la biblioteca y exportar</h4>
    <div class="row" style="gap:8px"><select id="dx-au" class="grow"><option value="">Elige un audio (WAV/MP3)…</option>${lib.map((a) => `<option value="${a.id}">${esc(a.name)} · ${Math.round(a.duration)} s</option>`).join("")}</select>
    <select id="dx-mode"><option value="replace">Reemplazar audio</option><option value="mix">De fondo (mezcla)</option></select><button class="btn sm" id="dx-put">🎵 Poner</button></div>
    <div class="row" style="gap:8px;margin-top:8px">${p.video_url ? '<button class="btn sm" id="dx-save">💾 Guardar su audio en la biblioteca</button>' : ""}
    <a class="btn sm" href="/api/posts/${id}/download">${icon("download", 15)} Descargar archivo</a><a class="btn sm ghost" href="/api/export?ids=${id}">ZIP con texto</a></div>`;
  const del = $("#d-del", d); (del ? del.parentNode : d).insertBefore(sec, del || null);
  $("#dx-put").onclick = async () => { const au = $("#dx-au").value; if (!au) return toast("Elige un audio", "bad"); try { const r = await api(`/api/posts/${id}/audio-track`, { method: "POST", body: { audio_id: au, mode: $("#dx-mode").value } }); toast("Poniendo el audio… (versión nueva; la original se conserva)", "good", "🎵"); openPost(r.id); } catch (e) { toast(esc(e.message), "bad"); } };
  if ($("#dx-save")) $("#dx-save").onclick = async () => { try { const a = await api(`/api/posts/${id}/save-audio`, { method: "POST", body: {} }); toast(`Audio guardado: <b>${esc(a.name)}</b>`, "good", "🎵", `<button class="btn sm" onclick="go('audios')">Ver audios</button>`); } catch (e) { toast(esc(e.message), "bad"); } };
};

// ------------------------------------------------------------------ Banco: memoria de cada modelo y exportar
const _banco = VIEWS.banco;
VIEWS.banco = async (el) => {
  await _banco(el);
  $$(".subject-card").forEach((card) => {
    const sid = $("[data-del]", card)?.dataset.del, name = $("b", card)?.textContent || "";
    if (!sid) return;
    const row = $("[data-del]", card).parentNode;
    const mem = document.createElement("button"); mem.className = "btn sm"; mem.textContent = "🧠 Memoria"; mem.title = "Lo que el equipo aprendió de este modelo (para no repetir errores)";
    mem.onclick = () => subjectMemory(sid, name);
    const ex = document.createElement("a"); ex.className = "btn sm ghost"; ex.href = `/api/subjects/${sid}/export`; ex.title = "Exportar modelo con fotos, voz, memoria y contenido"; ex.innerHTML = icon("download", 15);
    row.insertBefore(mem, row.lastElementChild); row.insertBefore(ex, row.lastElementChild);
  });
};
async function subjectMemory(sid, name) {
  const key = `subject_${sid}`;
  const render = async () => {
    const list = await api(`/api/training/${key}`);
    const box = modal(`🧠 Memoria de ${esc(name)}`, `<div class="hint">Cada corrección queda aquí y se aplica <b>cada vez</b> que se use ${esc(name)}: así no se repite el mismo error. Shelton también se la pasa al equipo.</div>
      ${list.map((e) => `<div class="card" style="padding:12px"><div class="row" style="gap:8px"><b>${esc(e.title)}</b><span class="chip ${e.enabled ? "good" : "warn"}">${e.enabled ? "Activa" : e.status}</span><span class="faint small">${esc(e.created)}</span><div class="grow"></div><button class="btn sm ghost" data-ldel="${e.id}">${icon("trash", 14)}</button></div><div class="muted small" style="margin-top:6px;white-space:pre-wrap">${esc(e.content)}</div></div>`).join("") || `<div class="faint">Sin lecciones todavía.</div>`}
      <label class="f">Nueva lección<input id="sl-t" placeholder="Ej: El lunar va en la mejilla izquierda"></label><textarea id="sl-c" rows="3" placeholder="Detalle: siempre visible, pequeño, marrón claro…"></textarea>`,
      `<button class="btn ghost" onclick="closeModal()">Cerrar</button><button class="btn primary" id="sl-go">Guardar lección</button>`);
    $("#sl-go", box).onclick = async () => { const t = $("#sl-t").value.trim(), c = $("#sl-c").value.trim() || t; if (!t) return toast("Escribe la lección", "bad"); await api(`/api/training/${key}`, { method: "POST", body: { title: t, content: c, author: "Equipo", role: "admin" } }); render(); };
    $$("[data-ldel]", box).forEach((b) => b.onclick = async () => { await api(`/api/training/${key}/${b.dataset.ldel}`, { method: "DELETE" }); render(); });
  };
  render();
}

// ------------------------------------------------------------------ Ajustes: exportar todo
const _ajustes = VIEWS.ajustes;
VIEWS.ajustes = async (el) => {
  await _ajustes(el);
  const c = document.createElement("div"); c.className = "card col"; c.style.marginTop = "16px";
  c.innerHTML = `<h3>Exportar todo</h3><div class="hint">Descarga un ZIP con <b>todo</b>: imágenes y videos con sus textos, el banco de modelos (fotos, voz y ficha), los audios, las voces, la memoria de cada agente y de cada modelo, los videos de referencia y un respaldo para restaurar en otra máquina. Las claves (.env) no se incluyen.</div>
    <div class="row"><a class="btn primary" href="/api/export/all">${icon("download", 16)} Exportar todo</a><a class="btn" href="/api/export?status=approved,published">Solo aprobados y publicados</a></div>`;
  el.append(c);
};
