@echo off
title Gilead Chatbot - Full Stack
echo ============================================
echo   Gilead Field Rep Chatbot - Full Stack
echo ============================================
echo.
echo Starting Backend and Frontend in separate windows...
echo.

REM Launch backend in a new window
start "Backend Server" cmd /k "cd /d "%~dp0" && call run_backend.bat"

REM Wait a few seconds for backend to start before frontend
timeout /t 5 /nobreak >nul

REM Launch frontend in a new window
start "Frontend Server" cmd /k "cd /d "%~dp0" && call run_frontend.bat"

echo.
echo Both servers are starting in separate windows.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000
echo.
echo Close this window or press any key to exit.
pause >nul
