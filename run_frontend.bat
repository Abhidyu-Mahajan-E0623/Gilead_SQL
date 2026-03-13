@echo off
title Gilead Chatbot - Frontend Server
cd /d "%~dp0Gilead-POC-FE-main\Gilead-POC-FE-main"

echo ============================================
echo   Gilead Field Rep Chatbot - Frontend
echo ============================================

REM Add Node.js to PATH for this session
set "PATH=C:\Program Files\nodejs;%CD%\node_modules\.bin;%PATH%"

REM Fix corporate SSL cert issues
set NODE_TLS_REJECT_UNAUTHORIZED=0

REM Check if node_modules exists, skip npm install if so
if not exist "node_modules\.package-lock.json" (
    echo [1/2] Installing npm dependencies...
    call npm.cmd config set strict-ssl false
    call npm.cmd install
    if errorlevel 1 (
        echo ERROR: npm install failed. Make sure Node.js is installed.
        pause
        exit /b 1
    )
) else (
    echo [1/2] node_modules already exists - skipping install
)

echo [2/2] Starting frontend on http://localhost:3000 ...
echo.
call node_modules\.bin\next.cmd dev
pause
