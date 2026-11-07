@echo off
REM Arranque de Shelton en Windows (doble clic)
cd /d "%~dp0"
if not exist venv (
  echo Creando entorno virtual...
  python -m venv venv
)
call venv\Scripts\activate
pip install -q -r requirements.txt
if not exist .env (
  copy .env.example .env
  echo Se creo .env: pon tu ANTHROPIC_API_KEY y vuelve a ejecutar.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8000
python -m app.main
pause
