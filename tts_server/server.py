"""Servidor de voz local para Shelton: Chatterbox Multilingual (Resemble AI, licencia MIT).

- 23 idiomas, incluido español.
- Clona una voz con 5-10 s de muestra (ref_audio_b64).
- Controles: exaggeration (expresividad) y cfg_weight (ritmo).

Arranque:  python tts_server/server.py        (puerto 8189; cambia con TTS_PORT)
Shelton lo usa en http://127.0.0.1:8189 (TTS_URL en .env o conexión "Voz local" en la web).
La primera vez descarga el modelo (~2-3 GB) desde Hugging Face.
"""
import base64
import io
import os
import tempfile

import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="Shelton TTS")
_model = None


def model():
    global _model
    if _model is None:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    return _model


class TTSIn(BaseModel):
    text: str
    language: str = "es"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    ref_audio_b64: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "loaded": _model is not None}


@app.post("/tts")
def tts(body: TTSIn):
    if not body.text.strip():
        raise HTTPException(400, "Texto vacío")
    ref_path = None
    try:
        if body.ref_audio_b64:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(base64.b64decode(body.ref_audio_b64))
                ref_path = f.name
        m = model()
        kwargs = dict(language_id=body.language[:2], exaggeration=max(0.25, min(body.exaggeration, 2.0)),
                      cfg_weight=max(0.0, min(body.cfg_weight, 1.0)))
        if ref_path:
            kwargs["audio_prompt_path"] = ref_path
        wav = m.generate(body.text, **kwargs)
        buf = io.BytesIO()
        sf.write(buf, wav.squeeze(0).cpu().numpy(), m.sr, format="WAV")
        return Response(buf.getvalue(), media_type="audio/wav")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    finally:
        if ref_path:
            os.unlink(ref_path)


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("TTS_HOST", "127.0.0.1"), port=int(os.getenv("TTS_PORT", "8189")))
