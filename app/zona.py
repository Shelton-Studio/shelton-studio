"""Qué pertenece SOLO a Shelton Hot y nunca se muestra en el Shelton normal."""


def adult_only(sub) -> bool:
    """La LoRA de estilo de Shelton Hot y la 🔞 LoRA adulta de cada modelo: viven solo en Shelton Hot."""
    info = getattr(sub, "lora_info", None) or {}
    return bool(info.get("adult_style") or info.get("adult_twin"))
