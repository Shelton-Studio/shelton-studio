"""Traductor español → inglés en la placa (sin nube) para SDXL/Flux en Shelton Hot."""
import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ["IMAGE_ENGINE"] = "mock"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="shelton_trad_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app import traductor  # noqa: E402


def test_detects_spanish_and_leaves_english_alone():
    assert traductor.looks_spanish("mujer desnuda de pie en la playa")
    assert not traductor.looks_spanish("adult woman, full body nude, standing on the beach")
    en, how = asyncio.run(traductor.to_english("adult woman, standing on the beach"))
    assert how == "" and en == "adult woman, standing on the beach"


def test_glossary_fallback_when_translator_not_ready(monkeypatch):
    monkeypatch.setattr(traductor, "find_python", lambda: None)
    en, how = asyncio.run(traductor.to_english("mujer desnuda de pie junto a la ventana, luz natural"))
    assert how == "glosario" and "woman" in en and "nude" in en and "natural light" in en


FAKE_SERVER = ("import json,sys\nprint('READY', flush=True)\nfor line in sys.stdin:\n"
               "    d=json.loads(line)\n    print(json.dumps({'texts':['EN '+t for t in d['texts']]}), flush=True)\n")


def _fake_server(monkeypatch, tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE_SERVER)
    monkeypatch.setattr(traductor, "SCRIPT", fake)
    monkeypatch.setattr(traductor, "engine", lambda: ("marian", sys.executable))
    monkeypatch.setattr(traductor, "_proc", None)
    monkeypatch.setattr(traductor, "_lock", None)
    traductor._cache.clear()
    return fake


def test_uses_the_local_translator_when_ready(monkeypatch, tmp_path):
    _fake_server(monkeypatch, tmp_path)

    async def go():
        a = await traductor.to_english("mujer en la playa al atardecer")
        b = await traductor.to_english("necesito una mujer")          # el mismo proceso, ya cargado
        traductor._proc.kill()
        return a, b
    a, b = asyncio.run(go())
    assert a == ("EN mujer en la playa al atardecer", "placa") and b == ("EN necesito una mujer", "placa")


def test_translator_script_splits_by_commas(monkeypatch, tmp_path):
    """El script real: con ctranslate2 y sentencepiece de mentira, traduce cada parte y las une con comas."""
    import traducir
    (tmp_path / "pkg" / "model").mkdir(parents=True)
    (tmp_path / "pkg" / "model" / "model.bin").write_bytes(b"x")
    (tmp_path / "pkg" / "sentencepiece.model").write_bytes(b"x")

    class SP:
        def __init__(self, model_file):
            pass

        def encode(self, text, out_type=str):
            return ["▁" + w for w in text.split()]

    class Res:
        def __init__(self, toks):
            self.hypotheses = [[t.upper() for t in toks]]

    class Tr:
        def __init__(self, path, device="cpu"):
            assert device == "cpu"

        def translate_batch(self, batch, **k):
            return [Res(b) for b in batch]
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(Translator=Tr))
    monkeypatch.setitem(sys.modules, "sentencepiece", types.SimpleNamespace(SentencePieceProcessor=SP))
    out = traducir.translate(tmp_path, ["mujer en la playa, luz suave"])
    assert out == ["MUJER EN LA PLAYA, LUZ SUAVE"]


def test_adult_zone_sends_english_to_sdxl(monkeypatch):
    import test_chain  # noqa: F401
    from test_lora import _png
    from app import privado
    from app.db import init_db
    init_db()
    monkeypatch.setattr(traductor, "find_python", lambda: None)
    seen = []

    class Eng:
        name = "comfyui"
        values = {"generate": {}}

        async def generate(self, prompt, w, h, seed, on_progress=None):
            seen.append(prompt)
            return _png("red")
    monkeypatch.setattr(privado, "_engine", lambda preset, ckpt: ({"preset": preset}, Eng()))

    async def go():
        r = await privado.generate("admin", "mujer desnuda de pie en la playa", "", "sdxl", ckpt="x.safetensors")
        for _ in range(100):
            await asyncio.sleep(0.02)
            if seen:
                break
        return r
    r = asyncio.run(go())
    assert r["translated"] == "glosario" and "woman" in seen[0] and "nude" in seen[0]
    try:
        asyncio.run(privado.generate("admin", "chica en la playa, teen", "", "sdxl", ckpt="x.safetensors"))
    except ValueError as e:
        assert "Bloqueado" in str(e)
    else:
        raise AssertionError


def test_live_translation_endpoint_and_cache(monkeypatch, tmp_path):
    from app import privado
    _fake_server(monkeypatch, tmp_path)
    monkeypatch.setattr(privado, "_admin", lambda req: {"username": "admin"})

    async def go():
        r = await privado.api_translate(privado.TranslateIn(text="mujer de pie en la playa"), None)
        traductor._proc.kill()
        await traductor._proc.wait()
        return r
    r = asyncio.run(go())
    assert r["en"] == "EN mujer de pie en la playa" and r["how"] == "placa"
    traductor._proc = None
    traductor._lock = None
    monkeypatch.setattr(traductor, "engine", lambda: None)               # la segunda vez sale de la memoria
    assert asyncio.run(traductor.to_english("mujer de pie en la playa"))[0] == "EN mujer de pie en la playa"


def test_short_spanish_is_detected_and_marian_layout_is_ready(tmp_path):
    import traducir
    assert traductor.looks_spanish("necesito una vagina") or traductor.looks_spanish("necesito una mujer")
    assert traductor.looks_spanish("quiero una foto")
    (tmp_path / "opus-mt-es-en").mkdir()
    (tmp_path / "opus-mt-es-en" / "config.json").write_text("{}")
    assert traducir.ready(tmp_path) == "marian"
    pkg = tmp_path / "x"
    (pkg / "model").mkdir(parents=True)
    (pkg / "model" / "model.bin").write_bytes(b"x")
    (pkg / "bpe.model").write_bytes(b"x")                                 # Argos 1.9 trae BPE: no sirve
    assert traducir._find(pkg) is None
