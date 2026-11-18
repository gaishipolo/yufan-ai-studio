@echo off
rem ASCII-only launcher used by the "YufanWorkbench" scheduled task.
rem Starts the API server detached from any agent session.
cd /d "%~dp0"
rem Drop shim vars that would change unlink semantics in child processes.
set "PYTHONPATH="
set "NODE_OPTIONS="
".venv\Scripts\python.exe" -m cli serve --host 127.0.0.1 --port 8080 >> "var\serve.log" 2>&1
