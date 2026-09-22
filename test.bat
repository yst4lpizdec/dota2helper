@echo off
setlocal
cd /d "%~dp0backend"

rem Показ на сохранённой катке: приложение думает, что идёт матч.
rem   test.bat            - старт матча с полным пиком
rem   test.bat in_progress - середина катки
rem   test.bat --list      - какие снимки есть
start "" "%~dp0.venv\Scripts\pythonw.exe" overlay_web.py

rem ping вместо timeout: timeout падает, когда ввод перенаправлен
ping -n 6 127.0.0.1 >nul

start "Dota2Helper replay" "%~dp0.venv\Scripts\python.exe" replay.py %1

echo.
echo Закрой окно replay - и панель снова будет ждать настоящую игру.
ping -n 4 127.0.0.1 >nul
