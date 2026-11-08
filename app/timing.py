"""Mejor hora para publicar, por red y por modelo del banco.

Mezcla lo de ADENTRO (tu historial: cuándo publicas, qué se aprueba y qué se rechaza de cada modelo y red) con lo de
AFUERA (investigación web actual sobre horarios por red y por público). Se recalcula en la revisión de la red cada 2 h
y queda en data/timing.json. Si conectas métricas de las redes más adelante, entran como señal de adentro.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from app.config import settings
from app.db import Brand, Post, Subject, get_or_create_brand, session

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def tz_name() -> str:
    return os.getenv("STUDIO_TIMEZONE", "") or "America/Argentina/Buenos_Aires"


def _local(dt: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        from datetime import timezone
        return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name()))
    except Exception:  # noqa: BLE001
        return dt


def _path() -> Path:
    return settings.DATA_DIR / "timing.json"


def load() -> dict:
    p = _path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def inside_stats(days: int = 60) -> dict:
    """Señales de adentro: publicaciones por hora/día, aprobaciones y rechazos por modelo y red."""
    from app.db import Feedback
    since = datetime.utcnow() - timedelta(days=days)
    pub_hours, pub_days = Counter(), Counter()
    per_subject = defaultdict(lambda: {"published": 0, "approved": 0, "rejected": 0, "hours": Counter(), "platforms": Counter()})
    per_platform = defaultdict(lambda: {"published": 0, "approved": 0, "rejected": 0, "hours": Counter()})
    with session() as s:
        subs = {x.id: x.name for x in s.query(Subject).all()}
        posts = {p.id: p for p in s.query(Post).filter(Post.created_at >= since)}
        for p in posts.values():
            for e in p.publish_log or []:
                try:
                    at = _local(datetime.fromisoformat(str(e.get("at", "")).rstrip("Z")))
                except ValueError:
                    continue
                pub_hours[at.hour] += 1
                pub_days[DAYS[at.weekday()]] += 1
                per_platform[p.platform]["published"] += 1
                per_platform[p.platform]["hours"][at.hour] += 1
                for sid in p.subject_ids or []:
                    if sid in subs:
                        per_subject[subs[sid]]["published"] += 1
                        per_subject[subs[sid]]["hours"][at.hour] += 1
                        per_subject[subs[sid]]["platforms"][p.platform] += 1
        for f in s.query(Feedback).filter(Feedback.created_at >= since, Feedback.action.in_(["approved", "rejected"])):
            p = posts.get(f.post_id)
            if not p:
                continue
            per_platform[p.platform][f.action] += 1
            for sid in p.subject_ids or []:
                if sid in subs:
                    per_subject[subs[sid]][f.action] += 1
                    per_subject[subs[sid]]["platforms"][p.platform] += 1

    def top(c: Counter, n=3):
        return [h for h, _ in c.most_common(n)]
    return {"timezone": tz_name(), "days": days,
            "published_by_hour": dict(sorted(pub_hours.items())), "published_by_day": dict(pub_days),
            "subjects": {k: {**{x: v[x] for x in ("published", "approved", "rejected")}, "top_hours": top(v["hours"]),
                             "platforms": dict(v["platforms"])} for k, v in per_subject.items()},
            "platforms": {k: {**{x: v[x] for x in ("published", "approved", "rejected")}, "top_hours": top(v["hours"])}
                          for k, v in per_platform.items()},
            "note": "Sin métricas de alcance conectadas: las señales de adentro son cuándo publicas y qué apruebas."}


TIMING = """You are the scheduling strategist of a content studio. Recommend the BEST TIME TO PUBLISH for each social
network and for each recurring model of the bank. Combine:
1) OUTSIDE: the web research report about current best posting times (by platform, audience, country/time zone, niche).
2) INSIDE: the studio's own history (when it publishes, what humans approve or reject per model and platform).
Inside data with real signal wins over generic advice; say when inside data is still too thin. Times in the studio
time zone, as concrete windows (e.g. "mar-jue 19:00-21:00"). Spanish. Short `why` for each. Keep source URLs exact."""


async def refresh() -> dict:
    """Investiga afuera + analiza adentro y guarda recomendaciones por red y por modelo."""
    from app.agents import registry
    from app.agents.specialists import brand_context
    from app.llm import create, json_call, obj, text_of
    from app import working
    with session() as s:
        b: Brand = get_or_create_brand(s)
        brand_txt = brand_context(b)
        platforms = list(b.platforms or []) or ["instagram_feed", "instagram_story", "tiktok"]
        subjects = [f"{x.name} ({x.kind})" for x in s.query(Subject).all()]
    inside = inside_stats()
    ask = (f"{brand_txt}\nTime zone: {tz_name()}\nPlatforms: {', '.join(platforms)}\n"
           f"Recurring models: {', '.join(subjects) or 'none'}\n"
           "Research the most recent data (this year) about the best days and hours to post on each of these platforms "
           "for this audience and niche, in this time zone. Report concrete windows with sources.")
    report = ""
    messages = [{"role": "user", "content": ask}]
    with working.track("trend_scout", "Mejor hora para publicar (investigando afuera)"):
        for _ in range(3):
            resp = await create(agent="trend_scout", summary="mejor hora para publicar", system=registry.prompt("trend_scout"),
                                messages=messages, max_tokens=12000, output_config={"effort": "low"},
                                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}])
            report += text_of(resp)
            if resp.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": [x.model_dump(mode="json", exclude_none=True) for x in resp.content]})
    data = await json_call(TIMING, f"OUTSIDE REPORT:\n{report[:12000]}\n\nINSIDE DATA:\n{json.dumps(inside, ensure_ascii=False)}\n\n"
                                   f"Platforms: {', '.join(platforms)}\nModels: {', '.join(subjects) or 'none'}", obj({
        "summary": {"type": "string"},
        "platforms": {"type": "array", "items": obj({"platform": {"type": "string"}, "best": {"type": "string"},
                                                     "why": {"type": "string"}})},
        "models": {"type": "array", "items": obj({"model": {"type": "string"}, "platform": {"type": "string"},
                                                  "best": {"type": "string"}, "why": {"type": "string"}})},
        "sources": {"type": "array", "items": {"type": "string"}},
    }), effort="low", agent="trend_scout", summary="ordenar horarios")
    data["inside"] = inside
    data["timezone"] = tz_name()
    data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def for_subject(name: str) -> list[dict]:
    return [m for m in load().get("models", []) if m.get("model", "").lower().startswith(name.lower())]
