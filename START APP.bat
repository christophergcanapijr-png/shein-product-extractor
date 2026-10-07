@echo off
title Product Extractor
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo The virtual environment is missing.
  echo Run the project setup first, then try again.
  pause
  exit /b 1
)

powershell.exe -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  start "" "http://127.0.0.1:8000"
  exit /b 0
)

start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
"venv\Scripts\python.exe" run.py

if errorlevel 1 (
  echo.
  echo The app stopped because of an error.
  pause
)
