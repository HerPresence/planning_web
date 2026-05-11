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

echo Waiting for PostgreSQL on port 5432...
set "PG_WAIT=0"
:wait_pg
netstat -ano | findstr /C:":5432" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 goto pg_ready
set /a PG_WAIT+=1
if %PG_WAIT% geq 30 (
    echo ERROR: PostgreSQL did not start within 30 seconds. Aborting.
    pause
    exit /b 1
)
timeout /t 1 >nul
goto wait_pg
:pg_ready
echo PostgreSQL is ready.

echo Starting Backend FastAPI...
start "Planning Backend" cmd /k "cd /d %WEB_DIR% && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"

echo Opening browser at %BACKEND_URL%
start "" "%BACKEND_URL%"

echo ===============================
echo SYSTEM STARTED
echo ===============================
pause
goto :eof