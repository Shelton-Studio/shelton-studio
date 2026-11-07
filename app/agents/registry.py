"""Registro de agentes: metadatos, ficha editable (prompt/modelo/esfuerzo) y versiones."""
from app.config import settings
from app.db import AgentProfile, session

# key -> datos fijos. Los prompts por defecto viven en cada módulo y se registran con register_default().
AGENTS: dict[str, dict] = {
    "shelton": {"name": "Shelton", "role": "Jefe de producción: conversa contigo y dirige a todo el equipo", "effort": "medium", "color": "#7c5cff"},
    "art_director": {"name": "Director creativo", "role": "Convierte ideas en prompts de imagen", "effort": "low", "color": "#a855f7"},
    "edit_director": {"name": "Editor", "role": "Traduce tus cambios en instrucciones de edición", "effort": "low", "color": "#c084fc"},
    "judge": {"name": "Juez de calidad", "role": "Segunda mirada: puntúa cada imagen (solo opina)", "effort": "low", "color": "#f59e0b"},
    "copywriter": {"name": "Copywriter", "role": "Caption, hashtags y alt text por red", "effort": "low", "color": "#6366f1"},
    "planner": {"name": "Planificador", "role": "Diseña campañas y lotes de ideas", "effort": "medium", "color": "#19c3a6"},
    "subject_designer": {"name": "Diseñador de modelos", "role": "Crea personajes, autos y lugares del banco", "effort": "low", "color": "#ec4899"},
    "video_producer": {"name": "Trends y Video", "role": "Productor de reels: dirige el movimiento de cada clip (Wan 2.2)", "effort": "low", "color": "#ef4444"},
    "trend_scout": {"name": "Scout de tendencias", "role": "Busca 24/7 tendencias de redes para replicar con tus modelos", "effort": "medium", "color": "#22c55e"},
    "tool_scout": {"name": "Scout de tools", "role": "Busca y evalúa herramientas y modelos locales nuevos", "effort": "medium", "color": "#84cc16"},
    "subject_analyst": {"name": "Analista de referencias", "role": "Describe las fotos que subes al banco", "effort": "low", "color": "#0ea5e9"},
}
_DEFAULT_PROMPTS: dict[str, str] = {}
_cache: dict[str, AgentProfile | None] = {}


def register_default(key: str, prompt: str) -> str:
    _DEFAULT_PROMPTS[key] = prompt
    return prompt


def profile(key: str) -> AgentProfile | None:
    if key not in _cache:
        with session() as s:
            _cache[key] = s.get(AgentProfile, key)
    return _cache[key]


def prompt(key: str) -> str:
    p = profile(key)
    return (p.prompt if p and p.prompt.strip() else _DEFAULT_PROMPTS.get(key, ""))


def model(key: str) -> str:
    p = profile(key)
    return (p.model if p and p.model else settings.CLAUDE_MODEL)


def effort(key: str, default: str) -> str:
    p = profile(key)
    return p.effort if p and p.effort else default


def save(key: str, **fields) -> AgentProfile:
    if key not in AGENTS:
        raise ValueError(f"Agente desconocido: {key}")
    with session() as s:
        p = s.get(AgentProfile, key) or AgentProfile(key=key, version=1)
        if s.get(AgentProfile, key) is None:
            s.add(p)
        changed = False
        for k, v in fields.items():
            if v is not None and getattr(p, k) != v:
                setattr(p, k, v)
                changed = True
        if changed and fields.keys() & {"prompt", "model", "effort"}:
            p.version = (p.version or 1) + 1
        s.flush()
        _cache.pop(key, None)
        return p


def info(key: str) -> dict:
    p = profile(key)
    meta = AGENTS[key]
    return {"key": key, **meta, "prompt": prompt(key), "default_prompt": _DEFAULT_PROMPTS.get(key, ""),
            "custom_prompt": bool(p and p.prompt.strip()), "model": model(key),
            "effort": effort(key, meta["effort"]), "version": p.version if p else 1,
            "enabled": p.enabled if p else True}
