#!/usr/bin/env bash
# Prepara una tarjeta alquilada (RunPod, Vast…) u otra PC con GPU como «nodo» de Shelton.
# Correr como root dentro de la máquina (en RunPod: la terminal web o SSH del pod):
#
#   export WORKER_TOKEN="una-clave-larga-que-inventes"      # la misma que pondrás en Shelton
#   export TS_AUTHKEY="tskey-auth-..."                        # clave de Tailscale (Settings → Keys → Generate auth key)
#   bash <(curl -fsSL https://raw.githubusercontent.com/juancamp7777/alligator-AI/main/scripts/node_setup.sh)
#
# Opcionales:
#   WORKER_GPUS="0"            lugares de entrenamiento (una RTX PRO 6000 en 3: activa MIG y pon los 3 UUID MIG-...)
#   NODE_COMFY=1               también instala ComfyUI + modelos para generar fotos en esta máquina
#   NODE_NAME="pro6000-1"      nombre del nodo en Tailscale
#   BASE=/workspace            carpeta persistente (en RunPod, el Network Volume)
#
# Instala: Tailscale (conexión privada con la placa), ai-toolkit (entrenador), el trabajador de Shelton y,
# si NODE_COMFY=1, ComfyUI con los modelos. Todo queda en $BASE: si usas disco persistente, la próxima vez es inmediato.
set -euo pipefail
say() { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }
ok() { printf "  \033[1;32m✔\033[0m %s\n" "$*"; }
die() { printf "\n\033[1;31m✖ %s\033[0m\n" "$*"; exit 1; }

BASE="${BASE:-/workspace}"
NODE_NAME="${NODE_NAME:-shelton-gpu-$(hostname | cut -c1-8)}"
REPO="${REPO:-https://github.com/juancamp7777/alligator-AI}"
[ -n "${WORKER_TOKEN:-}" ] || die "Falta WORKER_TOKEN (inventa una clave larga; la misma va en Shelton)"
mkdir -p "$BASE"
command -v nvidia-smi >/dev/null || die "Esta máquina no tiene GPU NVIDIA visible"

say "GPU"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/  /'
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq git curl python3-venv libgl1 libglib2.0-0 >/dev/null 2>&1 || true

say "Conexión privada (Tailscale)"
if ! command -v tailscale >/dev/null; then curl -fsSL https://tailscale.com/install.sh | sh >/dev/null; fi
if ! tailscale status >/dev/null 2>&1; then
  # En contenedores (RunPod/Vast) no hay systemd: modo «userspace», igual de privado
  mkdir -p "$BASE/tailscale"
  nohup tailscaled --tun=userspace-networking --state="$BASE/tailscale/tailscaled.state" \
    --socket=/var/run/tailscale/tailscaled.sock >"$BASE/tailscale/tailscaled.log" 2>&1 &
  sleep 3
fi
if [ -n "${TS_AUTHKEY:-}" ]; then
  tailscale up --authkey="$TS_AUTHKEY" --hostname="$NODE_NAME" --accept-routes >/dev/null && ok "Conectado como $NODE_NAME"
else
  tailscale status >/dev/null 2>&1 && ok "Tailscale ya conectado" || die "Falta TS_AUTHKEY para entrar a la red privada"
fi
TSIP=$(tailscale ip -4 2>/dev/null | head -n1)

say "Entrenador (ai-toolkit) en $BASE/ai-toolkit"
AITK="$BASE/ai-toolkit"
if [ -d "$AITK/.git" ]; then git -C "$AITK" pull -q --ff-only || true
else git clone -q https://github.com/ostris/ai-toolkit "$AITK"; fi
git -C "$AITK" submodule update --init --recursive -q || true
[ -x "$AITK/venv/bin/python" ] || python3 -m venv "$AITK/venv"
"$AITK/venv/bin/pip" install -q --upgrade pip
"$AITK/venv/bin/pip" install -q torch torchvision torchaudio --extra-index-url "https://download.pytorch.org/whl/${CUDA_WHEEL:-cu128}"
"$AITK/venv/bin/pip" install -q -r "$AITK/requirements.txt"
"$AITK/venv/bin/python" -c "import torch;assert torch.cuda.is_available();print('  PyTorch ve:',torch.cuda.get_device_name(0))"
ok "ai-toolkit listo (la primera vez que entrene baja Z-Image, ~20 GB, y queda guardado en $BASE)"
export HF_HOME="$BASE/hf_cache"

say "Trabajador de Shelton"
SRC="$BASE/shelton-src"
if [ -d "$SRC/.git" ]; then git -C "$SRC" pull -q --ff-only || true; else git clone -q --depth 1 "$REPO" "$SRC"; fi
LORA_DIR=""
if [ "${NODE_COMFY:-0}" = "1" ]; then
  say "ComfyUI para generar fotos en esta máquina"
  COMFY="$BASE/ComfyUI"
  if [ -d "$COMFY/.git" ]; then git -C "$COMFY" pull -q --ff-only || true; else git clone -q https://github.com/comfyanonymous/ComfyUI "$COMFY"; fi
  [ -x "$COMFY/venv/bin/python" ] || python3 -m venv "$COMFY/venv"
  "$COMFY/venv/bin/pip" install -q torch torchvision torchaudio --extra-index-url "https://download.pytorch.org/whl/${CUDA_WHEEL:-cu128}"
  "$COMFY/venv/bin/pip" install -q -r "$COMFY/requirements.txt" huggingface_hub
  bash "$SRC/scripts/quality_nodes.sh" "$COMFY" || echo "  ⚠ No pude instalar la mejora de rostro (se usa sin ella)"
  "$COMFY/venv/bin/python" "$SRC/scripts/download_models.py" --comfy "$COMFY" imagen calidad || echo "  ⚠ Faltan modelos: vuelve a correr"
  pkill -f "ComfyUI/main.py" 2>/dev/null || true
  nohup "$COMFY/venv/bin/python" "$COMFY/main.py" --listen 0.0.0.0 --port 8288 >"$BASE/comfy.log" 2>&1 &
  LORA_DIR="$COMFY/models/loras"
  ok "ComfyUI en http://$TSIP:8288"
fi
pkill -f shelton_worker.py 2>/dev/null || true
cat > "$BASE/start_worker.sh" <<RUN
#!/usr/bin/env bash
export WORKER_TOKEN="$WORKER_TOKEN" AITK_DIR="$AITK" WORKER_DIR="$BASE/shelton_jobs" HF_HOME="$BASE/hf_cache"
export COMFY_LORA_DIR="$LORA_DIR" WORKER_GPUS="${WORKER_GPUS:-}" WORKER_SHARE="${WORKER_SHARE:-1}"
exec python3 "$SRC/scripts/shelton_worker.py"
RUN
chmod 700 "$BASE/start_worker.sh"
nohup "$BASE/start_worker.sh" >"$BASE/worker.log" 2>&1 &
sleep 2
grep -q "Trabajador de Shelton" "$BASE/worker.log" && ok "Trabajador escuchando" || { tail -5 "$BASE/worker.log"; die "El trabajador no arrancó"; }

say "¡Listo! En Shelton → Conexiones → Agregar → «Nodo de GPU (trabajador)»:"
echo "  URL del trabajador:  http://$TSIP:8299"
echo "  Token:               (el WORKER_TOKEN que inventaste)"
[ -n "$LORA_DIR" ] && echo "  ComfyUI del nodo:    http://$TSIP:8288   (agrégalo también como conexión ComfyUI para generar)"
echo "  Si la máquina se reinicia: bash $BASE/start_worker.sh &"
