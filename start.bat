@echo off
chcp 65001 >nul
title knowledge_graph Launcher
cd /d "%~dp0"

set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

"%PY%" "%~dp0start.py" %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo Launcher exited with code %CODE%
    pause
)
