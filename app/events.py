"""Bus de eventos: la web se suscribe para ver todo en vivo, y los webhooks de eventos
(n8n, Make, Zapier, tu servidor) reciben los importantes."""
import asyncio
import json
import logging
import time

log = logging.getLogger("shelton.events")
_subscribers: set[asyncio.Queue] = set()
_hooks_cache: tuple[float, list] = (0.0, [])


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    name = _external_name(event)
    if name:
        try:
            asyncio.get_running_loop().create_task(_forward(name, event))
        except RuntimeError:
            pass


def _external_name(ev: dict) -> str | None:
    t = ev.get("type")
    if t == "post" and ev["post"]["status"] in ("ready", "approved", "published", "failed", "rejected"):
        return f"post.{ev['post']['status']}"
    if t == "approval":
        return f"approval.{ev['approval']['status']}"
    if t == "campaign":
        return "campaign.update"
    if t == "subject":
        return "subject.updated"
    return None


def _hooks() -> list:
    global _hooks_cache
    if time.monotonic() - _hooks_cache[0] > 15:
        from app.connections import list_enabled
        _hooks_cache = (time.monotonic(), [dict(c.config or {}) for c in list_enabled("webhook")])
    return _hooks_cache[1]


def invalidate_hooks() -> None:
    global _hooks_cache
    _hooks_cache = (0.0, [])


async def _forward(name: str, event: dict) -> None:
    import httpx
    for cfg in _hooks():
        wanted = [e.strip() for e in str(cfg.get("events") or "").split(",") if e.strip()]
        if wanted and not any(name == w or (w.endswith("*") and name.startswith(w[:-1])) for w in wanted):
            continue
        headers = cfg.get("headers") or {}
        if isinstance(headers, str):
            headers = json.loads(headers) if headers.strip() else {}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(cfg["url"], json={"event": name, "data": event}, headers=headers)
        except Exception as e:
            log.warning("webhook %s falló: %s", cfg.get("url"), e)
