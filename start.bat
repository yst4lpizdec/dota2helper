@echo off
setlocal
cd /d "%~dp0backend"

echo Starting Dota2Helper...
start "Dota2Helper" "%~dp0.venv\Scripts\python.exe" app.py

rem ping instead of timeout: timeout fails when stdin is redirected
ping -n 5 127.0.0.1 >nul

echo Starting overlay...
start "Dota2Helper overlay" "%~dp0.venv\Scripts\pythonw.exe" overlay_web.py

echo.
echo Done. Overlay: top-left corner, drag with mouse, Esc to close.
echo To stop everything - close the Dota2Helper console window.
ping -n 4 127.0.0.1 >nul
