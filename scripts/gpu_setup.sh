#!/usr/bin/env bash
# IA local de Shelton en la PC con la GPU (Ubuntu/Debian + NVIDIA). Correr como root (sudo -i), DESPUÉS de install.sh:
#   bash ~/shelton-studio/scripts/gpu_setup.sh
#
# - Instala ComfyUI (imágenes, edición, video, sonido) en ~/ComfyUI como servicio «shelton-studio-comfy» en 127.0.0.1:8288
# - Instala la voz local (Chatterbox, tts_server) como servicio «shelton-studio-voz» en 127.0.0.1:8289
# - Nombres y puertos propios: no pisa otros ComfyUI, voces ni servicios que ya existan en la máquina
# - Descarga los modelos para la RTX 3090 (o los grupos que indiques: SHELTON_MODELS="imagen edicion")
# - Todo escucha solo dentro de la PC: se usa a través de Shelton (nadie de afuera llega a la GPU)
# Volver a correrlo = actualizar ComfyUI y completar modelos que falten.
set -euo pipefail
say() { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }
ok() { printf "  \033[1;32m✔\033[0m %s\n" "$*"; }
die() { printf "\n\033[1;31m✖ %s\033[0m\n" "$*"; exit 1; }

SHELTON_DIR="${SHELTON_DIR:-$HOME/shelton-studio}"
COMFY="${COMFY_DIR:-$HOME/ComfyUI}"
COMFY_PORT="${COMFY_PORT:-8288}"
VOZ_PORT="${VOZ_PORT:-8289}"
port_busy() { python3 -c "import socket,sys;s=socket.socket();s.settimeout(.3);sys.exit(0 if s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0 else 1)" "$1"; }
[ "$(id -u)" = "0" ] || die "Córrelo como root: sudo -i y luego este comando"
[ -d "$SHELTON_DIR" ] || die "Primero instala Shelton (install.sh). No encuentro $SHELTON_DIR"

say "Revisando la GPU"
command -v nvidia-smi >/dev/null || die "No hay driver de NVIDIA. Instálalo: ubuntu-drivers install && reboot"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | sed 's/^/  /'
command -v git >/dev/null || apt-get install -y git >/dev/null
python3 -c "import ensurepip" 2>/dev/null || apt-get install -y python3-venv >/dev/null
ok "GPU lista"
USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -n1)
[ "${USED:-0}" -gt 4000 ] && echo "  ⚠ La GPU ya tiene ${USED} MB en uso por otros programas: los modelos grandes (video 14B) pueden quedarse sin memoria. Coordina con quien los usa."
for P in "$COMFY_PORT" "$VOZ_PORT"; do
  if port_busy "$P" && ! systemctl is-active -q shelton-studio-comfy shelton-studio-voz 2>/dev/null; then
    die "El puerto $P ya lo usa otro programa. Elige otros: COMFY_PORT=8388 VOZ_PORT=8389 bash $0"
  fi
done

say "ComfyUI en $COMFY"
if [ -d "$COMFY/.git" ]; then git -C "$COMFY" pull -q --ff-only && ok "Actualizado"
else git clone -q https://github.com/comfyanonymous/ComfyUI "$COMFY" && ok "Descargado"; fi
[ -x "$COMFY/venv/bin/python" ] || python3 -m venv "$COMFY/venv"
"$COMFY/venv/bin/pip" install -q --upgrade pip
# PyTorch con CUDA (sirve para RTX 30xx/40xx/50xx). Cambia CUDA_WHEEL si tu driver es muy viejo (ej: cu124).
"$COMFY/venv/bin/pip" install -q torch torchvision torchaudio --extra-index-url "https://download.pytorch.org/whl/${CUDA_WHEEL:-cu128}"
"$COMFY/venv/bin/pip" install -q -r "$COMFY/requirements.txt" huggingface_hub
"$COMFY/venv/bin/python" -c "import torch;assert torch.cuda.is_available(),'CUDA no disponible';print('  GPU para PyTorch:',torch.cuda.get_device_name(0))"

cat > /etc/systemd/system/shelton-studio-comfy.service <<UNIT
[Unit]
Description=ComfyUI (IA local de Shelton)
After=network-online.target

[Service]
WorkingDirectory=${COMFY}
ExecStart=${COMFY}/venv/bin/python main.py --listen 127.0.0.1 --port ${COMFY_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable -q shelton-studio-comfy && systemctl restart shelton-studio-comfy
ok "Servicio shelton-studio-comfy en 127.0.0.1:${COMFY_PORT}"

say "Voz local (Chatterbox)"
TTS="$SHELTON_DIR/tts_server"
[ -x "$TTS/venv/bin/python" ] || python3 -m venv "$TTS/venv"
"$TTS/venv/bin/pip" install -q --upgrade pip
"$TTS/venv/bin/pip" install -q -r "$TTS/requirements.txt"
cat > /etc/systemd/system/shelton-studio-voz.service <<UNIT
[Unit]
Description=Voz local de Shelton (Chatterbox)
After=network-online.target

[Service]
WorkingDirectory=${TTS}
Environment=TTS_HOST=127.0.0.1
Environment=TTS_PORT=${VOZ_PORT}
ExecStart=${TTS}/venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable -q shelton-studio-voz && systemctl restart shelton-studio-voz
ok "Servicio shelton-studio-voz en 127.0.0.1:${VOZ_PORT} (la primera vez descarga su modelo)"

say "Modelos para la GPU"
# shellcheck disable=SC2086
"$COMFY/venv/bin/python" "$SHELTON_DIR/scripts/download_models.py" --comfy "$COMFY" ${SHELTON_MODELS:-} || \
  echo "  ⚠ Quedaron modelos pendientes: vuelve a correr este script (retoma donde quedó)."

say "Conectando Shelton con la GPU"
ENV="$SHELTON_DIR/.env"
grep -qE '^COMFYUI_URL=' "$ENV" && sed -i "s#^COMFYUI_URL=.*#COMFYUI_URL=http://127.0.0.1:${COMFY_PORT}#" "$ENV" || echo "COMFYUI_URL=http://127.0.0.1:${COMFY_PORT}" >> "$ENV"
grep -qE '^TTS_URL=' "$ENV" && sed -i "s#^TTS_URL=.*#TTS_URL=http://127.0.0.1:${VOZ_PORT}#" "$ENV" || echo "TTS_URL=http://127.0.0.1:${VOZ_PORT}" >> "$ENV"
systemctl restart shelton-studio 2>/dev/null || true
ok "Listo. En Shelton pon el switch en «Local» para usar la 3090 (o «Nube» para OpenRouter)."
echo "  Revisa en Shelton: Ajustes → Estado (te dice si falta algún modelo o nodo de ComfyUI)."
echo "  Logs: journalctl -u shelton-studio-comfy -f   ·   journalctl -u shelton-studio-voz -f"
