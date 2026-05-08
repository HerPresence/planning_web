@echo off
title Planning System
setlocal enabledelayedexpansion

echo ===============================
echo STARTING PLANNING SYSTEM
echo ===============================

echo Script directory: %~dp0
set "SCRIPT_DIR=%~dp0"
set "WEB_DIR=%SCRIPT_DIR%"
set "VENV_DIR=%WEB_DIR%venv"
set "BACKEND_PORT=8002"
set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "PG_CTL=C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
set "PG_DATA=C:\Program Files\PostgreSQL\18\data"

echo Checking PostgreSQL state...
netstat -ano | findstr /C:":5432" | findstr /C:"LISTENING" >nul 2>&1
if errorlevel 1 (
    tasklist /FI "IMAGENAME eq postgres.exe" | findstr /I /C:"postgres.exe" >nul 2>&1
    if not errorlevel 1 (
        echo Found stale postgres.exe process without port 5432 listening.
        echo Stopping stale PostgreSQL process...
        taskkill /F /IM postgres.exe >nul 2>&1
        timeout /t 2 >nul
        if exist "%PG_DATA%\postmaster.pid" del /f /q "%PG_DATA%\postmaster.pid" >nul 2>&1
    )
)

echo Starting PostgreSQL...
"%PG_CTL%" start -D "%PG_DATA%" >nul 2>&1
if errorlevel 1 (
    echo PostgreSQL may already be running. Continue...
)
timeout /t 3 >nul

echo Starting Backend FastAPI...
start "Planning Backend" cmd /k "cd /d %WEB_DIR% && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"

echo Opening browser at %BACKEND_URL%
start "" "%BACKEND_URL%"

echo ===============================
echo SYSTEM STARTED
echo ===============================
pause
goto :eof