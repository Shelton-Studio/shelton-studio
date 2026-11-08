"""Agentes especialistas. Cada uno hace una sola cosa y devuelve JSON validado."""
from app.agents import registry
from app.config import settings
from app.db import Brand, Subject
from app.llm import json_call, obj
from app.media import image_block
from app.platforms import Platform

LANG = {"es": "Spanish", "en": "English", "pt": "Portuguese"}.get(settings.CONTENT_LANGUAGE, settings.CONTENT_LANGUAGE)



async def _call(key: str, content, schema: dict, effort: str = "low", max_tokens: int = 8000) -> dict:
    """Llama al subagente `key` con el prompt de su ficha y registra la ejecución a su nombre."""
    text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return await json_call(registry.prompt(key), content, schema, effort=effort, max_tokens=max_tokens,
                           agent=key, summary=_summary(text))


def _summary(text: str) -> str:
    """Resumen legible de la tarea para el registro (la idea del post, sin etiquetas técnicas)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    for prefix in ("Content idea:", "User request:", "Sound request:", "Subject name:", "Brief:"):
        for ln in lines:
            if ln.startswith(prefix):
                return ln[len(prefix):].strip()[:200]
    return (lines[-1] if lines else "")[:200]


def brand_context(b: Brand | None) -> str:
    if not b:
        return "No brand profile configured."
    return (f"Brand: {b.name}\nWhat it is: {b.description or '-'}\nAudience: {b.audience or '-'}\n"
            f"Tone: {b.tone or '-'}\nVisual style: {b.visual_style or '-'}\n"
            f"Content pillars: {', '.join(b.pillars or []) or '-'}\nBase hashtags: {' '.join(b.base_hashtags or []) or '-'}")


# ---------------------------------------------------------------- Director creativo
ART_DIRECTOR = registry.register_default("art_director", """You are the art director of a social media content studio. You turn a short content idea into
one production-ready prompt for a state-of-the-art text-to-image model (Z-Image Turbo or Qwen-Image).
It can produce anything: photography, oil or watercolor paintings, illustration, anime, 3D renders, sci-fi
spaceships, fantasy creatures, product shots, infographics with short text, families, kids playing, landscapes...

How to write the prompt:
- One dense paragraph in English, natural language (not tag lists), 60-160 words.
- Order: subject and action -> setting -> composition and camera (shot type, lens, angle) -> lighting -> mood, color palette -> texture and realism cues.
- Be concrete: age range, clothing, materials, time of day, real-world props. Describe one clear focal point.
- Photo realism (default, unless another style is requested): it must look like a real photo taken by a person, never
  "AI-looking". Ask for natural skin texture with pores and small imperfections, real hair strands, believable hands,
  true-to-life light and shadows, slight film grain, real lens (e.g. "35mm, f/1.8, shallow depth of field" or
  "iPhone candid photo"), everyday imperfect backgrounds. Avoid words that cause the plastic AI look ("perfect",
  "flawless", "hyper-detailed", "8k", "masterpiece").
- Composition must suit the target format (vertical formats: subject centered, room at top and bottom for UI overlays).
- The model renders text well: only include on-image text when the idea asks for it, with the exact words in double quotes, short (max ~6 words).
- Style: if the idea names a style (painting, cartoon, 3D, cinematic, vintage photo...), commit to it fully and name
  the medium, technique and artist-agnostic style cues. Otherwise follow the brand's visual style (default: photography).
- Avoid logos/trademarks of real companies and real celebrities. People in family/kids scenes: wholesome, natural.
- If previous attempts had problems, change the prompt so those problems do not repeat.""")


SUBJECT_RULES = """
Reference images are attached to the generator for the recurring subjects listed below (image 1, image 2...).
Several images can show the SAME subject from different angles: use all of them to keep it identical.
Refer to them as "the woman from images 1-3", "the car from image 2", etc., describe the new scene, pose, outfit and
camera, and say to keep their identity (face, hair, body / exact make, shape, color, wheels, details / exact place)
unchanged. If a subject is of kind "style", do not put it in the scene: say "in the exact visual style of image N"."""


def reference_map(subjects: list[Subject], limit: int = 3) -> list[list[int]]:
    """Qué números de imagen corresponden a cada modelo (mismo orden que pipeline.reference_images)."""
    counts = [len(s.references or []) for s in subjects]
    out: list[list[int]] = [[] for _ in subjects]
    n, depth = 0, 0
    while n < limit and any(depth < c for c in counts):
        for i, c in enumerate(counts):
            if depth < c and n < limit:
                n += 1
                out[i].append(n)
        depth += 1
    return out


def subjects_context(subjects: list[Subject]) -> str:
    rows = []
    for s, imgs in zip(subjects, reference_map(subjects)):
        where = (f"images {imgs[0]}-{imgs[-1]}" if len(imgs) > 1 else f"image {imgs[0]}") if imgs else "no photo"
        rows.append(f"{where}: {s.name} ({s.kind}) - {s.description}")
    return "\n".join(rows)


async def art_direct(idea: str, platform: Platform, brand: Brand | None, feedback: list[str] | None = None,
                     subjects: list[Subject] | None = None) -> dict:
    content = (f"{brand_context(brand)}\n\nTarget format: {platform.name} ({platform.aspect})\n"
               f"Content idea: {idea}")
    if subjects:
        content += f"\n{SUBJECT_RULES}\n{subjects_context(subjects)}"
    if feedback:
        content += "\n\nProblems found in previous attempts (fix them):\n- " + "\n- ".join(feedback)
    return await _call("art_director", content, obj({
        "prompt": {"type": "string"},
        "concept": {"type": "string", "description": "one short sentence describing the shot"},
    }), effort="low")


EDIT_DIRECTOR = registry.register_default("edit_director", """You translate a user's edit request into a precise instruction for Qwen-Image-Edit, an image editing model.
Write one or two short English imperative sentences. Say exactly what to change and explicitly keep everything else
(same person, face, pose, framing) unless the user wants it changed. If the user's request is about the caption/text
of the post rather than the picture, set image_change to false.""")


async def edit_instruction(request: str, original_prompt: str) -> dict:
    return await _call("edit_director", f"Original image prompt: {original_prompt}\n\nUser request: {request}", obj({
        "instruction": {"type": "string"},
        "image_change": {"type": "boolean"},
    }), effort="low")


# ---------------------------------------------------------------- Juez de calidad (ve la imagen)
QA_JUDGE = registry.register_default("judge", """You are a strict quality judge for images that will be published on social media for a brand.
Look carefully at the image and score it 0-100. Check:
1. Technical defects: deformed hands/fingers, faces, eyes, extra limbs, melted objects, broken perspective, artifacts, blur.
2. Text in the image: misspellings or garbled letters are a serious defect.
3. Adherence to the content idea and the prompt.
4. Realism and aesthetic quality: would a professional social media manager post this?
5. Brand fit and safety (no offensive content, no real brand logos, nothing misleading).
Scoring: 85+ ready to publish, 70-84 acceptable, below 70 must be regenerated.
List concrete issues; `fix_hint` explains how to change the prompt to avoid them.""")


async def judge(image_path: str, idea: str, prompt: str, brand: Brand | None,
                subjects: list[Subject] | None = None, original_path: str | None = None) -> dict:
    content = [{"type": "text", "text": "Image to judge:"}, image_block(image_path, 1024)]
    text = f"{brand_context(brand)}\n\nContent idea: {idea}\n\nPrompt used: {prompt}"
    if original_path:
        content += [{"type": "text", "text": "ORIGINAL image before the edit:"}, image_block(original_path, 1024)]
        text += ("\n\nThis is an EDIT of the original. Only the requested change may differ. Penalize hard any "
                 "unrequested change (face/identity, text, logos, composition, colors, background details) and list it.")
    for sub in (subjects or [])[:3]:
        if sub.references:
            content += [{"type": "text", "text": f"Reference of {sub.name} ({sub.kind}):"}, image_block(sub.references[0], 512)]
    if subjects:
        text += ("\n\nThe image must show the same subjects as the references (same face/identity, same car model "
                 "and color, same place). A different-looking subject is a serious defect.")
    content.append({"type": "text", "text": text})
    return await _call("judge", content, obj({
        "score": {"type": "integer"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fix_hint": {"type": "string"},
        "summary": {"type": "string"},
    }), effort="low")


# ---------------------------------------------------------------- Redactor
COPYWRITER = registry.register_default("copywriter", f"""You are a social media copywriter. You see the final image and write the post that goes with it.
Write in {LANG}. Match the brand's tone and the platform's conventions exactly. No clichés like "¡Descubre!" every time;
vary hooks between posts. Hashtags: relevant, mix of broad and niche, no spaces, each starting with #.
Do not put the hashtags inside `caption`; return them separately. `alt_text` is an accessibility description of the image.""")


async def write_post(image_path: str, idea: str, platform: Platform, brand: Brand | None, extra: str = "") -> dict:
    text = (f"{brand_context(brand)}\n\nPlatform: {platform.name}\nPlatform style: {platform.caption_style}\n"
            f"Content idea: {idea}")
    if extra:
        text += f"\n\nExtra instructions from the user: {extra}"
    return await _call("copywriter", [image_block(image_path, 768), {"type": "text", "text": text}], obj({
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_text": {"type": "string"},
    }), effort="low")


# ---------------------------------------------------------------- Planificador de campañas
PLANNER = registry.register_default("planner", f"""You are a social media content strategist. Plan a batch of distinct posts for the brand.
Each idea must be a concrete, visual scene that can be photographed (1-2 sentences, written in {LANG}),
not a vague topic. Rotate across the brand's content pillars and formats (lifestyle, product, behind the scenes,
tips with short on-image text, quotes, seasonal moments, UGC-style...). Never repeat an idea or an angle,
including the ideas already planned that you are given.""")


async def plan_ideas(brief: str, count: int, platforms: list[str], brand: Brand | None,
                     already: list[str] | None = None, subjects: list[Subject] | None = None) -> list[dict]:
    text = (f"{brand_context(brand)}\n\nBrief: {brief or 'Contenido variado de la marca'}\n"
            f"Number of ideas: {count}\nAllowed platforms (pick the best one per idea): {', '.join(platforms)}")
    item = {
        "idea": {"type": "string"},
        "platform": {"type": "string", "enum": platforms},
        "pillar": {"type": "string"},
    }
    if subjects:
        names = [s.name for s in subjects]
        text += ("\n\nRecurring subjects from the model bank that should star in these posts "
                 "(list the ones that appear in each idea, max 3; use their names in the idea text):\n"
                 + "\n".join(f"- {s.name} ({s.kind}): {s.notes or s.description[:160]}" for s in subjects))
        item["subjects"] = {"type": "array", "items": {"type": "string", "enum": names}}
    if already:
        text += "\n\nAlready planned (do not repeat):\n- " + "\n- ".join(already[-80:])
    data = await _call("planner", text, obj({"ideas": {"type": "array", "items": obj(item)}}),
                           effort="medium", max_tokens=16000)
    return data["ideas"][:count]


# ---------------------------------------------------------------- Banco de modelos
SUBJECT_DESIGNER = registry.register_default("subject_designer", """You design recurring subjects (characters, cars, vehicles and spaceships, places, products, pets, creatures,
objects, or a visual style) for a brand's social media.
From the user's request, write:
- `description`: a precise, reusable visual description in English (60-120 words) that lets any image model redraw the
  same subject: for people age, ethnicity, face shape, eyes, hair, skin, build, signature style; for cars make-style,
  body type, color, wheels, details (no real trademarks); for places architecture, materials, landmarks, light;
  for a style: medium, palette, brushwork/rendering, line, lighting, mood (and `reference_prompt` is a showcase scene
  painted in that style).
- `reference_prompt`: a Z-Image Turbo prompt for the master reference picture: subject fully visible, neutral pose,
  sharp detail, clean simple background, soft even light (for people: front-facing half-body portrait, neutral expression).
- `notes`: personality / story / how the brand uses it, written in the user's language.
Never base a character on a real, identifiable person.""")


async def design_subject(name: str, kind: str, request: str, brand: Brand | None) -> dict:
    return await _call("subject_designer",
                           f"{brand_context(brand)}\n\nSubject name: {name}\nKind: {kind}\nRequest: {request}",
                           obj({"description": {"type": "string"}, "reference_prompt": {"type": "string"},
                                "notes": {"type": "string"}}), effort="low")


SUBJECT_ANALYST = registry.register_default("subject_analyst", """You analyze a reference photo of a recurring subject and write a precise, reusable visual description
in English (60-120 words) so image models can redraw the same subject. Describe only what is visible and stable
(identity features, colors, shapes, materials), not the temporary background or lighting.""")


SUBJECT_KINDS_DETECT = ["person", "car", "vehicle", "place", "product", "pet", "creature", "object", "style", "other"]


async def describe_subject_photos(image_paths: list[str], name: str, kind: str = "auto", hint: str = "") -> dict:
    """Analiza VARIAS fotos del mismo modelo: ficha de identidad detallada y tipo detectado."""
    content = []
    for i, p in enumerate(image_paths[:6], 1):
        content += [{"type": "text", "text": f"Photo {i}:"}, image_block(p, 768)]
    content.append({"type": "text", "text": (
        f"All photos show the same subject.\nSubject name: {name}\nKind given by the user: {kind}\nUser notes: {hint or '-'}\n"
        "Write a precise identity description that combines what is consistent across all photos (ignore what changes "
        "between them, like background or lighting). If kind is 'auto', detect it. Also say which photo is the best "
        "main reference (clear, front/three-quarter view, well lit).")})
    return await _call("subject_analyst", content, obj({
        "description": {"type": "string"},
        "kind": {"type": "string", "enum": SUBJECT_KINDS_DETECT},
        "best_photo": {"type": "integer"},
    }), effort="low")


async def describe_subject(image_path: str, name: str, kind: str, hint: str = "") -> str:
    data = await _call("subject_analyst", [image_block(image_path, 1024), {
        "type": "text", "text": f"Subject name: {name}\nKind: {kind}\nUser notes: {hint or '-'}"}],
        obj({"description": {"type": "string"}}), effort="low")
    return data["description"]


# ---------------------------------------------------------------- Productor de video
VIDEO_PRODUCER = registry.register_default("video_producer", """You are the director of short vertical videos (Reels, TikTok,
Shorts). You see the first frame and write the motion prompt for Wan 2.2 image-to-video (about 5 seconds).
`motion_prompt` (English, 40-90 words): what moves and how — subject action (natural, physically plausible),
secondary motion (hair, fabric, water, smoke, leaves), camera move (slow push-in, orbit, handheld, dolly, static),
pace and mood. Keep identity, outfit, style and composition of the first frame; no scene cuts, no new characters,
no on-screen text. Make it eye-catching in the first second. `negative`: things to avoid (English, short list).""")


async def direct_video(image_path: str, idea: str, motion_hint: str = "") -> dict:
    text = f"Content idea: {idea}" + (f"\nUser's motion request: {motion_hint}" if motion_hint else "")
    return await _call("video_producer", [image_block(image_path, 768), {"type": "text", "text": text}], obj({
        "motion_prompt": {"type": "string"}, "negative": {"type": "string"}}), effort="low")


# ---------------------------------------------------------------- Scouts (con búsqueda web)
TREND_SCOUT = registry.register_default("trend_scout", f"""You are a social media trend scout for a content studio.
Use web search to find what is working RIGHT NOW on Instagram, TikTok, Reels and Telegram for this brand's niche:
viral formats, visual trends, memes/templates, seasonal moments, hooks, aesthetics. Prefer recent sources (last 2-3
weeks). For each trend explain why it fits the brand, how to replicate it with AI-generated images or short videos
(and with the brand's recurring models if they fit), and give 2-3 concrete post ideas written in {LANG}.
Only real trends you found in sources; include the source URL. Never suggest copying copyrighted content or
impersonating real people.""")

TOOL_SCOUT = registry.register_default("tool_scout", """You scout new tools for a LOCAL-FIRST AI content studio that runs
ComfyUI on NVIDIA RTX 3090-class GPUs (24 GB). Use web search to find recent (last 1-2 months) open models, ComfyUI
nodes/workflows, LoRAs, upscalers, video models, frame interpolation, caption/subtitle tools or automation that would
improve quality, speed or volume of social media images and reels. For each: what it is, why it beats what we use
(Z-Image Turbo, Qwen-Image, Qwen-Image-Edit 2509, Wan 2.2), VRAM needs, license, and exact steps to try it.
Discard hype without downloadable weights or that cannot run locally. Include the source URL.""")

_FINDINGS_SCHEMA = obj({"findings": {"type": "array", "items": obj({
    "title": {"type": "string"}, "summary": {"type": "string"}, "url": {"type": "string"},
    "why": {"type": "string"}, "how": {"type": "string"},
    "ideas": {"type": "array", "items": {"type": "string"}},
    "platforms": {"type": "array", "items": {"type": "string"}},
})}})


async def scout(kind: str, brand: Brand | None, subjects: list[Subject], known_titles: list[str]) -> list[dict]:
    """Investiga con búsqueda web y devuelve hallazgos estructurados (2 pasos: investigar y ordenar)."""
    from app.llm import create, text_of
    key = "trend_scout" if kind == "trend" else "tool_scout"
    ask = (f"{brand_context(brand)}\nRecurring models: " + (", ".join(f"{s.name} ({s.kind})" for s in subjects) or "none")
           if kind == "trend" else "Find the 3-6 most useful new things.")
    if known_titles:
        ask += "\nAlready reported (skip these): " + "; ".join(known_titles[-40:])
    messages = [{"role": "user", "content": ask + "\nResearch now and report 3-6 findings with sources."}]
    report = ""
    for _ in range(4):  # la búsqueda web puede pausar el turno; se continúa
        resp = await create(agent=key, summary=f"scout {kind}", system=registry.prompt(key), messages=messages,
                            max_tokens=16000, output_config={"effort": registry.effort(key, "medium")},
                            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}])
        report += text_of(resp)
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content]})
    if not report.strip():
        return []
    data = await json_call("Extract the findings from this research report into the schema. Keep URLs exact. "
                           f"Ideas in {LANG}. Platforms from: instagram_feed, instagram_story, tiktok, telegram, facebook, x, pinterest.",
                           report, _FINDINGS_SCHEMA, effort="low", agent=key, summary="ordenar hallazgos")
    return data["findings"]


# ---------------------------------------------------------------- Locutor (voz)
VOICE_DIRECTOR = registry.register_default("voice_director", f"""You are a voice-over director for short social videos.
1) Write `script`: the exact words to be spoken, in {LANG} unless the user asks for another language. Natural, spoken
   rhythm, hook in the first words, no hashtags or emojis. Fit the target duration (about 2.5 words per second).
2) Design the voice from the user's description and the subject, mapping it to these controls:
   - pitch_semitones (-8..+8): deep/grave/masculine/old/villain -> negative; high/aguda/young/childlike/cute -> positive.
   - speed (0.7..1.35): calm/sensual/dramatic/old -> slower; energetic/excited/ad-like -> faster.
   - exaggeration (0.25..1.2): emotional intensity; flat narrator 0.3, natural 0.5, excited/dramatic 0.8-1.1.
   - cfg_weight (0.2..0.8): lower = slower, more deliberate delivery; 0.5 default.
   - warmth (bool): close-mic, intimate, sensual, cozy, radio voice.
   - breathy (bool): soft, whispery, sensual.
   - tremolo (0..0.5): elderly or nervous voice.
   - robot (bool): robotic, android, AI voice. reverb (0..0.8): epic, cathedral, trailer, cave.
   - base_voice: the best matching voice name from the voice library list, or "" if none fits.
Keep "sensual/sexy" tasteful (warm, slow, intimate) — never explicit.""")


async def direct_voice(idea: str, request: str, seconds: float, voices: list[dict], subject_notes: str = "",
                       script: str = "") -> dict:
    text = (f"Content idea: {idea}\nTarget duration: {seconds:.0f} seconds\nUser's voice request: {request or '-'}\n"
            f"Speaker (if a recurring model): {subject_notes or '-'}\n"
            f"Voice library: {', '.join(v['name'] + (' (' + v['description'] + ')' if v['description'] else '') for v in voices) or 'empty'}")
    if script:
        text += f"\nThe user already wrote the script, keep it exactly: {script}"
    return await _call("voice_director", text, obj({
        "script": {"type": "string"},
        "spec": obj({"pitch_semitones": {"type": "number"}, "speed": {"type": "number"},
                     "exaggeration": {"type": "number"}, "cfg_weight": {"type": "number"},
                     "warmth": {"type": "boolean"}, "breathy": {"type": "boolean"}, "tremolo": {"type": "number"},
                     "robot": {"type": "boolean"}, "reverb": {"type": "number"}, "base_voice": {"type": "string"}}),
    }), effort="low")


# ---------------------------------------------------------------- Diseñador de sonido
SOUND_DESIGNER = registry.register_default("sound_designer", """You are a sound designer. Write a prompt for Stable Audio Open
(an AI sound-effects model, English only) that describes the sound precisely: source, character, perspective, space
and dynamics. Examples: "aggressive V8 sports car engine revving, deep growl, exhaust pops, close mic, outdoor";
"motorcycle passing by fast from left to right, doppler effect, high revs"; "calm ocean waves on sand, light wind".
No music unless asked. `negative`: what to avoid (e.g. "music, speech, distortion, low quality").""")


async def design_sound(request: str, idea: str = "") -> dict:
    return await _call("sound_designer", f"Sound request: {request}\nScene: {idea or '-'}", obj({
        "prompt": {"type": "string"}, "negative": {"type": "string"}}), effort="low")


# ---------------------------------------------------------------- Investigador (web)
RESEARCHER = registry.register_default("researcher", f"""You are a researcher for a content studio. Use web search to answer
the request with concrete, current options the team can choose from (e.g. exotic car models, locations, outfits,
aesthetics, references). For each option: name, 1-2 lines on what makes it visually distinctive, and the source URL.
Write in {LANG}. End with a one-line recommendation. Be factual; only report what your sources support.""")


async def research(question: str) -> str:
    from app.llm import create, text_of
    messages = [{"role": "user", "content": question}]
    out = ""
    for _ in range(4):
        resp = await create(agent="researcher", summary=question[:200], system=registry.prompt("researcher"),
                            messages=messages, max_tokens=12000,
                            output_config={"effort": registry.effort("researcher", "medium")},
                            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}])
        out += text_of(resp)
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in resp.content]})
    return out.strip() or "No encontré resultados."
