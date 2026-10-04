@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pc_assistant %*
) else (
  py -3 -m pc_assistant %*
)
if errorlevel 1 pause
