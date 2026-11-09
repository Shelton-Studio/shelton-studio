"""Archivo de memoria: todo lo que Shelton y cada subagente aprenden y hacen, en archivos .md que no se pierden.

data/training/
  <agente>/AAAA-MM-DD_HH-MM-SS_<lección>.md      lecciones activas (las lee el agente cada vez que trabaja)
  <agente>/_archivo/...                          lecciones archivadas: nunca se borran, se pueden reactivar
  subject_<id>/...                               memoria de cada modelo del banco
  _bitacora/<agente>/AAAA-MM-DD.md                qué hizo cada agente cada día (para retomar mañana)
  _conversaciones/AAAA-MM-DD_<id>.md              cada conversación completa
  _revisiones/...                                revisiones del equipo que hace Shelton
Todo se ve y se descarga desde la pestaña «Archivos» (uno por uno o en ZIP).
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import settings


def root() -> Path:
    d = settings.DATA_DIR / "training"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return re.sub(r"[^\w-]", "_", name)[:60] or "x"


# ------------------------------------------------------------------ Bitácora diaria de cada agente
def log_work(agent: str, summary: str, ok: bool = True, model: str = "", error: str = "") -> None:
    """Una línea por trabajo en data/training/_bitacora/<agente>/<día>.md. Nunca falla (no frena el trabajo)."""
    try:
        now = datetime.now()
        d = root() / "_bitacora" / _safe(agent)
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{now:%Y-%m-%d}.md"
        if not f.exists():
            f.write_text(f"# Bitácora de {agent} — {now:%Y-%m-%d}\n\n", encoding="utf-8")
        text = (summary or "trabajo").replace("\n", " ").strip()[:300]
        extra = f" · {model}" if model else ""
        mark = "✅" if ok else "⚠️"
        line = f"- {now:%H:%M:%S} {mark} {text}{extra}" + (f" — error: {error[:200]}" if error else "")
        with f.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def recent_work(agent: str, max_lines: int = 15) -> str:
    """Lo último que hizo un agente (hoy y días anteriores), para retomar donde quedó."""
    d = root() / "_bitacora" / _safe(agent)
    if not d.exists():
        return ""
    lines: list[str] = []
    for f in sorted(d.glob("*.md"), reverse=True)[:3]:
        day = f.stem
        for ln in reversed(f.read_text(encoding="utf-8").splitlines()):
            if ln.startswith("- "):
                lines.append(f"{day} {ln[2:]}")
                if len(lines) >= max_lines:
                    return "\n".join(reversed(lines))
    return "\n".join(reversed(lines))


# ------------------------------------------------------------------ Conversaciones completas
def save_conversation(conv_id: str, title: str, agent: str, display: list[dict], created=None) -> None:
    try:
        d = root() / "_conversaciones"
        d.mkdir(parents=True, exist_ok=True)
        day = (created or datetime.now()).strftime("%Y-%m-%d")
        f = d / f"{day}_{_safe(conv_id)}.md"
        out = [f"# {title}", "", f"- Agente: {agent}", f"- Conversación: {conv_id}",
               f"- Actualizada: {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        for m in display:
            who = "**Persona**" if m.get("role") == "user" else f"**{agent}**"
            out.append(f"{who}: {m.get('text', '')}")
            if m.get("post_ids"):
                out.append(f"_posts: {', '.join(m['post_ids'])}_")
            out.append("")
        f.write_text("\n".join(out), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ Árbol de archivos para la web
def _label(folder: str) -> tuple[str, str]:
    """(grupo, nombre legible) de una carpeta de data/training."""
    from app.agents import registry
    if folder == "_conversaciones":
        return "Conversaciones", "Conversaciones"
    if folder == "_revisiones":
        return "Revisiones del equipo", "Revisiones de Shelton"
    if folder.startswith("subject_"):
        from app.db import Subject, session
        with session() as s:
            sub = s.get(Subject, folder[8:])
            return "Modelos del banco", f"Modelo {sub.name}" if sub else folder
    meta = registry.AGENTS.get(folder)
    return "Agentes", (meta["name"] if meta else folder)


def tree() -> list[dict]:
    """Carpetas con sus archivos (lecciones activas y archivadas, bitácoras, conversaciones, revisiones)."""
    r = root()
    folders: dict[str, dict] = {}

    def add(key: str, group: str, name: str, f: Path, kind: str):
        st = f.stat()
        folders.setdefault(key, {"key": key, "group": group, "name": name, "files": []})["files"].append({
            "path": f.relative_to(r).as_posix(), "name": f.name, "kind": kind, "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    for d in sorted(p for p in r.iterdir() if p.is_dir()):
        if d.name == "_bitacora":
            for a in sorted(x for x in d.iterdir() if x.is_dir()):
                _, name = _label(a.name)
                for f in sorted(a.glob("*.md"), reverse=True):
                    add(a.name, "Agentes" if not a.name.startswith("subject_") else "Modelos del banco", name, f, "bitácora")
            continue
        group, name = _label(d.name)
        for f in sorted(d.glob("*.md"), reverse=True):
            add(d.name, group, name, f, {"_conversaciones": "conversación", "_revisiones": "revisión"}.get(d.name, "lección"))
        arch = d / "_archivo"
        if arch.exists():
            for f in sorted(arch.glob("*.md"), reverse=True):
                add(d.name, group, name, f, "archivada")
    order = {"Agentes": 0, "Modelos del banco": 1, "Revisiones del equipo": 2, "Conversaciones": 3}
    out = sorted(folders.values(), key=lambda x: (order.get(x["group"], 9), x["name"]))
    for x in out:
        x["count"] = len(x["files"])
    return out


def resolve(rel: str) -> Path:
    """Ruta segura dentro de data/training (nada fuera de ahí)."""
    r = root().resolve()
    p = (r / rel).resolve()
    if r not in p.parents and p != r:
        raise ValueError("Ruta no permitida")
    if not p.exists() or not p.is_file():
        raise ValueError("Ese archivo no existe")
    return p


def zip_bytes(folder: str | None = None) -> bytes:
    """ZIP de toda la memoria, o de una carpeta (un agente, un modelo, conversaciones...)."""
    r = root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(r.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(r)
            if folder:
                parts = rel.parts
                inside = parts[0] == folder or (len(parts) > 1 and parts[0] == "_bitacora" and parts[1] == folder)
                if not inside:
                    continue
            z.write(f, rel.as_posix())
        z.writestr("LEEME.md", "# Memoria de Shelton Studio\n\nLecciones activas, archivadas (_archivo), bitácoras "
                               "diarias (_bitacora), conversaciones y revisiones del equipo. Todo en Markdown.\n")
    return buf.getvalue()


# ------------------------------------------------------------------ Lo que lee Shelton al empezar cada sesión
def team_digest(max_lines: int = 40) -> str:
    """Resumen de los archivos: lo último que hizo cada agente (hoy y ayer) y las conversaciones recientes."""
    r = root()
    out = []
    b = r / "_bitacora"
    if b.exists():
        for a in sorted(x for x in b.iterdir() if x.is_dir()):
            recent = recent_work(a.name, 4)
            if recent:
                out.append(f"### {_label(a.name)[1]}\n{recent}")
    conv = r / "_conversaciones"
    if conv.exists():
        titles = []
        for f in sorted(conv.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]:
            first = f.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
            titles.append(f"- {f.stem[:10]}: {first}")
        if titles:
            out.append("### Conversaciones recientes\n" + "\n".join(titles))
    text = "\n\n".join(out)
    lines = text.splitlines()
    return "\n".join(lines[:max_lines * 2])


def search(query: str, limit: int = 20) -> list[dict]:
    """Busca un texto en todos los archivos de memoria."""
    q = query.lower().strip()
    hits = []
    for f in sorted(root().rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                if q in ln.lower():
                    hits.append({"path": f.relative_to(root()).as_posix(), "line": ln.strip()[:300]})
                    if len(hits) >= limit:
                        return hits
        except Exception:  # noqa: BLE001
            continue
    return hits
