@echo off
setlocal enabledelayedexpansion

echo ===== PostgreSQL Diagnostic Script =====
echo.

REM Check for pg_ctl
echo [1] Finding pg_ctl...
where pg_ctl.exe >nul 2>&1
if errorlevel 0 (
    for /f "usebackq delims=" %%A in (`where pg_ctl.exe 2^>nul`) do (
        set "PG_CTL_PATH=%%~A"
    )
)
if not defined PG_CTL_PATH (
    set "PG_CTL_PATH=C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
)
echo   pg_ctl location: %PG_CTL_PATH%
if exist "%PG_CTL_PATH%" (
    echo   ✓ pg_ctl found
) else (
    echo   ✗ pg_ctl NOT found
    goto :error
)

REM Check data directory
echo.
echo [2] Checking PostgreSQL data directory...
set "PG_DATA_DIR=C:\Program Files\PostgreSQL\18\data"
echo   Data dir: %PG_DATA_DIR%
if exist "%PG_DATA_DIR%" (
    echo   ✓ Data directory exists
) else (
    echo   ✗ Data directory NOT found
    goto :error
)

REM Check for key files
echo.
echo [3] Checking key PostgreSQL files...
if exist "%PG_DATA_DIR%\postgresql.conf" (
    echo   ✓ postgresql.conf found
) else (
    echo   ✗ postgresql.conf NOT found - CRITICAL
)
if exist "%PG_DATA_DIR%\PG_VERSION" (
    echo   ✓ PG_VERSION file found
    type "%PG_DATA_DIR%\PG_VERSION"
) else (
    echo   ✗ PG_VERSION NOT found - CRITICAL
)
if exist "%PG_DATA_DIR%\postmaster.opts" (
    echo   ✓ postmaster.opts found
) else (
    echo   - postmaster.opts not found (may be OK if never run)
)

REM Check port
echo.
echo [4] Checking if port 5432 is in use...
netstat -ano | findstr /C:":5432" | findstr /C:"LISTENING"
if errorlevel 1 (
    echo   - Port 5432 is NOT listening
) else (
    echo   ! Port 5432 is ALREADY in use (PostgreSQL may be running)
)

REM Check for existing postgres.exe processes
echo.
echo [5] Checking for running postgres.exe processes...
tasklist /FI "IMAGENAME eq postgres.exe" 2>nul
if errorlevel 1 (
    echo   - No postgres.exe running
) else (
    echo   ! postgres.exe is already running
    echo   (Consider stopping it with: taskkill /IM postgres.exe /F)
)

REM Try pg_ctl status
echo.
echo [6] Checking PostgreSQL status with pg_ctl...
"%PG_CTL_PATH%" status -D "%PG_DATA_DIR%"
set "STATUS_CODE=!errorlevel!"
echo   pg_ctl status exit code: %STATUS_CODE%

REM Try to start PostgreSQL
echo.
echo [7] Attempting to start PostgreSQL...
echo   Command: "%PG_CTL_PATH%" start -D "%PG_DATA_DIR%"
"%PG_CTL_PATH%" start -D "%PG_DATA_DIR%"
set "START_CODE=!errorlevel!"
echo   pg_ctl start exit code: %START_CODE%
timeout /t 3 >nul

REM Check if started
echo.
echo [8] Checking if PostgreSQL is responsive...
set "PG_ISREADY_PATH=C:\Program Files\PostgreSQL\18\bin\pg_isready.exe"
"%PG_ISREADY_PATH%" -h 127.0.0.1 -p 5432
set "READY_CODE=!errorlevel!"
echo   pg_isready exit code: %READY_CODE%
echo   (0 = accepting connections, 1 = rejecting, 2 = no response, 3 = no attempt)

if %READY_CODE% equ 0 (
    echo   ✓ PostgreSQL is ready!
) else (
    echo   ✗ PostgreSQL is NOT ready
)

echo.
echo ===== End of Diagnostic =====
pause
goto :eof

:error
echo.
echo ERROR during diagnostic
pause
exit /b 1
