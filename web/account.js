// Shelton Studio — cuenta: tu usuario arriba, menú (contraseña, usuarios, salir) y salida animada.
"use strict";

(function sessionUI() {
  // Si la sesión vence en medio del uso, vuelve al login (y luego a esta misma página)
  const _fetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const r = await _fetch(...args);
    const url = String(args[0]?.url || args[0] || "");
    if (r.status === 401 && url.startsWith("/api/") && !url.startsWith("/api/auth/")) {
      location.replace("/login?next=" + encodeURIComponent(location.pathname + location.hash));
    }
    return r;
  };
  api("/api/auth/me").then((m) => {
    S.me = m.user; S.authOn = m.auth;
    if (!m.auth || !m.user) return;
    const b = document.createElement("button"); b.className = "user-chip"; b.id = "user-chip"; b.title = "Tu cuenta";
    b.innerHTML = `<span class="u-av">${esc(m.user.username[0].toUpperCase())}</span><span class="u-name">${esc(m.user.username)}</span>`;
    $(".topbar").append(b);
    b.onclick = (e) => { e.stopPropagation(); userMenu(); };
  }).catch(() => {});
})();

function userMenu() {
  const old = $("#user-menu"); if (old) { old.remove(); return; }
  const r = $("#user-chip").getBoundingClientRect(), m = document.createElement("div");
  m.className = "popover user-menu"; m.id = "user-menu"; m.style.top = r.bottom + 8 + "px"; m.style.right = "16px";
  m.innerHTML = `<div class="um-head"><span class="u-av lg">${esc(S.me.username[0].toUpperCase())}</span><div><b>${esc(S.me.username)}</b><div class="faint small">${S.me.role === "admin" ? "Administrador" : "Editor"} · sesión guardada en este navegador</div></div></div>
    <button class="um-item" id="um-pw">${icon("key", 16)} Cambiar contraseña</button>
    ${S.me.role === "admin" ? `<button class="um-item" id="um-users">${icon("users", 16)} Usuarios</button>` : ""}
    <button class="um-item danger" id="um-out"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg> Salir</button>`;
  document.body.append(m);
  const close = (e) => { if (!e.target.closest("#user-menu")) { m.remove(); document.removeEventListener("click", close); } };
  setTimeout(() => document.addEventListener("click", close), 0);
  $("#um-pw").onclick = () => { m.remove(); changePassword(); };
  if ($("#um-users")) $("#um-users").onclick = () => { m.remove(); manageUsers(); };
  $("#um-out").onclick = () => { m.remove(); logout(); };
}

async function logout() {
  const o = document.createElement("div"); o.className = "bye";
  o.innerHTML = `<div class="bye-in"><div class="bye-logo">${S.wl.logo_url ? `<img src="${esc(S.wl.logo_url)}">` : esc((S.wl.product_name || "S")[0])}</div><b>¡Hasta pronto, ${esc(S.me?.username || "")}!</b><span>Cerrando sesión…</span></div>`;
  document.body.append(o);
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch {}
  setTimeout(() => location.replace("/login"), 900);
}

function changePassword() {
  const box = modal("Cambiar contraseña", `<label class="f">Contraseña actual<input type="password" id="pw-c" autocomplete="current-password"></label>
    <label class="f">Nueva contraseña (mín. 5 caracteres)<input type="password" id="pw-n" autocomplete="new-password"></label>
    <label class="f">Repite la nueva<input type="password" id="pw-r" autocomplete="new-password"></label>
    <div class="hint">Al cambiarla se cierran las sesiones abiertas en otros navegadores.</div>`,
    `<button class="btn ghost" onclick="closeModal()">Cancelar</button><button class="btn primary" id="pw-go">Guardar</button>`);
  $("#pw-go", box).onclick = async () => {
    if ($("#pw-n").value !== $("#pw-r").value) return toast("Las contraseñas nuevas no coinciden", "bad");
    try { await api("/api/auth/password", { method: "POST", body: { current: $("#pw-c").value, new: $("#pw-n").value } }); closeModal(); toast("Contraseña cambiada", "good", "🔐"); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
}

async function manageUsers() {
  const list = await api("/api/users");
  const box = modal("Usuarios", `<div class="hint">Los <b>admin</b> pueden todo (incluido crear usuarios). Los <b>editores</b> usan el estudio pero no administran cuentas.</div>
    ${list.map((u) => `<div class="card" style="padding:12px;display:flex;gap:10px;align-items:center"><span class="u-av">${esc(u.username[0].toUpperCase())}</span><div class="grow"><b>${esc(u.username)}</b> <span class="chip ${u.role === "admin" ? "accent" : ""}">${u.role}</span><div class="faint small">Último ingreso: ${u.last_login ? when(u.last_login) : "nunca"}</div></div>${u.id !== S.me.id ? `<button class="btn sm ghost" data-udel="${u.id}">${icon("trash", 14)}</button>` : '<span class="faint small">tú</span>'}</div>`).join("")}
    <h3 style="margin:8px 0 0">Nuevo usuario</h3><div class="row"><label class="f grow">Usuario<input id="nu-u" autocomplete="off"></label><label class="f" style="width:140px">Rol<select id="nu-r"><option value="editor">Editor</option><option value="admin">Admin</option></select></label></div>
    <label class="f">Contraseña (mín. 5 caracteres)<input type="password" id="nu-p" autocomplete="new-password"></label>`,
    `<button class="btn ghost" onclick="closeModal()">Cerrar</button><button class="btn primary" id="nu-go">Crear usuario</button>`);
  $("#nu-go", box).onclick = async () => {
    try { await api("/api/users", { method: "POST", body: { username: $("#nu-u").value, password: $("#nu-p").value, role: $("#nu-r").value } }); toast("Usuario creado", "good", "👤"); manageUsers(); }
    catch (e) { toast(esc(e.message), "bad"); }
  };
  $$("[data-udel]", box).forEach((b) => b.onclick = async () => {
    if (!confirm("¿Borrar este usuario?")) return;
    try { await api(`/api/users/${b.dataset.udel}`, { method: "DELETE" }); manageUsers(); } catch (e) { toast(esc(e.message), "bad"); }
  });
}
