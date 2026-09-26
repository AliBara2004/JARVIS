@echo off
title JARVIS
cd /d "%~dp0"

rem Already running? Just open the page instead of starting a second copy.
netstat -ano | findstr "127.0.0.1:7777" | findstr LISTENING >nul
if %errorlevel%==0 (
  start "" http://127.0.0.1:7777
  exit /b
)

echo Starting JARVIS. Keep this window open: closing it stops JARVIS.
echo.
python agent\main.py

rem Only reached if JARVIS stops or fails to start: keep the error on screen.
echo.
echo JARVIS has stopped.
pause
