@echo off
title JARVIS
cd /d "%~dp0"

rem launch.py opens the page if JARVIS is already running the latest code,
rem restarts it if it is running an older version, and starts it otherwise.
echo Starting JARVIS. Keep this window open: closing it stops JARVIS.
echo.
python agent\launch.py

rem A clean exit (replaced by a newer JARVIS, or already running) closes this window.
rem Anything else keeps the error on screen.
if errorlevel 1 (
  echo.
  echo JARVIS stopped with an error. Send the message above to Claude.
  pause
)
