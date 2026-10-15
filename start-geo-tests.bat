@echo off
setlocal
cd /d "%~dp0"
set "GEO_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "GEO_PYTHON=.venv\Scripts\python.exe"
"%GEO_PYTHON%" -X utf8 scripts\start_geo_tests.py
if errorlevel 1 pause
