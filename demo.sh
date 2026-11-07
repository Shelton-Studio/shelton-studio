#!/usr/bin/env bash
# Modo demo: interfaz completa sin GPU ni API key (imágenes y respuestas simuladas)
set -e
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
echo "Abre http://127.0.0.1:8011"
python tests/fake_server.py 8011
