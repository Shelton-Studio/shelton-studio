#!/usr/bin/env bash
# Instalador de Shelton Studio en un servidor Linux (Ubuntu/Debian). Un solo comando:
#   bash <(curl -fsSL https://raw.githubusercontent.com/juancamp7777/alligator-AI/main/install.sh)
#
# - Instala en su propia carpeta (~/shelton-studio): NO toca otras apps ni otros Shelton.
# - Elige un puerto libre (desde el 8010) y, si hay Tailscale, solo escucha en la red de Tailscale.
# - Pide la clave de OpenRouter o Anthropic sin mostrarla y la guarda solo en tu .env.
# - Lo deja como servicio (arranca solo). Volver a correrlo = actualizar.
set -euo pipefail

REPO="https://github.com/juancamp7777/alligator-AI.git"
DIR="${SHELTON_DIR:-$HOME/shelton-studio}"
SERVICE="shelton-studio"
say() { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }
ok() { printf "  \033[1;32m✔\033[0m %s\n" "$*"; }
die() { printf "\n\033[1;31m✖ %s\033[0m\n" "$*"; exit 1; }
ask() { local v=""; if [ -r /dev/tty ]; then read -r "$@" v < /dev/tty || true; fi; echo "$v"; }

say "Revisando el servidor"
command -v git >/dev/null || die "Falta git: apt install -y git"
command -v python3 >/dev/null || die "Falta python3: apt install -y python3"
FREE_MB=$(df -Pm "$HOME" | awk 'NR==2{print $4}')
[ "${FREE_MB:-0}" -ge 1500 ] || die "Poco espacio en disco (${FREE_MB} MB libres). Libera al menos 1,5 GB y vuelve a correrlo."
if ! python3 -c "import ensurepip" 2>/dev/null; then
  if [ "$(id -u)" = "0" ]; then apt-get install -y python3-venv >/dev/null && ok "python3-venv instalado"
  else die "Falta python3-venv: sudo apt install -y python3-venv"; fi
fi
ok "Python $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:2])))'), ${FREE_MB} MB libres"

say "Descargando Shelton Studio en $DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only origin main && ok "Actualizado"
else git clone -q "$REPO" "$DIR" && ok "Descargado"; fi
cd "$DIR"

say "Instalando dependencias (1-2 minutos la primera vez)"
[ -x venv/bin/python ] || { rm -rf venv; python3 -m venv venv; }
venv/bin/pip install -q --upgrade pip >/dev/null
venv/bin/pip install -q -r requirements.txt
ok "Listo"

if [ ! -f .env ]; then
  say "Configuración inicial"
  TS_IP=$(tailscale ip -4 2>/dev/null | head -n1 || true)
  HOST="${TS_IP:-127.0.0.1}"
  # Primer puerto libre desde el 8010 (prueba real de conexión: no choca con otras apps).
  PORT=$(HOST="$HOST" python3 - <<'PY'
import os, socket
for port in range(8010, 8100):
    busy = False
    for h in {os.environ["HOST"], "127.0.0.1", "0.0.0.0"}:
        try:
            with socket.create_connection(("127.0.0.1" if h == "0.0.0.0" else h, port), timeout=0.3):
                busy = True
        except OSError:
            pass
    if not busy:
        print(port)
        break
PY
)
  KEY_OR="${OPENROUTER_API_KEY:-}"; KEY_AN="${ANTHROPIC_API_KEY:-}"
  if [ -z "$KEY_OR$KEY_AN" ]; then
    echo "  Pega tu clave de OpenRouter (no se verá al escribir). Enter para dejarla para después:"
    KEY_OR=$(ask -s); echo
  fi
  ENGINE="${IMAGE_ENGINE:-mock}"
  cp .env.example .env
  HOST="$HOST" PORT="$PORT" KEY_OR="$KEY_OR" KEY_AN="$KEY_AN" ENGINE="$ENGINE" venv/bin/python - <<'PY'
import os, re
p = ".env"
s = open(p, encoding="utf-8").read()
def put(k, v):
    global s
    s = re.sub(rf"(?m)^#?\s*{k}=.*$", lambda m: f"{k}={v}", s, count=1) if re.search(rf"(?m)^#?\s*{k}=", s) else s + f"\n{k}={v}\n"
put("APP_HOST", os.environ["HOST"]); put("APP_PORT", os.environ["PORT"])
put("IMAGE_ENGINE", os.environ["ENGINE"])
if os.environ["KEY_OR"]: put("OPENROUTER_API_KEY", os.environ["KEY_OR"])
if os.environ["KEY_AN"]: put("ANTHROPIC_API_KEY", os.environ["KEY_AN"])
open(p, "w", encoding="utf-8").write(s)
PY
  chmod 600 .env
  ok "Escucha en ${HOST}:${PORT}$([ -n "$TS_IP" ] && echo ' (solo red Tailscale)')"
  ok "Motor de imagen: ${ENGINE} $([ "$ENGINE" = mock ] && echo '(imágenes simuladas hasta conectar la GPU)')"
  [ -n "$KEY_OR$KEY_AN" ] && ok "Clave guardada solo en $DIR/.env" || echo "  ⚠ Sin clave: agrégala luego en la web (Conexiones → Cerebro) o en $DIR/.env"
else
  ok "Se conserva tu .env actual"
fi
HOST=$(grep -E '^APP_HOST=' .env | cut -d= -f2); PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2)

say "Arrancando como servicio"
if command -v systemctl >/dev/null && [ "$(id -u)" = "0" ] && [ -d /run/systemd/system ]; then
  cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=Shelton Studio
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
WorkingDirectory=${DIR}
ExecStart=${DIR}/venv/bin/python -m app.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable -q "${SERVICE}"
  systemctl restart "${SERVICE}"
  ok "Servicio ${SERVICE} activo (se reinicia solo)"
else
  pkill -f "${DIR}/venv/bin/python -m app.main" 2>/dev/null || true
  nohup "${DIR}/venv/bin/python" -m app.main > "${DIR}/shelton.log" 2>&1 &
  ok "Corriendo en segundo plano (log: ${DIR}/shelton.log)"
fi

for _ in $(seq 1 40); do
  if curl -fsS "http://${HOST}:${PORT}/api/health" >/dev/null 2>&1; then
    BRAIN=$(curl -fsS "http://${HOST}:${PORT}/api/health" | venv/bin/python -c "import json,sys;h=json.load(sys.stdin);print(('OpenRouter' if h.get('provider')=='openrouter' else 'Anthropic')+' · '+h.get('model','')+('' if h.get('claude') else ' (SIN CLAVE)'))")
    say "¡Listo!"
    echo "  Abre:    http://${HOST}:${PORT}"
    echo "  Cerebro: ${BRAIN}"
    echo "  Logs:    journalctl -u ${SERVICE} -f   (o ${DIR}/shelton.log)"
    echo "  Actualizar más adelante: vuelve a correr este mismo comando."
    exit 0
  fi
  sleep 1
done
die "No arrancó. Mira el error con: journalctl -u ${SERVICE} -n 50 --no-pager"
