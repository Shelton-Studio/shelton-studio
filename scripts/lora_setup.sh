#!/usr/bin/env bash
# Entrenador de LoRAs para Shelton (ai-toolkit) en la PC con la GPU. Correr como root (sudo -i), una sola vez:
#   bash ~/shelton-studio/scripts/lora_setup.sh
#
# - Instala ai-toolkit en ~/ai-toolkit con su propio Python 3.12 (no toca el Python del sistema ni otros programas)
# - Lo usa Shelton desde el Banco de modelos: botón «Entrenar LoRA»
# - La primera vez que entrenes, ai-toolkit baja Z-Image Turbo para entrenar (~20 GB) y su adaptador oficial
# Volver a correrlo = actualizar ai-toolkit.
set -euo pipefail
say() { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }
ok() { printf "  \033[1;32m✔\033[0m %s\n" "$*"; }
die() { printf "\n\033[1;31m✖ %s\033[0m\n" "$*"; exit 1; }

AITK="${AITK_DIR:-$HOME/ai-toolkit}"
COMFY="${COMFY_DIR:-$HOME/ComfyUI}"
SHELTON_DIR="${SHELTON_DIR:-$HOME/shelton-studio}"
[ "$(id -u)" = "0" ] || die "Córrelo como root: sudo -i y luego este comando"
command -v nvidia-smi >/dev/null || die "No hay driver de NVIDIA"
[ -x "$COMFY/venv/bin/python" ] || die "Primero instala la IA local: bash $SHELTON_DIR/scripts/gpu_setup.sh"

say "ai-toolkit en $AITK"
if [ -d "$AITK/.git" ]; then git -C "$AITK" pull -q --ff-only && ok "Actualizado"
else git clone -q https://github.com/ostris/ai-toolkit "$AITK" && ok "Descargado"; fi
git -C "$AITK" submodule update --init --recursive -q 2>/dev/null || true

say "Python 3.12 propio para el entrenador"
"$COMFY/venv/bin/pip" install -q uv
UV="$COMFY/venv/bin/uv"
[ -x "$AITK/venv/bin/python" ] || "$UV" venv -q --python 3.12 "$AITK/venv"
"$AITK/venv/bin/python" --version | sed 's/^/  /'

say "Dependencias (PyTorch + ai-toolkit, unos minutos)"
"$UV" pip install -q --python "$AITK/venv/bin/python" torch torchvision torchaudio \
  --index-url "https://download.pytorch.org/whl/${CUDA_WHEEL:-cu128}"
"$UV" pip install -q --python "$AITK/venv/bin/python" -r "$AITK/requirements.txt"
"$AITK/venv/bin/python" -c "import torch;assert torch.cuda.is_available(),'CUDA no disponible';print('  GPU para entrenar:',torch.cuda.get_device_name(0))"

say "Conectando con Shelton"
ENV="$SHELTON_DIR/.env"
LORAS="$COMFY/models/loras"
mkdir -p "$LORAS"
grep -qE '^AITK_DIR=' "$ENV" && sed -i "s#^AITK_DIR=.*#AITK_DIR=$AITK#" "$ENV" || echo "AITK_DIR=$AITK" >> "$ENV"
grep -qE '^COMFY_LORA_DIR=' "$ENV" && sed -i "s#^COMFY_LORA_DIR=.*#COMFY_LORA_DIR=$LORAS#" "$ENV" || echo "COMFY_LORA_DIR=$LORAS" >> "$ENV"
systemctl restart shelton-studio 2>/dev/null || true
ok "Listo. En Shelton: Banco de modelos → «🎓 Entrenar» en la ficha del modelo (necesita 8+ fotos, mejor 15-30)."
echo "  Mientras entrena, mira el avance en el Banco o en «En vivo». Tarda ~1-2 h en la 3090."
