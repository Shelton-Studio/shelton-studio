// Shelton Studio — Consumo: de dónde salen los costos y cuánto se gasta (nube y GPU local), en vivo.
"use strict";

ICONS.coin = '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.6-3-1.6-1.8 0-3 .9-3 2.1 0 2.9 6 1.4 6 4.2 0 1.3-1.3 2.2-3 2.2-1.5 0-2.7-.7-3.1-1.7M12 6.5v1.4M12 16.4v1.4"/>';
(function addConsumoNav() {
  const i = NAV.findIndex((n) => Array.isArray(n) && n[0] === "actividad");
  NAV.splice(i + 1, 0, ["consumo", "coin", "Consumo"]);
  renderRail(); markNav();
  if (S.wl && S.wl.product_name) applyWL();
})();

// Series del gráfico (orden fijo; color validado para fondo oscuro)
const CS_SERIES = [
  ["cerebro", "Cerebro (texto)", "#3987e5"], ["imagen", "Imágenes", "#d95926"], ["video", "Video", "#199e70"],
  ["audio", "Voz y audio", "#c98500"], ["luz", "Luz de la GPU", "#d55181"],
];
const csKey = (k) => (k === "voz" || k === "transcripcion" || k === "sonido") ? "audio" : k;
const usd = (n, d) => {
  if (n == null) return "—";
  const v = Number(n);
  const dec = d != null ? d : (v !== 0 && Math.abs(v) < 0.01 ? 4 : 2);
  return "US$ " + v.toLocaleString("es", { minimumFractionDigits: dec, maximumFractionDigits: dec });
};
const num = (n) => Number(n || 0).toLocaleString("es");
const dur = (s) => { s = Math.round(s || 0); if (s < 60) return `${s} s`; if (s < 3600) return `${Math.round(s / 60)} min`; return `${(s / 3600).toFixed(1)} h`; };
const SRC = { real: ["Real", "good", "Lo informó el proveedor (OpenRouter)"], estimado: ["Estimado", "warn", "El proveedor no informa el costo: se calcula con su lista de precios"], local: ["Luz", "info", "GPU local: no se paga por uso, solo la luz"] };
const srcChip = (s) => { const x = SRC[s] || [s, ""]; return `<span class="chip ${x[1]}" title="${esc(x[2] || "")}">${x[0]}</span>`; };

let csTimer = null;
window.addEventListener("hashchange", () => { if (!location.hash.startsWith("#/consumo") && csTimer) { clearInterval(csTimer); csTimer = null; } });

VIEWS.consumo = async (el) => {
  let days = 7;
  try { days = +localStorage.getItem("csDays") || 7; } catch {}
  el.innerHTML = pageHead("💰", "Consumo", "De dónde sale cada costo y cuánto se gasta: el cerebro, las imágenes, el video, la voz y la luz de la GPU. Se actualiza en vivo.",
    `<div class="seg" id="cs-days">${[[1, "Hoy"], [7, "7 días"], [30, "30 días"], [90, "90 días"]].map(([d, t]) => `<button data-d="${d}" class="${d === days ? "on" : ""}">${t}</button>`).join("")}</div>
     <a class="btn" id="cs-csv" href="/api/consumo/csv?days=${days}">${icon("download", 15)} Exportar (Excel/CSV)</a>`) +
    `<div id="cs-gpu"></div><div id="cs-ahorro"></div><div id="cs-body"><div class="card">${empty("⏳", "Cargando el consumo…")}</div></div>`;
  $$("#cs-days button").forEach((b) => b.onclick = () => { try { localStorage.setItem("csDays", b.dataset.d); } catch {} VIEWS.consumo(el); });
  const loadGpu = async () => { try { renderGpu($("#cs-gpu"), await api("/api/consumo/gpu")); } catch {} };
  const loadAhorro = async () => { try { renderAhorro($("#cs-ahorro"), await api(`/api/consumo/ahorro?days=${Math.max(days, 7)}`), () => loadAhorro()); } catch {} };
  await Promise.all([loadGpu(), loadAhorro(), (async () => { try { renderConsumo($("#cs-body"), await api(`/api/consumo?days=${days}`), days); } catch (e) { $("#cs-body").innerHTML = `<div class="card">${empty("⚠️", esc(e.message))}</div>`; } })()]);
  if (csTimer) clearInterval(csTimer);
  let n = 0;
  csTimer = setInterval(async () => {
    if (!document.getElementById("cs-gpu")) { clearInterval(csTimer); csTimer = null; return; }
    loadGpu();
    if (++n % 6 === 0) { try { renderConsumo($("#cs-body"), await api(`/api/consumo?days=${days}`), days, true); } catch {} }
  }, 5000);
};

function renderGpu(box, g) {
  if (!box) return;
  const work = (g.working || []).length ? `<div class="cs-working">${g.working.map((w) => `<span class="chip accent">● ${esc(w.agent)}${w.what ? ": " + esc(w.what.slice(0, 70)) : ""}</span>`).join("")}</div>` : `<div class="faint small">Nadie del equipo está trabajando ahora.</div>`;
  if (!g.gpus || !g.gpus.length) {
    box.innerHTML = `<div class="card cs-gpu"><div class="row" style="gap:10px"><b>🖥️ GPU en vivo</b><span class="chip">Sin datos</span></div>
      <div class="faint small" style="margin-top:6px">No encontré la tarjeta gráfica desde aquí (Shelton corre en una máquina sin GPU o ComfyUI no responde). El consumo de la nube se sigue midiendo abajo.</div>${work}</div>`;
    return;
  }
  box.innerHTML = `<div class="card cs-gpu"><div class="row" style="gap:10px;flex-wrap:wrap"><b>🖥️ GPU en vivo</b><span class="chip good">● cada 5 s</span>
      <span class="faint small grow">${g.source === "nvidia-smi" ? "Medido en la placa con nvidia-smi" : "Lo que informa ComfyUI (sin vatios)"}</span></div>
    ${g.gpus.map((x) => {
      const memPct = x.mem_total_mb ? Math.round(x.mem_used_mb / x.mem_total_mb * 100) : 0;
      const kwhHour = x.power_w != null ? (x.power_w / 1000) * g.kwh_price : null;
      const who = Object.entries(x.who || {}).sort((a, b) => b[1] - a[1]);
      return `<div class="cs-gpu-row">
        <div class="cs-gpu-name"><b>${esc(x.name)}</b>${x.remote ? `<div class="faint small">${esc(x.remote)}</div>` : ""}</div>
        <div class="cs-meter"><span>Uso</span><div class="cs-track"><i style="width:${x.util ?? 0}%"></i></div><b>${x.util != null ? Math.round(x.util) + "%" : "—"}</b></div>
        <div class="cs-meter"><span>Memoria</span><div class="cs-track"><i style="width:${memPct}%"></i></div><b>${(x.mem_used_mb / 1024).toFixed(1)} / ${(x.mem_total_mb / 1024).toFixed(0)} GB</b></div>
        <div class="cs-mini"><div><span>Potencia</span><b>${x.power_w != null ? Math.round(x.power_w) + " W" : "—"}</b>${x.power_limit_w ? `<small>de ${Math.round(x.power_limit_w)} W</small>` : ""}</div>
          <div><span>Temperatura</span><b>${x.temp_c != null ? Math.round(x.temp_c) + " °C" : "—"}</b></div>
          <div><span>Luz por hora ahora</span><b>${kwhHour != null ? usd(kwhHour) : "—"}</b></div></div>
        <div class="cs-who">${who.length ? who.map(([k, mb]) => `<div class="row" style="gap:8px"><span class="grow small">${esc(k)}</span><span class="faint small">${(mb / 1024).toFixed(1)} GB</span></div>`).join("") : `<div class="faint small">Ningún programa usando la memoria de la GPU.</div>`}</div>
      </div>`;
    }).join("")}${work}</div>`;
}

function renderConsumo(box, d, days, quiet) {
  if (!box) return;
  const t = d.totals, or = d.openrouter, gpu = d.gpu;
  const period = days === 1 ? "hoy" : `los últimos ${days} días`;
  const orTile = or && or.ok ? `<div class="card kpi"><div class="k-head"><span class="k-label">Cuenta OpenRouter</span></div>
      <div class="k-value">${usd(days === 1 ? or.usage_today : days <= 7 ? or.usage_week : or.usage_month)}</div>
      <div class="k-sub">Lo que OpenRouter cobró ${days === 1 ? "hoy" : days <= 7 ? "esta semana" : "este mes"} (dato oficial).<br>
      ${or.limit != null ? `Tope ${usd(or.limit)} · quedan <b>${usd(or.limit_remaining)}</b>` : "Sin tope configurado"}</div></div>`
    : `<div class="card kpi"><div class="k-head"><span class="k-label">Cuenta OpenRouter</span></div><div class="k-value">—</div><div class="k-sub">${or ? "No pude leer la cuenta: " + esc(or.error || "") : "No hay clave de OpenRouter."}</div></div>`;
  box.innerHTML = `
    <div class="grid g4" style="margin-top:16px">
      <div class="card kpi"><div class="k-head"><span class="k-label">Gasto total · ${esc(period)}</span></div><div class="k-value">${usd(t.total)}</div>
        <div class="k-sub">Nube ${usd(t.cloud)} + luz de la GPU ${usd(t.gpu_light)}<br>Si sigue así: <b>${usd(t.projection_month)}</b> al mes</div></div>
      <div class="card kpi"><div class="k-head"><span class="k-label">Nube (APIs)</span></div><div class="k-value">${usd(t.cloud)}</div>
        <div class="k-sub">${srcChip("real")} ${usd(t.cloud_real)} informado por el proveedor<br>${srcChip("estimado")} ${usd(t.cloud_estimated)} calculado con su lista de precios</div></div>
      <div class="card kpi"><div class="k-head"><span class="k-label">Luz de la GPU</span></div><div class="k-value">${gpu.measured ? usd(gpu.cost) : "—"}</div>
        <div class="k-sub">${gpu.measured ? `${num(gpu.energy_kwh)} kWh · promedio ${gpu.avg_power_w} W · trabajando ${gpu.busy_hours} h de ${gpu.measured_hours} h medidas` : "Empieza a medirse cuando Shelton corre en la placa con la GPU."}<br>
        Precio del kWh: <button class="linkish" id="cs-kwh">${usd(gpu.kwh_price, 3)} ✎</button></div></div>
      ${orTile}
    </div>
    <div class="card" style="margin-top:16px"><div class="row" style="gap:10px;flex-wrap:wrap"><b>Gasto por día</b><span class="faint small grow">Nube por tipo + luz de la GPU. Pasa el mouse por una barra para ver el detalle.</span>
      <div class="cs-legend">${CS_SERIES.map(([, l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join("")}</div></div>
      ${chartHTML(d.daily)}</div>
    <div class="grid g2" style="margin-top:16px">
      ${tableCard("¿En qué se gasta?", "Por tipo de trabajo", d.by_kind.map((g) => ({ ...g, name: g.label })), true)}
      ${tableCard("¿Quién cobra?", "Por proveedor", d.by_provider, true)}
      ${tableCard("¿Qué modelo?", "Por modelo de IA", d.by_model, true)}
      ${tableCard("¿Qué agente?", "Por agente del equipo (cerebro)", d.by_agent.map((g) => ({ ...g, name: g.label })), true)}
    </div>
    ${gpu.who && gpu.who.length ? `<div class="card" style="margin-top:16px"><b>¿Quién ocupó la GPU?</b><div class="faint small" style="margin:2px 0 10px">Parte de la memoria de la GPU que usó cada programa en ${esc(period)} (sirve para ver si otro programa de la placa también la usa).</div>
      ${gpu.who.map((w) => `<div class="hbar" style="grid-template-columns:240px 1fr 54px"><span class="ellipsis" title="${esc(w.name)}">${esc(w.name)}</span><div class="cs-track"><i style="width:${w.share}%"></i></div><b>${w.share}%</b></div>`).join("")}</div>` : ""}
    ${d.top.length ? `<div class="card" style="margin-top:16px"><b>Lo más caro de ${esc(period)}</b>${eventsTable(d.top)}</div>` : ""}
    <div class="card" style="margin-top:16px"><div class="row"><b class="grow">Últimos consumos</b><span class="faint small">${num(t.events)} en ${esc(period)}${t.failed ? ` · ${t.failed} fallaron` : ""}</span></div>
      ${d.recent.length ? eventsTable(d.recent) : empty("🧾", "Todavía no hay consumos registrados. Aparecen solos con cada mensaje, foto, video o voz.")}</div>
    <details class="card" style="margin-top:16px"><summary style="cursor:pointer"><b>¿Cómo se calcula?</b></summary><div class="small muted" style="line-height:1.7;margin-top:8px">
      • <b>Real</b>: OpenRouter informa en cada respuesta cuánto costó exactamente; eso se guarda tal cual. El recuadro «Cuenta OpenRouter» es el total oficial que muestra OpenRouter.<br>
      • <b>Estimado</b>: algunos servicios (fal.ai, Replicate, OpenAI, Gemini, ElevenLabs, voz de OpenRouter) no informan el costo en la respuesta; se calcula con su lista de precios. Puede diferir un poco de la factura.<br>
      • <b>Luz</b>: la GPU de la placa no cobra por uso, solo gasta electricidad. Cada 30 s se miden los vatios con nvidia-smi y se multiplican por el precio del kWh (edítalo arriba con ✎).<br>
      • Las llamadas que fallan no se cobran (salvo que el proveedor diga lo contrario).<br>
      • Nada se borra: puedes exportar todo el historial a Excel con «Exportar».${d.tracking_since ? `<br>• Registro detallado desde: ${esc(when(d.tracking_since))}.` : ""}</div></details>`;
  $("#cs-kwh", box)?.addEventListener("click", async () => {
    const v = prompt("Precio del kWh de luz en US$ (por ejemplo 0.15):", gpu.kwh_price);
    if (v == null) return;
    const n = parseFloat(String(v).replace(",", "."));
    if (!(n >= 0)) { toast("Ese número no sirve", "bad"); return; }
    await api("/api/consumo/ajustes", { method: "POST", body: { kwh_price: n } });
    toast("Precio de la luz guardado", "good", "💡"); route();
  });
  bindChart(box);
  if (!quiet) {
    const csv = $("#cs-csv"); if (csv) csv.href = `/api/consumo/csv?days=${days}`;
  }
}

function chartHTML(daily) {
  const rows = daily.map((day) => {
    const v = { cerebro: 0, imagen: 0, video: 0, audio: 0, luz: day.gpu_cost || 0 };
    Object.entries(day.by_kind || {}).forEach(([k, c]) => { const kk = csKey(k); if (kk in v) v[kk] += c; });
    return { date: day.date, v, total: Object.values(v).reduce((a, b) => a + b, 0), kwh: day.gpu_kwh };
  });
  const max = Math.max(...rows.map((r) => r.total), 0);
  if (!max) return `<div class="faint small" style="padding:22px 0">Sin gastos en este período todavía.</div>`;
  const nice = niceMax(max);
  const fmtDay = (iso) => { const d = new Date(iso + "T12:00:00"); return d.toLocaleDateString("es", rows.length > 14 ? { day: "numeric", month: "numeric" } : { weekday: "short", day: "numeric" }); };
  const step = rows.length > 31 ? 7 : rows.length > 14 ? 3 : 1;
  return `<div class="cs-chart" role="img" aria-label="Gasto por día">
    <div class="cs-axis">${[nice, nice / 2, 0].map((y) => `<span>${usd(y, nice < 0.1 ? 3 : 2)}</span>`).join("")}</div>
    <div class="cs-plot"><div class="cs-grid"><i></i><i></i><i></i></div>
      <div class="cs-bars">${rows.map((r, i) => `<div class="cs-col" data-i="${i}" data-tip-html="${esc(tipHTML(r, fmtDay(r.date)))}">
        <div class="cs-stack" style="height:${(r.total / nice * 100).toFixed(2)}%">${CS_SERIES.slice().reverse().map(([k, , c]) => r.v[k] > 0 ? `<i style="background:${c};flex:${r.v[k]}"></i>` : "").join("")}</div>
        <span class="cs-x">${i % step === 0 ? esc(fmtDay(r.date)) : ""}</span></div>`).join("")}</div></div></div>`;
}
function niceMax(m) { const p = Math.pow(10, Math.floor(Math.log10(m))); const n = m / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
function tipHTML(r, label) {
  return `<b>${label}</b><div class="cs-tip-total">${usd(r.total)}</div>` + CS_SERIES.filter(([k]) => r.v[k] > 0).map(([k, l, c]) => `<div class="cs-tip-row"><i style="background:${c}"></i><span>${l}</span><b>${usd(r.v[k])}</b></div>`).join("") + (r.kwh ? `<div class="faint small">${r.kwh} kWh de luz</div>` : "");
}
function bindChart(box) {
  $$(".cs-col", box).forEach((c) => {
    c.onmouseenter = () => {
      $("#cs-tip")?.remove();
      const t = document.createElement("div"); t.id = "cs-tip"; t.className = "cs-tip"; t.innerHTML = c.dataset.tipHtml; document.body.append(t);
      const r = c.getBoundingClientRect(), w = t.offsetWidth;
      t.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, r.left + r.width / 2 - w / 2)) + "px";
      t.style.top = Math.max(8, r.top - t.offsetHeight - 8) + "px";
      c.classList.add("hover");
    };
    c.onmouseleave = () => { $("#cs-tip")?.remove(); c.classList.remove("hover"); };
  });
}

function tableCard(title, sub, rows, withBars) {
  const max = Math.max(...rows.map((r) => r.cost), 0) || 1;
  return `<div class="card"><b>${esc(title)}</b><div class="faint small" style="margin-bottom:8px">${esc(sub)}</div>
    ${rows.length ? `<table class="cs-table"><thead><tr><th>Nombre</th><th class="r">Veces</th><th class="r">Tokens</th><th class="r">Tiempo</th><th class="r">Costo</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td><div class="ellipsis" title="${esc(r.name)}">${esc(r.name)}</div>${withBars ? `<div class="cs-thin"><i style="width:${(r.cost / max * 100).toFixed(1)}%"></i></div>` : ""}</td>
      <td class="r">${num(r.count)}${r.failed ? `<div class="faint small">${r.failed} fallaron</div>` : ""}</td><td class="r">${r.tokens ? num(r.tokens) : "—"}</td><td class="r">${dur(r.seconds)}</td>
      <td class="r"><b>${usd(r.cost)}</b><div class="faint small">${[r.real ? "real " + usd(r.real) : "", r.estimated ? "est. " + usd(r.estimated) : "", r.local ? "luz " + usd(r.local) : ""].filter(Boolean).join(" · ")}</div></td></tr>`).join("")}</tbody></table>`
      : `<div class="faint small">Sin datos en este período.</div>`}</div>`;
}

function eventsTable(list) {
  return `<div class="cs-scroll"><table class="cs-table"><thead><tr><th>Cuándo</th><th>Qué</th><th>Proveedor · modelo</th><th>Agente</th><th class="r">Tokens</th><th class="r">Tiempo</th><th class="r">Costo</th></tr></thead><tbody>
    ${list.map((e) => `<tr class="${e.ok ? "" : "cs-fail"}"><td class="nowrap">${esc(when(e.at + "Z"))}</td><td>${esc(e.kind_label)}${e.ok ? "" : ' <span class="chip bad">falló</span>'}${e.note ? `<div class="faint small ellipsis" title="${esc(e.note)}">${esc(e.note)}</div>` : ""}</td>
      <td><div>${esc(e.provider)}</div><div class="faint small mono ellipsis" title="${esc(e.model)}">${esc(e.model || "")}</div></td><td>${esc(agentName(e.agent) || e.agent || "—")}</td>
      <td class="r">${e.tokens ? num(e.tokens) : "—"}</td><td class="r">${dur(e.seconds)}</td><td class="r nowrap"><b>${usd(e.cost)}</b> ${srcChip(e.source)}</td></tr>`).join("")}</tbody></table></div>`;
}

// Ahorro inteligente: optimizar sin bajar la calidad (todo se puede deshacer)
function renderAhorro(box, a, reload) {
  if (!box) return;
  const tips = a.tips || [];
  box.innerHTML = `<div class="card cs-ahorro"><div class="row" style="gap:10px;flex-wrap:wrap"><b>🌱 Ahorro inteligente</b>
      <span class="faint small grow">Optimizar sin bajar la calidad. Todo se puede deshacer.</span>
      <button class="btn sm primary" data-act="all_on" title="Caché, revisión de la red cada 6 h con descanso nocturno y modelo económico en tareas simples">⚡ Prender todos los ahorros</button>
      ${a.saved_total > 0 ? `<span class="chip good">Ya ahorrado: ${usd(a.saved_total)}</span>` : ""}
      ${a.potential_total > 0 ? `<span class="chip warn">Podrías ahorrar ~${usd(a.potential_total)} más en ${a.days} días</span>` : ""}</div>
    <div class="cs-tips">${tips.map((t) => `<div class="cs-tip-card ${t.on ? "on" : ""}">
      <div class="row" style="gap:8px;align-items:flex-start"><span class="cs-tip-ico">${t.icon}</span><div class="grow"><b>${esc(t.title)}</b>
        ${t.on ? ' <span class="chip good">Activado</span>' : ""}
        <div class="small muted" style="margin-top:3px">${esc(t.text)}</div>
        ${t.quality ? `<div class="small" style="margin-top:4px">✅ Calidad: ${esc(t.quality)}</div>` : ""}
        ${t.detail ? `<div class="faint small" style="margin-top:3px">${esc(t.detail)}</div>` : ""}</div>
        <div class="cs-tip-side">${t.saved ? `<div class="small">Ahorrado</div><b>${usd(t.saved)}</b>` : t.potential ? `<div class="small">Ahorro posible</div><b>~${usd(t.potential)}</b>` : ""}
          ${t.action ? `<button class="btn sm ${t.on ? "ghost" : "primary"}" data-act="${esc(t.action)}">${t.on ? "Deshacer" : "Activar"}</button>` : ""}
          ${t.choices ? `<label class="small">Cada <select data-net>${t.choices.map((h) => `<option value="${h}" ${h === t.value ? "selected" : ""}>${h} h</option>`).join("")}</select></label>
            <label class="small row" style="gap:6px"><input type="checkbox" data-night ${t.night_pause ? "checked" : ""}> Descansa de noche</label>` : ""}</div></div></div>`).join("")}</div>
    ${(a.keep || []).length ? `<div class="faint small" style="margin-top:10px">🔒 No se tocan (de ellos depende la calidad): ${a.keep.map((k) => `<b>${esc(k.agent)}</b> (${esc(k.why)})`).join(", ")}.</div>` : ""}</div>`;
  const send = async (action) => {
    try { await api("/api/consumo/ahorro", { method: "POST", body: { action } }); toast("Guardado. Se puede cambiar cuando quieras.", "good", "🛰️"); reload(); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  $$("[data-net]", box).forEach((s) => s.onchange = () => send(`network:${s.value}`));
  $$("[data-night]", box).forEach((c) => c.onchange = () => send(c.checked ? "night_on" : "night_off"));
  $$("[data-act]", box).forEach((b) => b.onclick = async () => {
    b.disabled = true;
    try { await api("/api/consumo/ahorro", { method: "POST", body: { action: b.dataset.act } }); toast("Listo. Se puede deshacer cuando quieras.", "good", "🌱"); reload(); }
    catch (e) { toast(esc(e.message), "bad"); b.disabled = false; }
  });
}
