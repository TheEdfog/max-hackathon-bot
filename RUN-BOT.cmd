@echo off
cd /d "%~dp0"
if not exist ".env.hiring" (
  echo Create .env.hiring from .env.hiring.example and configure your token and employer code.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Install Python 3.13, create .venv and install requirements-hiring.lock. See docs/MAX-BOT.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -m hiring.polling
pause
