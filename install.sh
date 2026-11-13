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
if [ -d "$DIR" ] && ! git -C "$DIR" remote get-url origin 2>/dev/null | grep -qi "alligator-ai"; then
  # Otra app con la misma carpeta: no se mezcla ni se actualiza con nuestro código.
  die "$DIR es de otro proyecto. Muévelo aparte (mv $DIR ${DIR}-viejo) o instala en otra carpeta con SHELTON_DIR=/ruta y vuelve a correrlo."
fi
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q --ff-only origin main && ok "Actualizado"
else git clone -q "$REPO" "$DIR" && ok "Descargado"; fi
cd "$DIR"

say "Instalando dependencias (1-2 minutos la primera vez)"
[ -x venv/bin/python ] || { rm -rf venv; python3 -m venv venv; }
venv/bin/pip install -q --upgrade pip >/dev/null
venv/bin/pip install -q -r requirements.txt
# Las redes cambian seguido: yt-dlp (el que baja videos por link) siempre a su última versión
venv/bin/pip install -q -U yt-dlp >/dev/null 2>&1 || true
# Traductor español → inglés en la placa (para SDXL/Flux en la Adult Zone). Opcional: si no se puede, no frena nada.
venv/bin/pip install -q ctranslate2 sentencepiece >/dev/null 2>&1 || \
  { [ -x "$HOME/ComfyUI/venv/bin/pip" ] && "$HOME/ComfyUI/venv/bin/pip" install -q ctranslate2 sentencepiece >/dev/null 2>&1; } || true
ok "Listo"

if [ ! -f .env ]; then
  say "Configuración inicial"
  TS_IP=$(tailscale ip -4 2>/dev/null | head -n1 || true)
  HOST="${TS_IP:-127.0.0.1}"
  # Link https privado de Tailscale (https://<máquina>.<tu-red>.ts.net): Shelton escucha solo dentro del equipo
  # y Tailscale lo publica con https a tu red (el micrófono del navegador también funciona así).
  if [ -n "$TS_IP" ] && [ "${SHELTON_HTTPS:-}" != "0" ]; then
    WANT="${SHELTON_HTTPS:-}"
    if [ -z "$WANT" ]; then
      echo -n "  ¿Abrir Shelton con un link https privado de Tailscale (tipo https://shelton.tu-red.ts.net)? [S/n]: "
      A=$(ask); case "$A" in n|N|no|NO) WANT=0 ;; *) WANT=1 ;; esac
    fi
    if [ "$WANT" = "1" ]; then HOST="127.0.0.1"; SHELTON_HTTPS=1; fi
  fi
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
  # Con clave de OpenRouter: imágenes reales por OpenRouter. Sin clave: simuladas hasta conectar la GPU.
  if [ -n "${IMAGE_ENGINE:-}" ]; then ENGINE="$IMAGE_ENGINE"; elif [ -n "$KEY_OR" ]; then ENGINE=openrouter; else ENGINE=mock; fi
  cp .env.example .env
  HOST="$HOST" PORT="$PORT" KEY_OR="$KEY_OR" KEY_AN="$KEY_AN" ENGINE="$ENGINE" HTTPS_TS="${SHELTON_HTTPS:-0}" venv/bin/python - <<'PY'
import os, re
p = ".env"
s = open(p, encoding="utf-8").read()
def put(k, v):
    global s
    s = re.sub(rf"(?m)^#?\s*{k}=.*$", lambda m: f"{k}={v}", s, count=1) if re.search(rf"(?m)^#?\s*{k}=", s) else s + f"\n{k}={v}\n"
put("APP_HOST", os.environ["HOST"]); put("APP_PORT", os.environ["PORT"])
put("IMAGE_ENGINE", os.environ["ENGINE"])
put("SHELTON_HTTPS", os.environ["HTTPS_TS"])
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
  # Si hay clave de OpenRouter y el motor estaba en "simulado", pasa a imágenes reales con OpenRouter.
  if grep -qE '^IMAGE_ENGINE=mock' .env && grep -qE '^OPENROUTER_API_KEY=.+' .env && [ "${KEEP_MOCK:-}" != "1" ]; then
    sed -i 's/^IMAGE_ENGINE=mock/IMAGE_ENGINE=openrouter/' .env
    ok "Motor de imagen: OpenRouter (imágenes reales; el video sigue esperando la GPU)"
  fi
fi
if [ "${SHELTON_HTTPS:-}" = "1" ] && ! grep -qE '^SHELTON_HTTPS=1' .env; then
  # Activar el link https en una instalación que ya existía: Shelton pasa a escuchar solo dentro del equipo
  sed -i 's/^APP_HOST=.*/APP_HOST=127.0.0.1/' .env
  if grep -qE '^SHELTON_HTTPS=' .env; then sed -i 's/^SHELTON_HTTPS=.*/SHELTON_HTTPS=1/' .env; else echo "SHELTON_HTTPS=1" >> .env; fi
  ok "Link https de Tailscale activado"
fi
HOST=$(grep -E '^APP_HOST=' .env | cut -d= -f2); PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2)

say "Acceso a la web (usuario y contraseña)"
ADMIN_MSG=""
if venv/bin/python -m app.auth list 2>/dev/null | grep -q .; then
  ok "Ya hay usuarios creados (se conservan). Olvidaste la clave: venv/bin/python -m app.auth reset-password"
else
  echo "  Crea tu usuario admin para entrar a Shelton."
  echo -n "  Usuario [admin]: "; ADMIN_USER=$(ask); ADMIN_USER="${ADMIN_USER:-admin}"
  echo "  Contraseña (mín. 5 caracteres, no se verá al escribir). Enter = generar una segura:"
  ADMIN_PW=$(ask -s); echo
  if [ -n "$ADMIN_PW" ]; then
    echo "  Repítela:"; ADMIN_PW2=$(ask -s); echo
    [ "$ADMIN_PW" = "$ADMIN_PW2" ] || die "Las contraseñas no coinciden. Vuelve a correr el instalador."
  fi
  OUT=$(SHELTON_ADMIN_USER="$ADMIN_USER" SHELTON_ADMIN_PASSWORD="$ADMIN_PW" venv/bin/python -m app.auth ensure-admin) \
    || die "No se pudo crear el usuario: $OUT"
  GEN=$(echo "$OUT" | awk '/^CREATED/{print $3}'); ADMIN_USER=$(echo "$OUT" | awk '/^CREATED/{print $2}')
  ok "Usuario admin creado: ${ADMIN_USER}"
  if [ -n "$GEN" ]; then
    ADMIN_MSG="  Usuario: ${ADMIN_USER}   Contraseña: ${GEN}   ← guárdala (se muestra solo esta vez)"
  else
    ADMIN_MSG="  Usuario: ${ADMIN_USER}   (con la contraseña que escribiste)"
  fi
  unset ADMIN_PW ADMIN_PW2
fi

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
TimeoutStopSec=15
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable -q "${SERVICE}"
  echo "  (reiniciando: puede tardar hasta ~20 s si hay navegadores abiertos)"
  timeout 40 systemctl restart "${SERVICE}" || { systemctl kill -s KILL "${SERVICE}" 2>/dev/null; sleep 2; systemctl restart "${SERVICE}"; }
  ok "Servicio ${SERVICE} activo (se reinicia solo)"
else
  pkill -f "${DIR}/venv/bin/python -m app.main" 2>/dev/null || true
  nohup "${DIR}/venv/bin/python" -m app.main > "${DIR}/shelton.log" 2>&1 &
  ok "Corriendo en segundo plano (log: ${DIR}/shelton.log)"
fi

for _ in $(seq 1 90); do
  if curl -fsS "http://${HOST}:${PORT}/api/health" >/dev/null 2>&1; then
    BRAIN=$(curl -fsS "http://${HOST}:${PORT}/api/health" | venv/bin/python -c "import json,sys;h=json.load(sys.stdin);print(('OpenRouter' if h.get('provider')=='openrouter' else 'Anthropic')+' · '+h.get('model','')+('' if h.get('claude') else ' (SIN CLAVE)'))")
    say "¡Listo!"
    LINK="http://${HOST}:${PORT}"
    if grep -qE '^SHELTON_HTTPS=1' .env && command -v tailscale >/dev/null; then
      # Puerto https: nunca pisa lo que otras apps ya publican en esta máquina. Usa 443 (link sin número) solo si
      # está libre o ya es de Shelton; si no, el primero libre de 8443, 8444, 9443, 10443 (o SHELTON_HTTPS_PORT).
      HTTPS_PORT=$(tailscale serve status --json 2>/dev/null | TARGET="http://127.0.0.1:${PORT}" WANT="${SHELTON_HTTPS_PORT:-}" \
        venv/bin/python -c "
import json, os, sys
target, want = os.environ['TARGET'], os.environ['WANT']
try:
    web = (json.load(sys.stdin) or {}).get('Web') or {}
    ok = True
except Exception:
    web, ok = {}, False
used = {}
for hostport, cfg in web.items():
    port = hostport.rsplit(':', 1)[-1]
    proxies = [h.get('Proxy', '') for h in ((cfg or {}).get('Handlers') or {}).values()]
    used[port] = proxies
mine = [p for p, prox in used.items() if any(x.rstrip('/') == target for x in prox)]
if want:
    print(want)
elif mine:
    print(mine[0])
elif ok and '443' not in used:
    print('443')
else:
    print(next(p for p in ('8443', '8444', '9443', '10443') if p not in used))
" 2>/dev/null || echo 8443)
      if tailscale serve --bg --https="${HTTPS_PORT}" "http://127.0.0.1:${PORT}" >/dev/null 2>&1; then
        TS_NAME=$(tailscale status --json 2>/dev/null | venv/bin/python -c "import json,sys;print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
        [ -n "$TS_NAME" ] && LINK="https://${TS_NAME}$([ "$HTTPS_PORT" = "443" ] || echo ":${HTTPS_PORT}")"
      else
        echo "  ⚠ No se pudo publicar el link https. En login.tailscale.com → DNS activa «MagicDNS» y «HTTPS Certificates», y vuelve a correr el instalador."
      fi
    fi
    echo "  Abre:    ${LINK}"
    echo "  Cerebro: ${BRAIN}"
    [ -n "$ADMIN_MSG" ] && echo "$ADMIN_MSG"
    echo "  Logs:    journalctl -u ${SERVICE} -f   (o ${DIR}/shelton.log)"
    echo "  Actualizar más adelante: vuelve a correr este mismo comando."
    exit 0
  fi
  sleep 1
done
die "No arrancó. Mira el error con: journalctl -u ${SERVICE} -n 50 --no-pager"
