@echo off
title Planning System
setlocal enabledelayedexpansion

echo ===============================
echo STARTING PLANNING SYSTEM
echo ===============================

echo Script directory: %~dp0
set "SCRIPT_DIR=%~dp0"
set "WEB_DIR=%SCRIPT_DIR%"
set "FRONT_DIR=%SCRIPT_DIR%..\planning_front"
set "DB_PORT=5432"
set "BACKEND_PORT=8002"
set "PG_DATA_DIR=C:\Program Files\PostgreSQL\18\data"
set "BUILD_INDEX=%FRONT_DIR%\build\index.html"

call :FindPgCtl
if errorlevel 1 goto :error

echo Starting PostgreSQL via pg_ctl...
"%PG_CTL_PATH%" start -D "%PG_DATA_DIR%" >nul 2>&1

echo Waiting for PostgreSQL readiness via pg_isready...
set "PG_ISREADY_PATH=C:\Program Files\PostgreSQL\18\bin\pg_isready.exe"
set /a counter=0
:wait_pg_ready
"%PG_ISREADY_PATH%" -h 127.0.0.1 -p %DB_PORT% >nul 2>&1
if not errorlevel 1 (
    echo PostgreSQL is ready.
) else (
    if %counter% geq 30 (
        echo ERROR: PostgreSQL did not become ready in time.
        goto :error
    )
    timeout /t 1 >nul
    set /a counter+=1
    goto wait_pg_ready
)

call :IsPortInUse %BACKEND_PORT%
if errorlevel 1 (
    echo ERROR: Backend port %BACKEND_PORT% is already in use.
    goto :error
)

echo Checking frontend build...
if exist "%BUILD_INDEX%" (
    echo Frontend build found, skipping npm run build.
) else (
    echo Frontend build not found, running npm run build...
    pushd "%FRONT_DIR%" >nul 2>&1 || (
        echo ERROR: Failed to change directory to %FRONT_DIR%.
        goto :error
    )
    npm run build
    if errorlevel 1 (
        popd >nul 2>&1
        echo ERROR: npm run build failed.
        goto :error
    )
    popd >nul 2>&1
)

echo Starting Backend FastAPI...
start "Planning Backend" cmd /k "cd /d %WEB_DIR% && call venv\Scripts\activate && uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"

echo Waiting for backend on http://127.0.0.1:%BACKEND_PORT% ...
call :WaitForHttp http://127.0.0.1:%BACKEND_PORT%/api/articles 30
if errorlevel 1 (
    echo ERROR: Backend did not respond in time.
    goto :error
)

echo Opening browser at http://127.0.0.1:%BACKEND_PORT%
start "" "http://127.0.0.1:%BACKEND_PORT%"

echo ===============================
echo SYSTEM STARTED
echo ===============================
pause
goto :eof

:FindPgCtl
where pg_ctl.exe >nul 2>&1
if errorlevel 0 (
    for /f "usebackq delims=" %%A in (`where pg_ctl.exe 2^>nul`) do (
        set "PG_CTL_PATH=%%~A"
        goto :foundPgCtl
    )
)
set "PG_CTL_PATH=C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
if not exist "%PG_CTL_PATH%" (
    echo ERROR: pg_ctl.exe not found. Install PostgreSQL or add it to PATH.
    exit /b 1
)
:foundPgCtl
exit /b 0

:IsPortInUse
setlocal
set "PORT=%~1"
netstat -ano | findstr /C:":%PORT%" | findstr /C:"LISTENING" >nul 2>&1
if errorlevel 1 (
    endlocal
    exit /b 0
)
endlocal
exit /b 1

:WaitForPort
setlocal
set "PORT=%~1"
set "TIMEOUT=%~2"
set /a counter=0
:waitportloop
if %counter% geq %TIMEOUT% (
    endlocal
    exit /b 1
)
call :IsPortInUse %PORT%
if errorlevel 1 (
    endlocal
    exit /b 0
)
timeout /t 1 >nul
set /a counter+=1
goto waitportloop

:WaitForHttp
setlocal
set "URL=%~1"
set "TIMEOUT=%~2"
set /a counter=0
:waithttploop
if %counter% geq %TIMEOUT% (
    endlocal
    exit /b 1
)
powershell -Command "try { Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 0 (
    endlocal
    exit /b 0
)
timeout /t 1 >nul
set /a counter+=1
goto waithttploop

:error
echo.
echo STARTUP FAILED.
pause
exit /b 1