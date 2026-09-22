@echo off
setlocal
cd /d "%~dp0backend"

echo Starting Dota2Helper...
start "Dota2Helper" "%~dp0.venv\Scripts\python.exe" app.py

rem ping instead of timeout: timeout fails when stdin is redirected
ping -n 8 127.0.0.1 >nul

echo Feeding a saved match (%1)...
start "Dota2Helper replay" "%~dp0.venv\Scripts\python.exe" replay.py %1

ping -n 3 127.0.0.1 >nul

echo Starting overlay...
start "Dota2Helper overlay" "%~dp0.venv\Scripts\pythonw.exe" overlay_web.py

echo.
echo Test mode. Close the "Dota2Helper replay" window - and the panel goes
echo back to waiting for a real game.
ping -n 4 127.0.0.1 >nul
