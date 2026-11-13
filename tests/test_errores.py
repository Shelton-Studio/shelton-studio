"""Errores de la nube explicados en español."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.errores import friendly  # noqa: E402


def test_fal_locked_balance_is_explained():
    e = 'fal.ai 403 (bytedance/seedream/v5/pro/text-to-image): {"detail":"User is locked. Reason: Exhausted balance."}'
    out = friendly(e)
    assert out.startswith("⚠ fal.ai: la cuenta de fal") and "Detalle: fal.ai 403" in out
    assert friendly(out) == out                                   # no se duplica


def test_other_errors():
    assert "🎨 Modelos" in friendly("fal.ai 404 (fal-ai/x): Application not found")
    assert "clave" in friendly("Higgsfield rechazó la clave: revisa que pegaste la clave completa en Conexiones")
    assert friendly("algo raro") == "algo raro"
