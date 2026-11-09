// Shelton Studio — Archivos de memoria: todo lo que aprenden y hacen Shelton y sus subagentes, en .md.
"use strict";

(function addArchivosNav() {
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "memoria");
  NAV.splice(i + 1, 0, ["archivos", "download", "Archivos"]);
  renderRail(); markNav();
  if (S.wl && S.wl.product_name) applyWL();
})();

const KIND_CHIP = { "lección": ["Lección", "good"], "archivada": ["Archivada", "warn"], "bitácora": ["Bitácora", "info"],
  "conversación": ["Conversación", "accent"], "revisión": ["Revisión", ""] };

VIEWS.archivos = async (el) => {
  const folders = await api("/api/memory/files");
  const total = folders.reduce((n, f) => n + f.count, 0);
  const groups = {};
  folders.forEach((f) => (groups[f.group] = groups[f.group] || []).push(f));
  el.innerHTML = pageHead("🗂️", "Archivos de memoria", "Todo lo que Shelton y cada subagente aprenden y hacen, guardado en archivos .md. Nada se borra: lo viejo queda archivado. Shelton los relee solo al empezar cada sesión.",
    `<a class="btn primary" href="/api/memory/zip">${icon("download", 16)} Descargar todo (ZIP)</a>`) +
    `<div class="row" style="gap:8px;margin-bottom:14px"><input id="ar-q" class="grow" placeholder="Buscar en los archivos (nombre o carpeta)…"><span class="faint small">${total} archivos</span></div>` +
    (Object.entries(groups).map(([g, list]) => `<h3 style="margin:18px 0 8px">${esc(g)}</h3><div class="grid" style="gap:10px">${list.map((f) => `
      <details class="card ar-folder" data-name="${esc((f.name + " " + f.key).toLowerCase())}"><summary class="row" style="gap:10px;cursor:pointer">
        <b class="grow">${esc(f.name)}</b><span class="chip">${f.count} archivos</span>
        <a class="btn sm ghost" href="/api/memory/zip?folder=${encodeURIComponent(f.key)}" onclick="event.stopPropagation()">${icon("download", 14)} ZIP</a></summary>
        <div class="ar-files">${f.files.map((x) => `<div class="ar-file" data-path="${esc(x.path)}"><span class="chip ${KIND_CHIP[x.kind]?.[1] || ""}">${KIND_CHIP[x.kind]?.[0] || esc(x.kind)}</span>
          <span class="grow mono small ellipsis" title="${esc(x.path)}">${esc(x.name)}</span><span class="faint small">${when(x.modified)}</span>
          <button class="btn sm ghost" data-view="${esc(x.path)}">Ver</button>
          <a class="btn sm ghost" href="/api/memory/file?download=true&path=${encodeURIComponent(x.path)}">${icon("download", 14)}</a>
          ${x.kind === "archivada" ? `<button class="btn sm" data-restore="${esc(x.path)}">Reactivar</button>` : ""}</div>`).join("")}</div></details>`).join("")}</div>`).join("")
      || `<div class="card">${empty("🗂️", "Todavía no hay archivos. Aparecen solos a medida que el equipo trabaja y aprende.")}</div>`);
  $("#ar-q").oninput = (e) => { const q = e.target.value.toLowerCase(); $$(".ar-folder").forEach((d) => { const hit = !q || d.dataset.name.includes(q) || $$(".ar-file", d).some((f) => f.dataset.path.toLowerCase().includes(q)); d.style.display = hit ? "" : "none"; if (q && hit) d.open = true; }); };
  $$("[data-view]").forEach((b) => b.onclick = async () => {
    const r = await fetch(`/api/memory/file?path=${encodeURIComponent(b.dataset.view)}`);
    const txt = r.ok ? await r.text() : "No se pudo abrir";
    modal(`📄 ${esc(b.dataset.view.split("/").pop())}`, `<div class="faint small mono">${esc(b.dataset.view)}</div><pre class="ar-pre">${esc(txt)}</pre>`,
      `<a class="btn" href="/api/memory/file?download=true&path=${encodeURIComponent(b.dataset.view)}">${icon("download", 15)} Descargar .md</a><button class="btn primary" onclick="closeModal()">Cerrar</button>`, true);
  });
  $$("[data-restore]").forEach((b) => b.onclick = async () => {
    const parts = b.dataset.restore.split("/"), agent = parts[0], id = parts[parts.length - 1].replace(/\.md$/, "");
    try { await api(`/api/training/${encodeURIComponent(agent)}/${encodeURIComponent(id)}/restore`, { method: "POST" }); toast("Lección reactivada: se vuelve a aplicar", "good", "🧠"); VIEWS.archivos(el); }
    catch (e) { toast(esc(e.message), "bad"); }
  });
};
