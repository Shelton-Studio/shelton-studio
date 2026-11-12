#!/usr/bin/env bash
# Mejora de rostro para ComfyUI (calidad máxima): Impact Pack + Impact Subpack + detector de caras.
#   bash quality_nodes.sh /ruta/a/ComfyUI
# Se puede volver a correr (actualiza). Si falla, Shelton sigue funcionando: la calidad máxima se hace sin rostro.
set -euo pipefail
COMFY="${1:-${COMFY_DIR:-$HOME/ComfyUI}}"
[ -d "$COMFY/custom_nodes" ] || { echo "  ✖ No encuentro ComfyUI en $COMFY"; exit 1; }
PY="$COMFY/venv/bin/python"; [ -x "$PY" ] || PY=python3
for repo in ComfyUI-Impact-Pack ComfyUI-Impact-Subpack; do
  d="$COMFY/custom_nodes/$repo"
  if [ -d "$d/.git" ]; then git -C "$d" pull -q --ff-only || true
  else git clone -q "https://github.com/ltdrdata/$repo" "$d"; fi
  [ -f "$d/requirements.txt" ] && "$PY" -m pip install -q -r "$d/requirements.txt"
done
mkdir -p "$COMFY/models/ultralytics/bbox"
F="$COMFY/models/ultralytics/bbox/face_yolov8m.pt"
[ -s "$F" ] || curl -fsSL -o "$F" "https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt"
echo "  ✔ Mejora de rostro instalada (Impact Pack + detector de caras)"
