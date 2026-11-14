"""Errores de los servicios en la nube explicados en español simple (qué pasó y qué hacer), sin perder el original."""
from __future__ import annotations

import re

RULES = [
    (r"exhausted balance|user is locked", "fal.ai: la cuenta de fal dueña de la clave no tiene saldo o está bloqueada. "
     "Revisa Billing en fal.ai (o pide a su soporte que la destrabe) y que la clave sea de esa misma cuenta."),
    (r"higgsfield.*(credit|balance|insufficient|402)|(credit|balance|insufficient).*higgsfield",
     "Higgsfield: la API no tiene saldo. El saldo de la API es aparte del de la app: cárgalo en console.higgsfield.ai."),
    (r"openrouter.*(402|credit|insufficient)|(402|insufficient credits).*openrouter",
     "OpenRouter (el cerebro de Shelton) no tiene saldo: recarga en openrouter.ai → Credits."),
    (r"fal\.ai (401|403)|higgsfield rechazó la clave|invalid (api )?key|unauthorized",
     "La clave de ese servicio no es válida: vuelve a pegarla completa en Conexiones."),
    (r"(fal\.ai|higgsfield) 404|not found.*model|model.*not found|application .* not found",
     "Ese modelo no existe (o cambió de nombre) en ese servicio: elige otro en 🎨 Modelos."),
    (r"(fal\.ai|higgsfield) (400|422)", "El modelo elegido no aceptó el pedido: prueba otro modelo en 🎨 Modelos."),
    (r"content.*(policy|moderation|safety|nsfw)|flagged|safety checker",
     "El servicio en la nube rechazó el contenido por sus reglas. Para eso usa tu placa (o Shelton Hot si es adulto)."),
    (r"timed? ?out|timeout", "El servicio tardó demasiado en responder. Prueba de nuevo en un rato."),
]


def friendly(error: str) -> str:
    """«fal.ai 403 (…): User is locked…» → «fal.ai: la cuenta… — Detalle: fal.ai 403 …»."""
    text = (error or "").strip()
    if not text or text.startswith("⚠"):
        return text
    for pattern, msg in RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return f"⚠ {msg} — Detalle: {text}"[:1000]
    return text
