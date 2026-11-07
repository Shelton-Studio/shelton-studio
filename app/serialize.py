"""Conversión de modelos de la BD a JSON para la API y para Claude."""
from app.db import Brand, Campaign, Post, Subject
from app.media import url_for
from app.platforms import get_platform


def post_dict(p: Post) -> dict:
    return {
        "id": p.id, "status": p.status, "idea": p.idea, "platform": p.platform,
        "platform_name": get_platform(p.platform).name, "prompt": p.prompt,
        "image_url": url_for(p.image_path), "raw_url": url_for(p.raw_path),
        "media_type": p.media_type or "image", "video_url": url_for(p.video_path), "duration": p.duration or 0,
        "width": p.width, "height": p.height, "caption": p.caption, "hashtags": p.hashtags or [],
        "alt_text": p.alt_text, "qa_score": p.qa_score, "qa_notes": p.qa_notes, "attempts": p.attempts,
        "qa_passed": p.qa_passed,
        "alternatives": [{"image_url": url_for(a.get("final")), "score": a.get("score"), "issues": a.get("issues", []),
                          "summary": a.get("summary", "")} for a in (p.alternatives or [])],
        "error": p.error, "parent_id": p.parent_id, "campaign_id": p.campaign_id,
        "subject_ids": p.subject_ids or [], "publish_log": p.publish_log or [],
        "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
    }


def subject_dict(s: Subject) -> dict:
    return {"id": s.id, "name": s.name, "kind": s.kind, "description": s.description, "notes": s.notes,
            "references": [url_for(r) for r in (s.references or [])],
            "alternatives": [url_for(a) for a in (s.alternatives or [])],
            "created_at": s.created_at.isoformat() + "Z" if s.created_at else None}


def brand_dict(b: Brand) -> dict:
    return {k: getattr(b, k) for k in ("id", "name", "description", "audience", "tone", "visual_style", "pillars",
                                       "base_hashtags", "platforms", "autopilot_daily", "autopilot_hour")}


def campaign_dict(c: Campaign, counts: dict | None = None) -> dict:
    return {"id": c.id, "brief": c.brief, "count": c.count, "platforms": c.platforms, "status": c.status,
            "error": c.error, "counts": counts or {},
            "created_at": c.created_at.isoformat() + "Z" if c.created_at else None}
