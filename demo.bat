@echo off
REM Modo demo: interfaz completa sin GPU ni API key (imagenes y respuestas simuladas)
cd /d "%~dp0"
if not exist venv (
  python -m venv venv
)
call venv\Scripts\activate
pip install -q -r requirements.txt
start "" http://127.0.0.1:8011
python tests\fake_server.py 8011
pause
