#!/usr/bin/env python3
"""Traductor español → inglés que corre en la placa, sin ninguna IA de la nube (modelo abierto de Argos/OPUS).

    python traducir.py --setup --dir DIR      # baja el modelo (una vez, ~100 MB)
    echo '{"texts": ["mujer en la playa"]}' | python traducir.py --dir DIR

Necesita `ctranslate2` y `sentencepiece` en el Python con que se corre (Shelton busca uno que los tenga).
Usa la CPU: no le quita la GPU a los entrenamientos ni a las fotos.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

INDEX = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


def _find(root: Path) -> tuple[Path, Path] | None:
    """Carpeta del modelo ctranslate2 y su sentencepiece dentro del paquete descomprimido."""
    for sp in root.rglob("sentencepiece.model"):
        model = sp.parent / "model"
        if (model / "model.bin").exists():
            return model, sp
    return None


def setup(root: Path) -> None:
    if _find(root):
        print("ya está")
        return
    with urllib.request.urlopen(INDEX, timeout=60) as r:
        index = json.load(r)
    pkg = next(p for p in index if p.get("from_code") == "es" and p.get("to_code") == "en")
    root.mkdir(parents=True, exist_ok=True)
    last = None
    for link in pkg.get("links") or []:
        try:
            req = urllib.request.Request(link, headers={"User-Agent": "shelton-translator"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            zipfile.ZipFile(io.BytesIO(data)).extractall(root)
            break
        except Exception as e:  # noqa: BLE001
            last = e
    if not _find(root):
        raise SystemExit(f"No pude bajar el modelo de traducción: {last or 'formato desconocido'}")
    print("listo")


def translate(root: Path, texts: list[str]) -> list[str]:
    import ctranslate2
    import sentencepiece as spm
    found = _find(root)
    if not found:
        raise SystemExit("Falta el modelo: corre con --setup")
    model, sp_path = found
    sp = spm.SentencePieceProcessor(model_file=str(sp_path))
    tr = ctranslate2.Translator(str(model), device="cpu")
    out = []
    for text in texts:
        # los pedidos suelen ser listas separadas por comas: se traduce cada parte y se vuelve a unir
        parts = [p.strip() for p in re.split(r"[,\n;]+", text) if p.strip()]
        if not parts:
            out.append(text)
            continue
        res = tr.translate_batch([sp.encode(p, out_type=str) for p in parts], beam_size=2, max_decoding_length=256)
        done = ["".join(r.hypotheses[0]).replace("▁", " ").strip().rstrip(".") for r in res]
        out.append(", ".join(d for d in done if d))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--setup", action="store_true")
    a = ap.parse_args()
    root = Path(a.dir)
    if a.setup:
        setup(root)
        return
    texts = json.load(sys.stdin).get("texts") or []
    json.dump({"texts": translate(root, texts)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
