#!/usr/bin/env python3
"""Traductor español → inglés que corre en la placa, sin ninguna IA de la nube.

Motor principal: el modelo abierto Helsinki-NLP/opus-mt-es-en (MarianMT) con `transformers` + `torch` en la CPU,
que ya vienen con el Python de ComfyUI. Respaldo: un paquete de Argos con `ctranslate2` + `sentencepiece`.

    python traducir.py --setup --dir DIR                  # baja el modelo (una vez, ~300 MB)
    echo '{"texts": ["mujer en la playa"]}' | python traducir.py --dir DIR
    python traducir.py --serve --dir DIR                  # queda abierto: una línea JSON por pedido (en vivo)

Usa la CPU: no le quita la GPU a los entrenamientos ni a las fotos.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

MARIAN = "Helsinki-NLP/opus-mt-es-en"
INDEX = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


def marian_dir(root: Path) -> Path:
    return root / "opus-mt-es-en"


def marian_ready(root: Path) -> bool:
    return (marian_dir(root) / "config.json").exists()


def _find(root: Path) -> tuple[Path, Path] | None:
    """Paquete de Argos usable: modelo ctranslate2 + tokenizador sentencepiece (los que traen «bpe.model» no sirven)."""
    if not root.exists():
        return None
    for sp in sorted(root.rglob("sentencepiece.model")):
        if "stanza" in sp.parts:
            continue
        for model in (sp.parent / "model", sp.parent):
            if (model / "model.bin").exists():
                return model, sp
    return None


def ready(root: Path) -> str:
    return "marian" if marian_ready(root) else "argos" if _find(root) else ""


def setup(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if marian_ready(root):
        print("ya está")
        return
    try:
        from transformers import MarianMTModel, MarianTokenizer
    except Exception:  # noqa: BLE001
        MarianMTModel = None
    if MarianMTModel is not None:
        tok = MarianTokenizer.from_pretrained(MARIAN)
        model = MarianMTModel.from_pretrained(MARIAN)
        tok.save_pretrained(marian_dir(root))
        model.save_pretrained(marian_dir(root))
        print("listo (opus-mt-es-en)")
        return
    if _find(root):
        print("ya está (argos)")
        return
    with urllib.request.urlopen(INDEX, timeout=60) as r:
        index = json.load(r)
    pkg = next(p for p in index if p.get("from_code") == "es" and p.get("to_code") == "en")
    for link in pkg.get("links") or []:
        req = urllib.request.Request(link, headers={"User-Agent": "shelton-translator"})
        with urllib.request.urlopen(req, timeout=300) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(root)
        break
    if not _find(root):
        raise SystemExit("Este Python no tiene «transformers» y el paquete de Argos no trae sentencepiece")
    print("listo (argos)")


def _parts(text: str) -> list[str]:
    # los pedidos suelen ser listas separadas por comas: se traduce cada parte y se vuelve a unir
    return [p.strip() for p in re.split(r"[,\n;]+", text) if p.strip()]


class Translator:
    def __init__(self, root: Path):
        self.kind = ready(root)
        if self.kind == "marian":
            import torch
            from transformers import MarianMTModel, MarianTokenizer
            torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
            self.torch = torch
            self.tok = MarianTokenizer.from_pretrained(marian_dir(root))
            self.model = MarianMTModel.from_pretrained(marian_dir(root)).eval()
        elif self.kind == "argos":
            import ctranslate2
            import sentencepiece as spm
            model, sp_path = _find(root)
            self.sp = spm.SentencePieceProcessor(model_file=str(sp_path))
            self.tr = ctranslate2.Translator(str(model), device="cpu")
        else:
            raise SystemExit("Falta el modelo: corre con --setup")

    def _batch(self, parts: list[str]) -> list[str]:
        if self.kind == "marian":
            batch = self.tok(parts, return_tensors="pt", padding=True, truncation=True, max_length=256)
            with self.torch.no_grad():
                out = self.model.generate(**batch, num_beams=2, max_new_tokens=256)
            return [t.strip().rstrip(".") for t in self.tok.batch_decode(out, skip_special_tokens=True)]
        res = self.tr.translate_batch([self.sp.encode(p, out_type=str) for p in parts], beam_size=2,
                                      max_decoding_length=256)
        return ["".join(r.hypotheses[0]).replace("▁", " ").strip().rstrip(".") for r in res]

    def translate(self, texts: list[str]) -> list[str]:
        out = []
        for text in texts:
            parts = _parts(text)
            out.append(", ".join(d for d in self._batch(parts) if d) if parts else text)
        return out


def translate(root: Path, texts: list[str]) -> list[str]:
    return Translator(root).translate(texts)


def serve(root: Path) -> None:
    """Queda abierto con el modelo cargado: responde al instante mientras se escribe."""
    tr = Translator(root)
    print("READY", flush=True)
    for line in sys.stdin:
        try:
            texts = json.loads(line).get("texts") or []
            print(json.dumps({"texts": tr.translate(texts)}, ensure_ascii=False), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"error": str(e)[:300]}), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--serve", action="store_true")
    a = ap.parse_args()
    root = Path(a.dir)
    if a.setup:
        setup(root)
    elif a.serve:
        serve(root)
    else:
        texts = json.load(sys.stdin).get("texts") or []
        json.dump({"texts": translate(root, texts)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
