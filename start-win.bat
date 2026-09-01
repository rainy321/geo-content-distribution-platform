@echo off
setlocal
cd /d "%~dp0"
TITLE GEO MVP Local Starter

if not defined DEMO_MODE set "DEMO_MODE=true"
if not defined SEED_DEMO_DATA set "SEED_DEMO_DATA=true"
if not defined ALLOW_REAL_PUBLISHING set "ALLOW_REAL_PUBLISHING=false"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

ECHO ==================================================
ECHO  Starting GEO MVP local demo...
ECHO ==================================================
ECHO.

ECHO [1/2] Starting Python Backend Server in a new window...
REM The START command launches a new process.
REM The first quoted string is the title of the new window.
REM cmd /k runs the command and keeps the window open to show logs.
START "GEO Backend" cmd /k "%PYTHON_EXE% sau_backend.py"

ECHO [2/2] Starting Vue.js Frontend Server in another new window...
START "GEO Frontend" cmd /k "cd sau_frontend && npm run dev -- --host 127.0.0.1"

ECHO.
ECHO ==================================================
ECHO  Done.
ECHO  Two new windows have been opened for the backend
ECHO  and frontend servers. You can monitor logs there.
ECHO  Open http://127.0.0.1:5173 after both are ready.
ECHO ==================================================
ECHO.

ECHO This window will close in 10 seconds...
timeout /t 10 /nobreak > nul
