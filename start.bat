@echo off
setlocal
cd /d "%~dp0backend"

rem Одна программа: приёмник данных живёт внутри неё, отдельного окна
rem консоли больше нет. Журнал — backend\data\helper.log
start "" "%~dp0.venv\Scripts\pythonw.exe" overlay_web.py
