@echo off
rem ---------------------------------------------------------------
rem  Yufan AI Studio - workbench launcher
rem  Starts the API (8080) and the LibTV debug Chrome (CDP 9226).
rem  Keep this window open: closing it stops the backend.
rem  This file is ASCII-only on purpose. Chinese text in a .cmd
rem  gets misread under the GBK code page and breaks parsing.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%api\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERROR] Python venv not found: %PY%
  echo         Run: python -m venv api\.venv
  pause
  exit /b 1
)

echo Starting backend + LibTV browser ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start-services.ps1" -WithBrowser

echo.
echo Backend:  http://127.0.0.1:8080
echo Frontend: http://127.0.0.1:5173
echo Keep this window open. Closing it stops the backend.
echo.
pause
