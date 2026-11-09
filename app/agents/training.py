"""Entrenamiento humano de cada agente.

Cada lección es un archivo Markdown en data/training/<agente>/AAAA-MM-DD_HH-MM-SS_<titulo>.md
(se puede ver, copiar o respaldar desde la carpeta). El agente lee sus lecciones activas cada vez que trabaja,
así "recuerda" lo que le enseñaron aunque pasen días.
"""
import re
from datetime import datetime
from pathlib import Path

from app.config import settings

MAX_CHARS = 8000   # tope de conocimiento que se inyecta en cada llamada (para cuidar tokens)


def _dir(agent: str) -> Path:
    d = settings.DATA_DIR / "training" / re.sub(r"[^\w-]", "_", agent)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE).strip()
    return re.sub(r"[\s_-]+", "-", s)[:50] or "leccion"


def _parse(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---"):
        head, _, body = raw[3:].partition("\n---")
        for line in head.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        body = body.lstrip("\n")
    status = meta.get("status") or ("active" if meta.get("enabled", "true") != "false" else "disabled")
    return {"id": path.stem, "file": str(path), "title": meta.get("title", path.stem), "author": meta.get("author", ""),
            "role": meta.get("role", ""), "source": meta.get("source", "manual"), "created": meta.get("created", ""),
            "status": status, "enabled": status == "active", "conflict_with": meta.get("conflict_with", ""),
            "conflict_reason": meta.get("conflict_reason", ""), "decided_by": meta.get("decided_by", ""),
            "content": body.strip()}


def _one_line(v) -> str:
    return str(v or "").replace("\n", " ").strip()


def _write(path: Path, e: dict) -> None:
    status = e.get("status") or ("active" if e.get("enabled", True) else "disabled")
    path.write_text(f"---\ntitle: {_one_line(e['title'])}\nauthor: {_one_line(e.get('author'))}\nrole: {_one_line(e.get('role'))}\n"
                    f"source: {_one_line(e.get('source', 'manual'))}\ncreated: {e['created']}\nstatus: {status}\n"
                    f"conflict_with: {_one_line(e.get('conflict_with'))}\nconflict_reason: {_one_line(e.get('conflict_reason'))}\n"
                    f"decided_by: {_one_line(e.get('decided_by'))}\n"
                    f"---\n\n{e['content'].strip()}\n", encoding="utf-8")


def add(agent: str, title: str, content: str, author: str = "", role: str = "", source: str = "manual",
        status: str = "active", conflict_with: str = "", conflict_reason: str = "") -> dict:
    now = datetime.now()
    path = _dir(agent) / f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_{_slug(title)}.md"
    i = 1
    while path.exists():
        path = path.with_name(f"{path.stem}-{i}.md")
        i += 1
    e = {"title": title.strip() or "Lección", "content": content, "author": author, "role": role, "source": source,
         "created": now.strftime("%Y-%m-%d %H:%M:%S"), "status": status, "conflict_with": conflict_with,
         "conflict_reason": conflict_reason}
    _write(path, e)
    return _parse(path)


CONFLICT_CHECK = """You maintain the memory of an AI agent. Compare a NEW lesson with the agent's EXISTING active lessons.
A contradiction means following both is impossible or they give opposite instructions for the same situation
(e.g. "always use 3 hashtags" vs "use 10 hashtags"). Refinements, additions or exceptions are NOT contradictions.
Explain briefly and concretely in Spanish, citing what each one says."""


async def add_checked(agent: str, title: str, content: str, author: str = "", role: str = "", source: str = "manual",
                      auto_apply: bool = True) -> dict:
    """Guarda la lección revisando contradicciones. Si choca con otra, queda en espera hasta que una persona decida."""
    from app.llm import json_call, obj
    active = [e for e in entries(agent) if e["enabled"]]
    conflict = None
    if active:
        listing = "\n".join(f"[{e['id']}] ({e['created']}) {e['title']}: {e['content'][:400]}" for e in active[:40])
        try:
            r = await json_call(CONFLICT_CHECK, f"EXISTING:\n{listing}\n\nNEW: {title}: {content}", obj({
                "contradicts": {"type": "boolean"}, "conflicting_id": {"type": "string"},
                "explanation": {"type": "string"}}), effort="low", agent="shelton", summary="revisar memoria")
            if r["contradicts"]:
                conflict = r
        except Exception:
            conflict = None
    if conflict:
        old = next((e for e in active if e["id"] == conflict["conflicting_id"]), None)
        e = add(agent, title, content, author, role, source, status="conflict",
                conflict_with=conflict["conflicting_id"],
                conflict_reason=(f"Contradice «{old['title']}» del {old['created']}. " if old else "") + conflict["explanation"])
    else:
        e = add(agent, title, content, author, role, source, status="active" if auto_apply else "pending")
    return e


def resolve(agent: str, entry_id: str, action: str, decided_by: str = "") -> dict:
    """Decisión humana sobre una lección en espera: activate (bajo su responsabilidad) | replace | discard."""
    path = _dir(agent) / f"{Path(entry_id).name}.md"
    if not path.exists():
        raise ValueError("Lección no encontrada")
    e = _parse(path)
    if action == "discard":
        e["status"] = "disabled"
    elif action == "replace":
        if e.get("conflict_with"):
            try:
                update(agent, e["conflict_with"], status="disabled", decided_by=f"reemplazada por {e['id']}")
            except ValueError:
                pass
        e["status"] = "active"
    elif action == "activate":
        e["status"] = "active"
        if e.get("conflict_reason"):
            e["conflict_reason"] = "ACTIVADA A PESAR DE LA CONTRADICCIÓN (decisión humana). " + e["conflict_reason"]
    else:
        raise ValueError("Acción inválida")
    e["decided_by"] = decided_by or "persona del equipo"
    _write(path, e)
    return _parse(path)


def entries(agent: str) -> list[dict]:
    return [_parse(p) for p in sorted(_dir(agent).glob("*.md"), reverse=True)]


def update(agent: str, entry_id: str, **fields) -> dict:
    path = _dir(agent) / f"{Path(entry_id).name}.md"
    if not path.exists():
        raise ValueError("Lección no encontrada")
    e = _parse(path)
    e.update({k: v for k, v in fields.items() if v is not None})
    if "enabled" in fields and fields["enabled"] is not None and "status" not in fields:
        e["status"] = "active" if fields["enabled"] else "disabled"
    _write(path, e)
    return _parse(path)


def delete(agent: str, entry_id: str) -> None:
    """Nada se borra: la lección pasa a _archivo/ (deja de aplicarse, pero queda guardada y se puede reactivar)."""
    path = _dir(agent) / f"{Path(entry_id).name}.md"
    if path.exists():
        e = _parse(path)
        e["status"] = "disabled"
        e["decided_by"] = (e.get("decided_by") or "") + f" · archivada {datetime.now():%Y-%m-%d %H:%M}"
        arch = path.parent / "_archivo"
        arch.mkdir(exist_ok=True)
        _write(arch / path.name, e)
        path.unlink()


def restore(agent: str, entry_id: str) -> dict:
    """Reactiva una lección archivada."""
    src = _dir(agent) / "_archivo" / f"{Path(entry_id).name}.md"
    if not src.exists():
        raise ValueError("Lección archivada no encontrada")
    e = _parse(src)
    e["status"] = "active"
    _write(_dir(agent) / src.name, e)
    src.unlink()
    return _parse(_dir(agent) / src.name)


def knowledge(agent: str) -> str:
    """Bloque que se agrega al prompt del agente: lecciones activas, de la más nueva a la más vieja."""
    parts, total = [], 0
    for e in entries(agent):
        if not e["enabled"]:
            continue
        chunk = f"### {e['title']} ({e['created']}{', ' + e['author'] if e['author'] else ''})\n{e['content']}"
        if total + len(chunk) > MAX_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    if not parts:
        return ""
    return ("\n\n## Entrenamiento recibido de tu equipo humano\n"
            "Estas lecciones las escribieron personas del equipo. Síguelas: tienen prioridad sobre tus costumbres, "
            "salvo que contradigan las reglas de seguridad.\n\n" + "\n\n".join(parts))


REMEMBER_TOOL = {
    "name": "remember",
    "description": "Guarda una lección permanente en el entrenamiento de un agente. Úsala SOLO cuando el usuario te "
                   "enseñe algo explícitamente ('aprende que...', 'recuerda siempre...', 'de ahora en adelante...').",
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "Título corto de la lección"},
        "content": {"type": "string", "description": "La lección clara y accionable, con ejemplos si los dio"},
        "agent": {"type": "string", "description": "A qué agente entrenar (por defecto, tú mismo)"},
        "subject": {"type": "string", "description": "Opcional: nombre de un modelo del banco si la lección es de ESE "
                    "modelo (ej. «Valentina siempre con el lunar en la mejilla izquierda»). Se aplica cada vez que se use."},
    }, "required": ["title", "content"]},
}


# ------------------------------------------------------------------ Revisión del equipo (Shelton lee a todos)
TEAM_AUDIT = """You are Shelton, the chief agent. You read the instructions (prompt) and the trained lessons of every
agent on your team plus the brand profile. Find real PROBLEMS between them: contradictions across agents (e.g. the
copywriter was told 3 hashtags max but the brand has 10 base hashtags), lessons that conflict with the brand or with
another agent's job, outdated or vague lessons, and gaps that cause repeated human corrections. For each issue say
which agents are involved, explain the problem clearly in Spanish, and give a concrete suggestion (which lesson to
change, replace or add). If everything is consistent, say so. Do not invent problems."""


def audits_dir() -> Path:
    d = settings.DATA_DIR / "training" / "_revisiones"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def team_audit(brand_text: str) -> dict:
    from app.agents import registry
    from app.llm import json_call, obj
    team = []
    for key, meta in registry.AGENTS.items():
        lessons = [e for e in entries(key) if e["status"] in ("active", "conflict", "pending")]
        team.append(f"## {key} — {meta['name']}: {meta['role']}\nPrompt (extracto): {registry.base_prompt(key)[:900]}\n"
                    + "\n".join(f"- [{e['status']}] {e['title']} ({e['created']}): {e['content'][:300]}" for e in lessons))
    data = await json_call(TEAM_AUDIT, f"{brand_text}\n\n" + "\n\n".join(team)[:90000], obj({
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": obj({
            "agents": {"type": "array", "items": {"type": "string"}}, "severity": {"type": "string", "enum": ["alta", "media", "baja"]},
            "problem": {"type": "string"}, "explanation": {"type": "string"}, "suggestion": {"type": "string"}})},
    }), effort="medium", agent="shelton", summary="revisión del equipo")
    now = datetime.now()
    data["created"] = now.strftime("%Y-%m-%d %H:%M:%S")
    path = audits_dir() / f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_revision.md"
    md = [f"# Revisión del equipo — {data['created']}", "", data["summary"], ""]
    for i in data["issues"]:
        md += [f"## [{i['severity']}] {i['problem']}", f"Agentes: {', '.join(i['agents'])}", "", i["explanation"], "",
               f"**Sugerencia:** {i['suggestion']}", ""]
    path.write_text("\n".join(md), encoding="utf-8")
    (path.with_suffix(".json")).write_text(__import__("json").dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    data["id"] = path.stem
    return data


def audits(limit: int = 20) -> list[dict]:
    import json
    out = []
    for p in sorted(audits_dir().glob("*.json"), reverse=True)[:limit]:
        d = json.loads(p.read_text(encoding="utf-8"))
        d["id"] = p.stem
        out.append(d)
    return out


# ------------------------------------------------------------------ Entrenamiento por modelo del banco
def subject_key(subject_id: str) -> str:
    return f"subject_{subject_id}"


def subject_knowledge(subject_id: str, max_chars: int = 1500) -> str:
    """Lecciones activas de un modelo del banco (ej. «a Valentina siempre con lunar en la mejilla izquierda»)."""
    parts, total = [], 0
    for e in entries(subject_key(subject_id)):
        if not e["enabled"]:
            continue
        chunk = f"{e['title']}: {e['content']}".replace("\n", " ")
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return " | ".join(parts)


# ------------------------------------------------------------------ Shelton pasa cada cambio al equipo entero
PROPAGATE = """You are Shelton, the chief of an AI content team. A human just taught ONE agent (or one recurring model of
the bank) a new lesson. Decide which OTHER agents must also know it so the whole chain stays consistent
(e.g. "never show the car's logo" taught to the art director also matters to the judge, who must check it, and to
the video producer, who must not reveal it in motion). Only include agents whose work is really affected; for each,
rewrite the lesson in its own terms (Spanish, short, actionable). If nobody else is affected, return an empty list.
Never weaken safety rules."""


async def propagate(agent: str, entry: dict) -> list[dict]:
    """Copia adaptada de una lección a los agentes que también la necesitan (no se re-propaga en cadena)."""
    from app.agents import registry
    from app.llm import json_call, obj
    if entry.get("source", "").startswith("equipo") or entry.get("status") != "active":
        return []
    team = "\n".join(f"- {k}: {m['name']} — {m['role']}" for k, m in registry.AGENTS.items() if k != agent)
    who = registry.AGENTS.get(agent, {}).get("name") or agent
    try:
        r = await json_call(PROPAGATE, f"Lesson taught to {who} ({agent}):\n{entry['title']}: {entry['content']}\n\n"
                                       f"Team:\n{team}", obj({
            "updates": {"type": "array", "items": obj({
                "agent": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}})}}),
            effort="low", agent="shelton", summary="pasar lección al equipo")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for u in r.get("updates", [])[:8]:
        key = u.get("agent", "")
        if key == agent or key not in registry.AGENTS or not u.get("content"):
            continue
        e = await add_checked(key, u.get("title") or entry["title"], u["content"] + f"\n\n(Pasada por Shelton desde {who}.)",
                              author="Shelton", role="jefe", source=f"equipo:{agent}")
        out.append({**e, "agent": key, "agent_name": registry.AGENTS[key]["name"]})
    return out
