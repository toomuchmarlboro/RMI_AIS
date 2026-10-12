@echo off
set VENV_PY=.\.venv\Scripts\python.exe

if not exist %VENV_PY% (
    echo [!] Virtual environment not found. Setting up...
    python -m venv .venv
    .\.venv\Scripts\pip.exe install -r requirements.txt
)

echo [+] Launching RMI AIS Tracker on http://127.0.0.1:8000 ...
%VENV_PY% -m src.main
pause
