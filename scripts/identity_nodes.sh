#!/usr/bin/env bash
# 🪪 Cara por foto (InstantID sobre SDXL) para el ComfyUI de Shelton.
#   bash ~/shelton-studio/scripts/identity_nodes.sh [/ruta/a/ComfyUI]
# Se puede volver a correr: lo que ya está no se baja de nuevo. Si falla, Shelton sigue igual (solo no hay cara por foto).
#
# LICENCIAS: el adaptador de InstantID es Apache-2.0, pero los modelos de caras de InsightFace (antelopev2) son
# SOLO para investigación, no para uso comercial. Antes de vender con esto, conseguir la licencia de InsightFace.
set -euo pipefail
COMFY="${1:-${COMFY_DIR:-$HOME/ComfyUI}}"
[ -d "$COMFY/custom_nodes" ] || { echo "  ✖ No encuentro ComfyUI en $COMFY"; exit 1; }
PY="$COMFY/venv/bin/python"; [ -x "$PY" ] || PY=python3
HF="https://huggingface.co"

get() {   # get URL DESTINO: baja solo si falta (a un .part y después lo mueve, para no dejar archivos a medias)
  [ -s "$2" ] && return 0
  mkdir -p "$(dirname "$2")"
  curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}

D="$COMFY/custom_nodes/ComfyUI_InstantID"
if [ -d "$D/.git" ]; then git -C "$D" pull -q --ff-only || true
else git clone -q https://github.com/cubiq/ComfyUI_InstantID "$D"; fi
"$PY" -m pip install -q insightface onnxruntime-gpu || {
  echo "  ⚠ insightface no se instaló: hace falta compilar. Prueba: apt-get install -y build-essential python3-dev y corre esto de nuevo"; exit 1; }

get "$HF/InstantX/InstantID/resolve/main/ip-adapter.bin" "$COMFY/models/instantid/ip-adapter.bin"
get "$HF/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors" \
    "$COMFY/models/controlnet/instantid_controlnet.safetensors"
A="$COMFY/models/insightface/models/antelopev2"
if [ ! -s "$A/glintr100.onnx" ]; then
  mkdir -p "$A"
  get "$HF/MonsterMMORPG/tools/resolve/main/antelopev2.zip" "/tmp/antelopev2.zip"
  "$PY" - "$A" <<'PYEOF'
import sys, zipfile, os
dest = sys.argv[1]
with zipfile.ZipFile("/tmp/antelopev2.zip") as z:
    for n in z.namelist():
        if n.endswith(".onnx"):
            with z.open(n) as src, open(os.path.join(dest, os.path.basename(n)), "wb") as out:
                out.write(src.read())
PYEOF
  rm -f /tmp/antelopev2.zip
fi
[ -s "$A/glintr100.onnx" ] || { echo "  ✖ Falta el modelo de caras antelopev2 en $A"; exit 1; }
echo "  ✔ Cara por foto instalada (InstantID). Reinicia ComfyUI para que la vea: systemctl restart ${SVC:-shelton-studio-comfy}"
echo "  ⚠ Licencia: los modelos de caras de InsightFace son solo para investigación (no comercial)."
