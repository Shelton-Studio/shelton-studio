@echo off
echo.
echo ========================================
echo   🤖 SHELTON - Content Generation
echo ========================================
echo.
echo Starting Shelton server...
echo.
echo Dashboard will be available at:
echo   http://localhost:8000
echo.
echo Press Ctrl+C to stop
echo.
echo ========================================
echo.

cd /d "%~dp0"
python app/main.py

pause
