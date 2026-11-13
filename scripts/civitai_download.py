#!/usr/bin/env python3
"""Descarga checkpoints de Civitai a la carpeta de ComfyUI de la placa (para la Adult Zone y SDXL).

Uso (en la placa, como root):
    python3 civitai_download.py                      # CyberRealistic XL + Pony Diffusion V6 XL
    python3 civitai_download.py "Juggernaut XL"      # otro por nombre
    python3 civitai_download.py https://civitai.com/models/12345   # o por su link

Pide tu clave de Civitai sin mostrarla (o la toma de CIVITAI_TOKEN). La clave no se guarda en ningún lado.
Solo usa Python, sin instalar nada. Si el archivo ya está, no lo baja de nuevo.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://civitai.com/api/v1"
DEFAULTS = [("CyberRealistic XL", ("SDXL 1.0",)), ("Pony Diffusion V6 XL", ("Pony",))]
DEST = Path(os.getenv("COMFY_DIR", str(Path.home() / "ComfyUI"))) / "models" / "checkpoints"


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "shelton-downloader"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def find(query: str, token: str, bases=("SDXL 1.0", "Pony")) -> tuple[dict, dict] | None:
    """Devuelve (modelo, versión) del checkpoint pedido: por link/id o buscando por nombre."""
    m = re.search(r"civitai\.com/models/(\d+)", query) or re.fullmatch(r"\s*(\d+)\s*", query)
    if m:
        model = _get(f"{API}/models/{m.group(1)}", token)
        return model, (model.get("modelVersions") or [None])[0]
    q = urllib.parse.urlencode({"query": query, "types": "Checkpoint", "limit": 20, "nsfw": "true"})
    items = _get(f"{API}/models?{q}", token).get("items") or []
    words = [w for w in re.findall(r"\w+", query.lower()) if w not in ("xl", "v6")]
    for it in items:
        name = it.get("name", "").lower()
        v = next((v for v in it.get("modelVersions") or [] if v.get("baseModel") in bases), None)
        if v and all(w in name for w in words):
            return it, v
    for it in items:                                    # si no coincide exacto, el primero del tipo correcto
        v = next((v for v in it.get("modelVersions") or [] if v.get("baseModel") in bases), None)
        if v:
            return it, v
    return None


def download(model: dict, version: dict, token: str) -> Path:
    files = version.get("files") or []
    f = next((x for x in files if x.get("primary") and x.get("name", "").endswith(".safetensors")), None) or \
        next((x for x in files if x.get("name", "").endswith(".safetensors")), None) or (files[0] if files else None)
    if not f:
        raise RuntimeError("Ese modelo no tiene archivo para descargar")
    out = DEST / Path(f["name"]).name
    if out.exists() and out.stat().st_size > 1_000_000:
        print(f"  ✔ Ya estaba: {out.name}")
        return out
    url = f.get("downloadUrl") or version.get("downloadUrl")
    url += ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(token)
    tmp = out.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "shelton-downloader"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as w:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -1
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            w.write(chunk)
            done += len(chunk)
            pct = int(100 * done / total) if total else -1
            if pct != last and (pct % 5 == 0 or pct < 0):
                print(f"\r  ⬇ {out.name}: {pct}% ({done // (1 << 20)} MB)" if pct >= 0 else f"\r  ⬇ {done // (1 << 20)} MB",
                      end="", flush=True)
                last = pct
    print()
    if done < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("La descarga vino vacía: revisa que la clave de Civitai sea correcta")
    tmp.rename(out)
    print(f"  ✔ Listo: {out.name} ({done // (1 << 20)} MB)")
    return out


def main() -> int:
    token = os.getenv("CIVITAI_TOKEN") or getpass.getpass("Pega tu clave (API key) de Civitai y Enter (no se ve): ").strip()
    if not token:
        print("Falta la clave de Civitai")
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    wanted = [(a, ("SDXL 1.0", "Pony")) for a in sys.argv[1:]] or DEFAULTS
    ok = 0
    for query, bases in wanted:
        print(f"\n▶ {query}")
        try:
            hit = find(query, token, bases)
            if not hit or not hit[1]:
                print("  ✖ No lo encontré en Civitai. Prueba con su link: python3 civitai_download.py https://civitai.com/models/…")
                continue
            model, version = hit
            print(f"  Encontrado: {model.get('name')} · {version.get('name')} ({version.get('baseModel')})")
            download(model, version, token)
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"  ✖ Civitai respondió {e.code}" + (": la clave no es válida" if e.code in (401, 403) else ""))
        except Exception as e:  # noqa: BLE001
            print(f"  ✖ {e}")
    print(f"\n{ok} de {len(wanted)} listos en {DEST}")
    print("Recarga la Adult Zone (F5): aparecen en «Checkpoint (SDXL)». No hace falta reiniciar nada.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
