@echo off
title Planning System
setlocal enabledelayedexpansion

echo ===============================
echo   PLANNING SYSTEM STARTUP
echo ===============================
echo.

set "SCRIPT_DIR=%~dp0"
set "WEB_DIR=%SCRIPT_DIR%"
set "FRONTEND_DIR=%SCRIPT_DIR%..\planning_front"
set "BACKEND_PORT=8002"
set "FRONTEND_PORT=3000"
set "PG_BIN=C:\Program Files\PostgreSQL\18\bin"
set "PG_CTL=%PG_BIN%\pg_ctl.exe"
set "PG_DATA=C:\Program Files\PostgreSQL\18\data"
set "PG_ISREADY=%PG_BIN%\pg_isready.exe"


REM ─── 1. PostgreSQL ────────────────────────────────────────────────────────────
echo [1/3] PostgreSQL...

REM Step 1: already ready — nothing to do
"%PG_ISREADY%" -h 127.0.0.1 -p 5432 -q >nul 2>&1
if not errorlevel 1 (
    echo   Already running.
    goto pg_ready
)

REM Step 2: check service state (1=STOPPED 2=START_PENDING 3=STOP_PENDING 4=RUNNING)
for /f "tokens=3" %%s in ('sc query PlanningPostgreSQL ^| findstr "STATE"') do set "PG_STATE_NUM=%%s"
echo   Service state code: %PG_STATE_NUM%

REM Step 3: only start if explicitly STOPPED
if "%PG_STATE_NUM%"=="1" (
    echo   Service is STOPPED — starting...
    net start PlanningPostgreSQL >nul 2>&1
) else (
    echo   Service is already starting or running — waiting for pg_isready...
)

REM Step 4: wait up to 60 seconds, polling every 2 seconds
set "PG_WAIT=0"
:wait_pg
"%PG_ISREADY%" -h 127.0.0.1 -p 5432 -q >nul 2>&1
if not errorlevel 1 goto pg_ready
set /a PG_WAIT+=2
if %PG_WAIT% geq 60 goto pg_timeout
if %PG_WAIT%==10 echo   Waiting for PostgreSQL... (%PG_WAIT%s)
if %PG_WAIT%==30 echo   Waiting for PostgreSQL... (%PG_WAIT%s)
if %PG_WAIT%==50 echo   Waiting for PostgreSQL... (%PG_WAIT%s)
timeout /t 2 /nobreak >nul
goto wait_pg

:pg_timeout
echo.
echo   ERROR: PostgreSQL did not become ready within 60 seconds.
echo   --- sc query PlanningPostgreSQL ---
sc query PlanningPostgreSQL
echo   --- netstat :5432 ---
netstat -ano | findstr :5432
echo.
echo   Manual check:
echo     "%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432
echo.
echo   Start aborted. Fix PostgreSQL manually and re-run.
pause
exit /b 1

:pg_ready
echo   [OK] PostgreSQL is ready.
echo.


REM ─── 2. Backend ───────────────────────────────────────────────────────────────
echo [2/3] Backend FastAPI (port %BACKEND_PORT%)...
start "Planning Backend" cmd /k "cd /d %WEB_DIR% && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"

set "BE_WAIT=0"
:wait_backend
netstat -ano | findstr /C:":%BACKEND_PORT%" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    timeout /t 5 >nul
    goto backend_ready
)
set /a BE_WAIT+=1
if %BE_WAIT% geq 60 (
    echo.
    echo   ERROR: Backend did not start in 60 seconds.
    echo   Check the "Planning Backend" window for errors.
    pause
    exit /b 1
)
if %BE_WAIT%==10 echo   Waiting for backend... (%BE_WAIT%s)
if %BE_WAIT%==30 echo   Waiting for backend... (%BE_WAIT%s)
if %BE_WAIT%==50 echo   Waiting for backend... (%BE_WAIT%s)
timeout /t 1 >nul
goto wait_backend

:backend_ready
echo   [OK] Backend is ready.
echo.


REM ─── 3. Frontend ──────────────────────────────────────────────────────────────
echo [3/3] Frontend React (port %FRONTEND_PORT%)...
start "Planning Frontend" cmd /k "cd /d %FRONTEND_DIR% && npm start"

set "FE_WAIT=0"
:wait_frontend
netstat -ano | findstr /C:":%FRONTEND_PORT%" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 goto frontend_ready
set /a FE_WAIT+=1
if %FE_WAIT% geq 120 (
    echo.
    echo   ERROR: Frontend did not start in 120 seconds.
    echo   Check the "Planning Frontend" window for errors.
    pause
    exit /b 1
)
if %FE_WAIT%==15 echo   Waiting for frontend... (%FE_WAIT%s)
if %FE_WAIT%==45 echo   Waiting for frontend... (%FE_WAIT%s)
if %FE_WAIT%==90 echo   Waiting for frontend... (%FE_WAIT%s)
timeout /t 1 >nul
goto wait_frontend

:frontend_ready
echo   [OK] Frontend is ready.
echo.


REM ─── Open browser ─────────────────────────────────────────────────────────────
start "" "http://localhost:%FRONTEND_PORT%"

echo ===============================
echo   PLANNING SYSTEM IS RUNNING
echo ===============================
echo   http://localhost:%FRONTEND_PORT%
echo ===============================
echo.
pause
goto :eof
