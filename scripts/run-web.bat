@echo off
REM Goal xG — Windows launcher for FastAPI web UI.
REM Double-click or run from cmd/PowerShell: scripts\run-web.bat
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-web.ps1"
exit /b %ERRORLEVEL%
