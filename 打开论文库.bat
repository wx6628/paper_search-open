@echo off
cd /d "%~dp0"
start "PaperLibrary server" python -I ps\server.py --port 8765
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765
