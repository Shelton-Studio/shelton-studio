@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

if "%1"=="" (
    echo Usage: shelton.bat [command] [arguments]
    echo.
    echo Commands:
    echo   shelton.bat generate "Your prompt here"
    echo   shelton.bat status [job-id]
    echo   shelton.bat history
    echo   shelton.bat approve [content-id]
    echo   shelton.bat health
    echo.
    exit /b 1
)

python cli/shelton_cli.py %*
