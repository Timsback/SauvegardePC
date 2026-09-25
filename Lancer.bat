@echo off
cd /d "%~dp0app"
python web_main.py
if errorlevel 1 pause
