@echo off
rem Feedly MCP setup wizard (GUI)
rem Thin launcher: all logic lives in scripts\setup_wizard.py
setlocal
set "PY="
rem 1) local dev venv (source checkout), 2) python on PATH, 3) the py launcher
if exist "%~dp0.venv\Scripts\activate.bat" set PY="%~dp0.venv\Scripts\python"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 9009
)
%PY% "%~dp0scripts\setup_wizard.py" %*
set "RC=%errorlevel%"
rem (GUI: no console pause needed)
endlocal & exit /b %RC%
