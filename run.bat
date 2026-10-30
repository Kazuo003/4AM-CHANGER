@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\4AM-Changer\4AM-Changer.exe" (
  start "" "%~dp0dist\4AM-Changer\4AM-Changer.exe"
  exit /b 0
)
python app.py
if errorlevel 1 pause
