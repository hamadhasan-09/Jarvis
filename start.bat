@echo off
:: =====================================================================
:: JARVIS Nexus Live HUD
:: NOTE: ui\server.py is the ONLY active entry point for this project.
:: Legacy entry points (app.py, app_gui.py, etc.) are in the archive/ folder.
:: =====================================================================
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
chcp 65001 > nul
title JARVIS Nexus Live HUD
cls
echo =======================================================
echo   Starting JARVIS Nexus Live HUD...
echo =======================================================
echo.
call venv\Scripts\activate
python ui\server.py
pause
