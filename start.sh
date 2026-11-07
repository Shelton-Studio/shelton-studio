#!/usr/bin/env bash
# Arranque de Shelton en Linux/Mac
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "Creando entorno virtual..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Se creó .env: pon tu ANTHROPIC_API_KEY y vuelve a ejecutar."
  exit 1
fi

python -m app.main
