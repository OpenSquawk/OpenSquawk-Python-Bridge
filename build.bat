@echo off
REM Build the clickable .exe for Windows. Just double-click this file.
cd /d "%~dp0"
python build.py
pause
