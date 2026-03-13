@echo off
title Gilead Chatbot - Backend Server
cd /d "%~dp0backend"

echo ============================================
echo   Gilead Field Rep Chatbot - Backend
echo ============================================

REM Create venv if it doesn't exist
if not exist "venv\Scripts\activate.bat" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Make sure Python is installed.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Virtual environment already exists - skipping creation
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install dependencies only if marker file doesn't exist
if not exist "venv\.deps_installed" (
    echo [2/3] Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    copy /y NUL "venv\.deps_installed" >NUL 2>&1
    echo     Done.
) else (
    echo [2/3] Dependencies already installed - skipping
)

echo [3/3] Starting backend server on http://localhost:8000 ...
echo.
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
pause
