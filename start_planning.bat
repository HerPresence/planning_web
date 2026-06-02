@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ================================================================
::  METRICORE DEV LAUNCHER  v4
::  Edit the CONFIG block below -- nothing else needs changing.
:: ================================================================

:: ================================================================
::  CONFIG  -- adjust these paths to match your machine
:: ================================================================
set "PROJECT_ROOT=%~dp0"
set "FRONT_ROOT=%~dp0..\planning_front"
set "NGINX_DIR=C:\nginx"
set "PG_BIN=C:\Program Files\PostgreSQL\18\bin"
set "BACKEND_PORT=8002"
set "FRONTEND_PORT=3000"
set "DB_PORT=5432"
set "DB_NAME=planning_db"
set "DB_USER=postgres"

:: Derived -- do not edit below this line
set "WEB_DIR=%PROJECT_ROOT%"
set "FRONTEND_DIR=%FRONT_ROOT%"
set "NGINX_EXE=%NGINX_DIR%\nginx.exe"
set "PG_ISREADY=%PG_BIN%\pg_isready.exe"
set "PSQL_EXE=%PG_BIN%\psql.exe"
set "WIN_BACKEND=Metricore Backend"
set "WIN_FRONTEND=Metricore Frontend"

:: PID + Log dirs
set "LAUNCHER_DIR=%PROJECT_ROOT%.launcher"
set "LOG_DIR=%PROJECT_ROOT%logs\launcher"
set "PID_BACKEND=%LAUNCHER_DIR%\backend.pid"
set "PID_FRONTEND=%LAUNCHER_DIR%\frontend.pid"
set "PID_POSTGRES=%LAUNCHER_DIR%\postgres.pid"
set "LOG_FILE=%LOG_DIR%\launcher.log"

:: Create dirs silently
if not exist "%LAUNCHER_DIR%" mkdir "%LAUNCHER_DIR%" >nul 2>&1
if not exist "%LOG_DIR%"      mkdir "%LOG_DIR%"      >nul 2>&1

title Metricore DEV Launcher v4

:: ================================================================
::  MAIN MENU
:: ================================================================
:MENU
cls
echo.
echo  ================================================
echo    METRICORE DEV LAUNCHER  v4
echo  ================================================
echo.
echo   --- Daily Development ---
echo   [1] Dev Start        postgres + backend + frontend
echo   [2] Restart Backend  after Python/API changes
echo   [3] Restart Frontend after React/JS changes
echo   [5] Full Restart     kill all + start all
echo.
echo   --- Production ---
echo   [4] Build + Deploy   build + nginx reload  (metricore.com.ua)
echo   [9] Safe Deploy      same + confirmation prompt
echo.
echo   --- Tools ---
echo   [6] Check Status     services, ports, bundle hash
echo   [7] Open URLs        localhost / docs / metricore.com.ua
echo   [8] Database Tools   PostgreSQL diagnostics
echo.
echo   [0] Exit
echo.
echo   Note: [3] = localhost dev server
echo         [4]/[9] = metricore.com.ua production
echo  ================================================
echo.
set "CHOICE="
set /p CHOICE=  Enter option [0-9]:

if "%CHOICE%"=="1" goto ACTION_DEV_START
if "%CHOICE%"=="2" goto ACTION_RESTART_BACKEND
if "%CHOICE%"=="3" goto ACTION_RESTART_FRONTEND
if "%CHOICE%"=="4" goto ACTION_BUILD_DEPLOY
if "%CHOICE%"=="5" goto ACTION_FULL_RESTART
if "%CHOICE%"=="6" goto ACTION_CHECK_STATUS
if "%CHOICE%"=="7" goto ACTION_OPEN_URLS
if "%CHOICE%"=="8" goto ACTION_DB_TOOLS
if "%CHOICE%"=="9" goto ACTION_SAFE_DEPLOY
if "%CHOICE%"=="0" goto ACTION_EXIT

echo.
echo  [!] Invalid choice. Enter 0-9.
timeout /t 2 /nobreak >nul
goto MENU


:: ================================================================
::  [1] DEV START
:: ================================================================
:ACTION_DEV_START
call :FN_LOG "=== DEV START ==="
cls
echo.
echo  ================================================
echo   DEV START  --  postgres + backend + frontend
echo  ================================================
echo.

echo  [1/3] Checking PostgreSQL...
call :FN_ENSURE_POSTGRES
if %errorlevel% neq 0 (
    echo.
    echo  [!!] PostgreSQL failed -- aborting.
    echo       Run services.msc and check PlanningPostgreSQL service.
    call :FN_LOG "ABORT: PostgreSQL failed"
    pause
    goto MENU
)
echo   [OK] PostgreSQL ready  (port %DB_PORT%)
echo.

echo  [2/3] Starting Backend...
call :FN_STOP_BACKEND
call :FN_START_BACKEND
if %errorlevel% neq 0 (
    echo.
    echo  [!!] Backend failed -- check %WIN_BACKEND% window.
    call :FN_LOG "ABORT: Backend failed"
    pause
    goto MENU
)
call :FN_CAPTURE_BACKEND_PID
echo   [OK] Backend ready  http://127.0.0.1:%BACKEND_PORT%  PID: !BACKEND_PID!
call :FN_LOG "Backend ready PID=!BACKEND_PID!"
echo.

echo  [3/3] Starting Frontend...
call :FN_STOP_FRONTEND
call :FN_START_FRONTEND
if %errorlevel% neq 0 (
    echo.
    echo  [!!] Frontend failed -- check %WIN_FRONTEND% window.
    call :FN_LOG "ABORT: Frontend failed"
    pause
    goto MENU
)
call :FN_CAPTURE_FRONTEND_PID
echo   [OK] Frontend ready  http://localhost:%FRONTEND_PORT%  PID: !FRONTEND_PID!
call :FN_LOG "Frontend ready PID=!FRONTEND_PID!"

start "" "http://localhost:%FRONTEND_PORT%"
call :FN_LOG "System started OK"

echo.
echo  ================================================
echo   SYSTEM READY
echo  ================================================
echo   Postgres  : OK  port %DB_PORT%
echo   Backend   : OK  port %BACKEND_PORT%  PID: !BACKEND_PID!
echo   Frontend  : OK  port %FRONTEND_PORT%  PID: !FRONTEND_PID!
echo   Browser   : http://localhost:%FRONTEND_PORT%
echo  ================================================
echo.
echo  (Press any key to return to menu)
pause >nul
goto MENU


:: ================================================================
::  [2] RESTART BACKEND
:: ================================================================
:ACTION_RESTART_BACKEND
call :FN_LOG "=== RESTART BACKEND ==="
cls
echo.
echo  ================================================
echo   RESTART BACKEND  --  after Python/API changes
echo  ================================================
echo.

echo  Stopping backend...
call :FN_STOP_BACKEND
timeout /t 2 /nobreak >nul

echo  Starting backend...
call :FN_START_BACKEND
if %errorlevel% neq 0 (
    echo.
    echo  [!!] Backend restart failed.
    call :FN_LOG "ERROR: Backend restart failed"
    pause
    goto MENU
)
call :FN_CAPTURE_BACKEND_PID

echo.
echo   [OK] Backend restarted successfully
echo   URL : http://127.0.0.1:%BACKEND_PORT%
echo   PID : !BACKEND_PID!
call :FN_LOG "Backend restarted PID=!BACKEND_PID!"
echo.
timeout /t 3 /nobreak >nul
goto MENU


:: ================================================================
::  [3] RESTART FRONTEND  (localhost dev server)
:: ================================================================
:ACTION_RESTART_FRONTEND
call :FN_LOG "=== RESTART FRONTEND DEV ==="
cls
echo.
echo  ================================================
echo   RESTART FRONTEND  --  localhost:%FRONTEND_PORT% dev server
echo  ================================================
echo.
echo   Note: This restarts the LOCAL dev server (npm start).
echo         For production deploy use [4] Build + Deploy.
echo.

echo  Stopping frontend dev server...
call :FN_STOP_FRONTEND
timeout /t 2 /nobreak >nul

echo  Starting frontend dev server...
call :FN_START_FRONTEND
if %errorlevel% neq 0 (
    echo.
    echo  [!!] Frontend restart failed.
    call :FN_LOG "ERROR: Frontend restart failed"
    pause
    goto MENU
)
call :FN_CAPTURE_FRONTEND_PID

echo.
echo   [OK] Frontend restarted successfully
echo   URL : http://localhost:%FRONTEND_PORT%
echo   PID : !FRONTEND_PID!
call :FN_LOG "Frontend restarted PID=!FRONTEND_PID!"
echo.
timeout /t 3 /nobreak >nul
goto MENU


:: ================================================================
::  [4] BUILD + DEPLOY  (no confirmation)
:: ================================================================
:ACTION_BUILD_DEPLOY
call :FN_LOG "=== BUILD + DEPLOY ==="
cls
echo.
echo  ================================================
echo   BUILD + DEPLOY  --  production metricore.com.ua
echo  ================================================
echo.
goto _DO_BUILD_DEPLOY


:: ================================================================
::  [9] SAFE DEPLOY  (with confirmation)
:: ================================================================
:ACTION_SAFE_DEPLOY
call :FN_LOG "=== SAFE DEPLOY (confirm) ==="
cls
echo.
echo  ================================================
echo   PRODUCTION SAFE DEPLOY
echo  ================================================
echo.
echo   You are about to update metricore.com.ua.
echo.
echo   This will:
echo     - run npm build  (~2-5 minutes)
echo     - reload nginx
echo     - serve new bundle to all users
echo.
echo  ------------------------------------------------
set "CONFIRM="
set /p CONFIRM=  Are you sure? Type YES to continue:

if /i not "%CONFIRM%"=="YES" (
    echo.
    echo   Deploy cancelled.
    call :FN_LOG "Safe deploy cancelled"
    timeout /t 2 /nobreak >nul
    goto MENU
)
echo.
call :FN_LOG "Safe deploy confirmed"

:: ================================================================
::  SHARED BUILD LOGIC  (used by [4] and [9])
:: ================================================================
:_DO_BUILD_DEPLOY

:: Pre-check: run_build.py exists
set "BUILD_SCRIPT=%FRONT_ROOT%\run_build.py"
if not exist "%BUILD_SCRIPT%" (
    echo.
    echo  [!!] ERROR: run_build.py not found!
    echo.
    echo   Expected : %BUILD_SCRIPT%
    echo   Current  : %CD%
    echo   FRONT_ROOT = %FRONT_ROOT%
    echo.
    echo   Do NOT run start_planning.bat from C:\Windows\System32
    echo   Launch it from: %PROJECT_ROOT%
    call :FN_LOG "ERROR: run_build.py not found: %BUILD_SCRIPT%"
    pause
    goto MENU
)

:: Record old bundle hash
set "OLD_BUNDLE=none"
for /f "tokens=*" %%f in ('dir /b "%FRONT_ROOT%\build\static\js\main.*.js" 2^>nul') do set "OLD_BUNDLE=%%f"
echo   Previous bundle : %OLD_BUNDLE%
echo.

:: Step 1: Build
echo  [1/3] Running npm build...
echo        Source : %FRONT_ROOT%\src
echo        Output : %FRONT_ROOT%\build
echo        This takes 2-5 minutes...
echo  ------------------------------------------------

pushd "%FRONT_ROOT%"
python run_build.py
set "_BUILD_FAILED=0"
if errorlevel 1 set "_BUILD_FAILED=1"
popd

echo  ------------------------------------------------
echo.

if "%_BUILD_FAILED%"=="1" (
    echo  ================================================
    echo   BUILD FAILED
    echo  ================================================
    echo   Fix errors shown above, then retry.
    call :FN_LOG "BUILD FAILED"
    pause
    goto MENU
)

:: Step 2: Capture new bundle hash
set "NEW_BUNDLE=none"
for /f "tokens=*" %%f in ('dir /b "%FRONT_ROOT%\build\static\js\main.*.js" 2^>nul') do set "NEW_BUNDLE=%%f"

:: Step 3: Reload nginx
echo  [2/3] Reloading nginx...
call :FN_RELOAD_NGINX
if !NGINX_OK!==1 (
    echo   [OK] nginx reloaded -- new bundle is live.
) else (
    echo   [!] nginx reload skipped or failed.
    echo       Checked: %NGINX_EXE%
    echo       Run manually: nginx -s reload
)

:: Step 4: Verify build output
echo.
echo  [3/3] Verifying build...
set "BUILD_INDEX=%FRONT_ROOT%\build\index.html"
if exist "%BUILD_INDEX%" (
    echo   [OK] build\index.html exists.
) else (
    echo   [!!] build\index.html NOT found!
)

:: Summary
echo.
echo  ================================================
echo   BUILD + DEPLOY RESULT
echo  ================================================
if "%OLD_BUNDLE%"=="%NEW_BUNDLE%" (
    if "%NEW_BUNDLE%"=="none" (
        echo   [!!] No bundle in build\static\js\
    ) else (
        echo   Bundle  : UNCHANGED -- %NEW_BUNDLE%
        echo   Source not modified since last build.
    )
) else (
    echo   Old     : %OLD_BUNDLE%
    echo   New     : %NEW_BUNDLE%
    echo   [OK] Bundle hash updated!
)
echo.
echo   Path : %FRONT_ROOT%\build
if !NGINX_OK!==1 (
    echo.
    echo   [OK] metricore.com.ua is now updated.
    echo        Browser: Ctrl+Shift+R for hard refresh.
) else (
    echo.
    echo   [!] nginx was NOT reloaded -- run manually to go live.
)
echo  ================================================
call :FN_LOG "BUILD+DEPLOY OK old=%OLD_BUNDLE% new=%NEW_BUNDLE%"
echo.
pause
goto MENU


:: ================================================================
::  [5] FULL RESTART
:: ================================================================
:ACTION_FULL_RESTART
call :FN_LOG "=== FULL RESTART ==="
cls
echo.
echo  ================================================
echo   FULL RESTART  --  kill all + start all
echo  ================================================
echo.
echo  Stopping all services...
call :FN_STOP_FRONTEND
call :FN_STOP_BACKEND
echo  Waiting for clean shutdown (3s)...
timeout /t 3 /nobreak >nul
echo  Starting all services...
echo.
goto ACTION_DEV_START


:: ================================================================
::  [6] CHECK STATUS
:: ================================================================
:ACTION_CHECK_STATUS
cls
echo.
echo  ================================================
echo   CHECK STATUS  --  services, ports, build info
echo  ================================================
echo.

:: PostgreSQL
set "_PG_ST=DOWN   " & set "_PG_PID=---"
"%PG_ISREADY%" -h 127.0.0.1 -p %DB_PORT% -q >nul 2>&1
if !errorlevel!==0 (
    set "_PG_ST=RUNNING"
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%DB_PORT% " ^| findstr "LISTENING"') do set "_PG_PID=%%a"
)
echo   PostgreSQL : !_PG_ST!   port %DB_PORT%    PID: !_PG_PID!

:: Backend
set "_BE_ST=DOWN   " & set "_BE_PID=---" & set "_BE_HTTP=---"
netstat -ano 2>nul | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 (
    set "_BE_ST=RUNNING"
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%BACKEND_PORT% " ^| findstr "LISTENING"') do set "_BE_PID=%%a"
    call :FN_HEALTH_BACKEND
    if !HEALTH_OK!==1 (set "_BE_HTTP=HTTP OK") else (set "_BE_HTTP=HTTP FAIL")
)
echo   Backend    : !_BE_ST!   port %BACKEND_PORT%    PID: !_BE_PID!   !_BE_HTTP!

:: Frontend dev
set "_FE_ST=DOWN   " & set "_FE_PID=---"
netstat -ano 2>nul | findstr ":%FRONTEND_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 (
    set "_FE_ST=RUNNING"
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%FRONTEND_PORT% " ^| findstr "LISTENING"') do set "_FE_PID=%%a"
)
echo   Frontend   : !_FE_ST!   port %FRONTEND_PORT%    PID: !_FE_PID!   (dev server)

:: nginx
set "_NGX_ST=DOWN   "
netstat -ano 2>nul | findstr ":80 " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 set "_NGX_ST=RUNNING"
echo   nginx      : !_NGX_ST!   port 80  (metricore.com.ua)

:: Build info
echo.
echo   --- Production Build ---
set "_BUNDLE=(none)"
for /f "tokens=*" %%f in ('dir /b "%FRONT_ROOT%\build\static\js\main.*.js" 2^>nul') do set "_BUNDLE=%%f"
echo   Bundle  : !_BUNDLE!
if exist "%FRONT_ROOT%\build\index.html" (
    for /f "tokens=*" %%t in ('powershell -NoProfile -NonInteractive -Command "(Get-Item '%FRONT_ROOT%\build\index.html').LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')" 2^>nul') do (
        echo   Built   : %%t
    )
) else (
    echo   Built   : (no build found)
)
echo   Path    : %FRONT_ROOT%\build

:: Saved PIDs
echo.
echo   --- Saved PIDs ---
if exist "%PID_BACKEND%"  (set /p _SP=<"%PID_BACKEND%"  & echo   backend.pid  = !_SP!)  else echo   backend.pid  = (none)
if exist "%PID_FRONTEND%" (set /p _SP=<"%PID_FRONTEND%" & echo   frontend.pid = !_SP!)  else echo   frontend.pid = (none)

:: Recent log
echo.
echo   --- Last 5 log entries ---
if exist "%LOG_FILE%" (
    powershell -NoProfile -NonInteractive -Command "Get-Content '%LOG_FILE%' -Tail 5" 2>nul
) else (
    echo   (no log yet)
)

echo.
echo  ================================================
pause
goto MENU


:: ================================================================
::  [7] OPEN URLS
:: ================================================================
:ACTION_OPEN_URLS
cls
echo.
echo  ================================================
echo   OPEN URLS
echo  ================================================
echo.
echo   [1] http://localhost:%FRONTEND_PORT%         (dev frontend)
echo   [2] http://localhost:%BACKEND_PORT%/docs     (API docs)
echo   [3] http://metricore.com.ua        (production)
echo   [A] All three
echo.
set "UCHOICE="
set /p UCHOICE=  Which [1/2/3/A]:

if /i "%UCHOICE%"=="1" (
    start "" "http://localhost:%FRONTEND_PORT%"
    echo   Opened: localhost:%FRONTEND_PORT%
)
if /i "%UCHOICE%"=="2" (
    start "" "http://localhost:%BACKEND_PORT%/docs"
    echo   Opened: localhost:%BACKEND_PORT%/docs
)
if /i "%UCHOICE%"=="3" (
    start "" "http://metricore.com.ua"
    echo   Opened: metricore.com.ua
)
if /i "%UCHOICE%"=="a" (
    start "" "http://localhost:%FRONTEND_PORT%"
    start "" "http://localhost:%BACKEND_PORT%/docs"
    start "" "http://metricore.com.ua"
    echo   Opened all three URLs.
)
call :FN_LOG "Open URLs: %UCHOICE%"
echo.
timeout /t 2 /nobreak >nul
goto MENU


:: ================================================================
::  [8] DATABASE TOOLS
:: ================================================================
:ACTION_DB_TOOLS
cls
echo.
echo  ================================================
echo   DATABASE TOOLS  --  PostgreSQL diagnostics
echo  ================================================
echo.

:: Check 1: pg_isready
echo  [1/5] pg_isready...
if exist "%PG_ISREADY%" (
    "%PG_ISREADY%" -h 127.0.0.1 -p %DB_PORT% -q >nul 2>&1
    if !errorlevel!==0 (
        echo   [OK] PostgreSQL accepting connections  (port %DB_PORT%)
    ) else (
        echo   [!!] PostgreSQL NOT ready on port %DB_PORT%
    )
) else (
    echo   [!] pg_isready not found: %PG_ISREADY%
)

:: Check 2: Windows service
echo.
echo  [2/5] Windows service...
set "_SVC=unknown"
for /f "tokens=3" %%s in ('sc query PlanningPostgreSQL 2^>nul ^| findstr "STATE"') do set "_SVC=%%s"
if "!_SVC!"=="4"       echo   [OK] PlanningPostgreSQL: RUNNING
if "!_SVC!"=="1"       echo   [!!] PlanningPostgreSQL: STOPPED
if "!_SVC!"=="unknown" echo   [?]  PlanningPostgreSQL: service not found

:: Check 3: .env file
echo.
echo  [3/5] .env file...
set "ENV_FILE=%PROJECT_ROOT%.env"
if exist "%ENV_FILE%" (
    echo   [OK] .env found: %ENV_FILE%
) else (
    echo   [!] .env not found: %ENV_FILE%
    echo       Using defaults from config.py
)

:: Check 4: DB exists
echo.
echo  [4/5] Database '%DB_NAME%'...
if exist "%PSQL_EXE%" (
    "%PSQL_EXE%" -U %DB_USER% -h 127.0.0.1 -p %DB_PORT% -lqt 2>nul | findstr /i "%DB_NAME%" >nul 2>&1
    if !errorlevel!==0 (
        echo   [OK] Database '%DB_NAME%' exists
    ) else (
        echo   [!!] Database '%DB_NAME%' NOT found
        echo        Run: CREATE DATABASE %DB_NAME%;
    )
) else (
    echo   [!] psql not found: %PSQL_EXE%
)

:: Check 5: Table count
echo.
echo  [5/5] Table count in %DB_NAME%...
if exist "%PSQL_EXE%" (
    for /f %%c in ('"%PSQL_EXE%" -U %DB_USER% -h 127.0.0.1 -p %DB_PORT% -d %DB_NAME% -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='"'"'public'"'"'" 2^>nul') do (
        echo   [OK] Tables in public schema: %%c
    )
) else (
    echo   (psql not found -- skipped)
)

echo.
echo  ================================================
call :FN_LOG "DB Tools check done"
pause
goto MENU


:: ================================================================
::  [0] EXIT
:: ================================================================
:ACTION_EXIT
echo.
echo  Metricore DEV Launcher closed.
echo.
timeout /t 1 /nobreak >nul
exit /b 0


:: ================================================================
::  SUBROUTINES
:: ================================================================

:: ----------------------------------------------------------------
:: FN_ENSURE_POSTGRES
:: ----------------------------------------------------------------
:FN_ENSURE_POSTGRES
"%PG_ISREADY%" -h 127.0.0.1 -p %DB_PORT% -q >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%DB_PORT% " ^| findstr "LISTENING"') do echo %%a > "%PID_POSTGRES%"
    exit /b 0
)
for /f "tokens=3" %%s in ('sc query PlanningPostgreSQL 2^>nul ^| findstr "STATE"') do set "_PG_SVC=%%s"
if "!_PG_SVC!"=="1" (
    echo   Service STOPPED -- starting PlanningPostgreSQL...
    net start PlanningPostgreSQL >nul 2>&1
) else (
    echo   Service starting or running -- waiting for pg_isready...
)
set "_PGW=0"
:_pg_wait_loop
"%PG_ISREADY%" -h 127.0.0.1 -p %DB_PORT% -q >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%DB_PORT% " ^| findstr "LISTENING"') do echo %%a > "%PID_POSTGRES%"
    exit /b 0
)
set /a _PGW+=2
if %_PGW% geq 60 (
    echo   [!!] PostgreSQL timeout after 60s.
    sc query PlanningPostgreSQL
    exit /b 1
)
if %_PGW%==10 echo   Waiting for PostgreSQL... (%_PGW%s)
if %_PGW%==30 echo   Waiting for PostgreSQL... (%_PGW%s)
if %_PGW%==50 echo   Waiting for PostgreSQL... (%_PGW%s)
timeout /t 2 /nobreak >nul
goto _pg_wait_loop


:: ----------------------------------------------------------------
:: FN_START_BACKEND
:: ----------------------------------------------------------------
:FN_START_BACKEND
start "%WIN_BACKEND%" cmd /k "cd /d %WEB_DIR% && call venv\Scripts\activate.bat && echo. && echo  [Backend] http://localhost:%BACKEND_PORT% && python -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"
echo   Waiting for backend HTTP...
set "_BEW=0"
:_be_wait_loop
set "HEALTH_OK=0"
curl -sf --max-time 2 "http://127.0.0.1:%BACKEND_PORT%/docs" >nul 2>&1
if not errorlevel 1 set "HEALTH_OK=1"
if "!HEALTH_OK!"=="0" (
    powershell -NoProfile -NonInteractive -Command "try{$null=(New-Object Net.WebClient).DownloadString('http://127.0.0.1:%BACKEND_PORT%/docs');exit 0}catch{exit 1}" >nul 2>&1
    if not errorlevel 1 set "HEALTH_OK=1"
)
if "!HEALTH_OK!"=="1" (
    echo   [OK] Backend ready on port %BACKEND_PORT%
    exit /b 0
)
set /a _BEW+=1
if %_BEW% geq 30 (
    echo   [WARN] Backend did not respond, but process may still be starting
    exit /b 0
)
if %_BEW%==10 echo   Still waiting... (%_BEW%s)
if %_BEW%==20 echo   Still waiting... (%_BEW%s)
timeout /t 2 /nobreak >nul
goto _be_wait_loop


:: ----------------------------------------------------------------
:: FN_HEALTH_BACKEND  -- sets HEALTH_OK=1 if OK
:: ----------------------------------------------------------------
:FN_HEALTH_BACKEND
set "HEALTH_OK=0"
curl -sf --max-time 3 "http://127.0.0.1:%BACKEND_PORT%/docs" >nul 2>&1
if %errorlevel%==0 (set "HEALTH_OK=1" & exit /b 0)
powershell -NoProfile -NonInteractive -Command "try{$null=(New-Object Net.WebClient).DownloadString('http://127.0.0.1:%BACKEND_PORT%/docs');exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel%==0 set "HEALTH_OK=1"
exit /b 0


:: ----------------------------------------------------------------
:: FN_CAPTURE_BACKEND_PID
:: ----------------------------------------------------------------
:FN_CAPTURE_BACKEND_PID
set "BACKEND_PID=N/A"
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%BACKEND_PORT% " ^| findstr "LISTENING"') do (
    set "BACKEND_PID=%%a"
    echo %%a > "%PID_BACKEND%"
    goto _be_pid_done
)
:_be_pid_done
exit /b 0


:: ----------------------------------------------------------------
:: FN_STOP_BACKEND
:: ----------------------------------------------------------------
:FN_STOP_BACKEND
echo   Stopping backend...
if exist "%PID_BACKEND%" (
    set /p _KILL_PID=<"%PID_BACKEND%"
    if defined _KILL_PID taskkill /PID !_KILL_PID! /T /F >nul 2>&1
    del "%PID_BACKEND%" >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq %WIN_BACKEND%"    /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Planning Backend" /T /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%BACKEND_PORT% " ^| findstr "LISTENING"') do (
    if not "%%a"=="" taskkill /PID %%a /F >nul 2>&1
)
call :FN_LOG "Backend stopped"
exit /b 0


:: ----------------------------------------------------------------
:: FN_START_FRONTEND
:: ----------------------------------------------------------------
:FN_START_FRONTEND
start "%WIN_FRONTEND%" cmd /k "cd /d %FRONTEND_DIR% && echo. && echo  [Frontend] http://localhost:%FRONTEND_PORT% && npm start"
echo   Waiting for frontend (first compile may take 120s)...
set "_FEW=0"
:_fe_wait_loop
netstat -ano 2>nul | findstr ":%FRONTEND_PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _FEW+=1
if %_FEW% geq 120 (echo   [!!] Frontend timeout & exit /b 1)
if %_FEW%==15 echo   Compiling React... (%_FEW%s)
if %_FEW%==45 echo   Still compiling... (%_FEW%s)
if %_FEW%==90 echo   Almost there...   (%_FEW%s)
timeout /t 1 /nobreak >nul
goto _fe_wait_loop


:: ----------------------------------------------------------------
:: FN_CAPTURE_FRONTEND_PID
:: ----------------------------------------------------------------
:FN_CAPTURE_FRONTEND_PID
set "FRONTEND_PID=N/A"
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%FRONTEND_PORT% " ^| findstr "LISTENING"') do (
    set "FRONTEND_PID=%%a"
    echo %%a > "%PID_FRONTEND%"
    goto _fe_pid_done
)
:_fe_pid_done
exit /b 0


:: ----------------------------------------------------------------
:: FN_STOP_FRONTEND
:: ----------------------------------------------------------------
:FN_STOP_FRONTEND
echo   Stopping frontend...
if exist "%PID_FRONTEND%" (
    set /p _KILL_PID=<"%PID_FRONTEND%"
    if defined _KILL_PID taskkill /PID !_KILL_PID! /T /F >nul 2>&1
    del "%PID_FRONTEND%" >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq %WIN_FRONTEND%"    /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Planning Frontend" /T /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%FRONTEND_PORT% " ^| findstr "LISTENING"') do (
    if not "%%a"=="" taskkill /PID %%a /F >nul 2>&1
)
call :FN_LOG "Frontend stopped"
exit /b 0


:: ----------------------------------------------------------------
:: FN_RELOAD_NGINX  -- sets NGINX_OK=1/0
:: ----------------------------------------------------------------
:FN_RELOAD_NGINX
set "NGINX_OK=0"
if exist "%NGINX_EXE%" (
    "%NGINX_EXE%" -s reload >nul 2>&1
    if !errorlevel!==0 set "NGINX_OK=1"
    exit /b 0
)
nginx -s reload >nul 2>&1
if !errorlevel!==0 set "NGINX_OK=1"
exit /b 0


:: ----------------------------------------------------------------
:: FN_LOG  -- append timestamped line to log file
:: ----------------------------------------------------------------
:FN_LOG
set "_L=%~1"
for /f "tokens=2 delims==" %%i in ('wmic os get localdatetime /format:value 2^>nul') do set "_WD=%%i"
set "_TS=!_WD:~0,4!-!_WD:~4,2!-!_WD:~6,2! !_WD:~8,2!:!_WD:~10,2!:!_WD:~12,2!"
echo [!_TS!] !_L! >> "%LOG_FILE%"
exit /b 0
