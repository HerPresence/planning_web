@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ================================================================
::  METRICORE LAUNCHER v7
::  Все через меню. Нічого вручну.
:: ================================================================

:: ── Базові шляхи (самовизначення) ───────────────────────────────
set "WEB_DIR=%~dp0"
if "!WEB_DIR:~-1!"=="\" set "WEB_DIR=!WEB_DIR:~0,-1!"
set "FRONT_DIR=!WEB_DIR!\..\planning_front"

:: ── Налаштування (перезаписуються launcher_config.bat) ──────────
set "NGINX_DIR=C:\nginx-1.28.3\nginx-1.28.3"
set "PG_BIN=C:\Program Files\PostgreSQL\18\bin"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"
set "DB_PORT=5432"
set "DB_NAME=planning_db"
set "DB_USER=postgres"

:: ── Завантажити збережений конфіг ───────────────────────────────
set "LAUNCHER_CONFIG=!WEB_DIR!\launcher_config.bat"
if exist "!LAUNCHER_CONFIG!" call "!LAUNCHER_CONFIG!"

:: ── Похідні шляхи ───────────────────────────────────────────────
set "NGINX_EXE=!NGINX_DIR!\nginx.exe"
set "NGINX_CONF_SRC=!WEB_DIR!\nginx.conf"
set "NGINX_CONF_DST=!NGINX_DIR!\conf\nginx.conf"
set "PG_ISREADY=!PG_BIN!\pg_isready.exe"
set "PSQL_EXE=!PG_BIN!\psql.exe"

:: ── Папки для PID і логів ────────────────────────────────────────
set "PID_DIR=!WEB_DIR!\.launcher"
set "LOG_DIR=!WEB_DIR!\logs\launcher"
set "PID_BE=!PID_DIR!\backend.pid"
set "PID_FE=!PID_DIR!\frontend.pid"
set "LOG_FILE=!LOG_DIR!\launcher.log"
if not exist "!PID_DIR!" mkdir "!PID_DIR!" >nul 2>&1
if not exist "!LOG_DIR!" mkdir "!LOG_DIR!" >nul 2>&1

title Metricore Launcher v7

:: ================================================================
::  ГОЛОВНЕ МЕНЮ
:: ================================================================
:MENU
cls
echo(
echo  ============================================
echo   METRICORE LAUNCHER v7
echo  ============================================
echo   Проект  : !WEB_DIR!
echo   Backend : port !BACKEND_PORT!
echo   nginx   : !NGINX_DIR!
echo  --------------------------------------------
echo(
echo   [1] Запустити все        PostgreSQL + Backend + nginx + Frontend
echo   [2] Перезапустити backend   (після змін Python)
echo   [3] Перезапустити frontend  (після змін React)
echo   [4] Build + Deploy          (оновити metricore.com.ua)
echo   [5] Зупинити все
echo(
echo   [6] Статус               перевірити всі сервіси
echo   [7] Переналаштувати      змінити шлях / порт / nginx
echo   [8] Відкрити посилання   localhost / docs / metricore.com.ua
echo   [9] Діагностика          перевірити процеси / порти / папки / git
echo(
echo   [0] Вихід
echo  ============================================
echo(
set "C="
set /p C=  Вибір:

if "!C!"=="1" goto START_ALL
if "!C!"=="2" goto RESTART_BACKEND
if "!C!"=="3" goto RESTART_FRONTEND
if "!C!"=="4" goto BUILD_DEPLOY
if "!C!"=="5" goto STOP_ALL
if "!C!"=="6" goto CHECK_STATUS
if "!C!"=="7" goto WIZARD
if "!C!"=="8" goto OPEN_URLS
if "!C!"=="9" goto DIAGNOSTICS
if "!C!"=="0" goto EXIT_LAUNCHER
echo  Невірний вибір.
timeout /t 2 /nobreak >nul
goto MENU


:: ================================================================
::  [1] ЗАПУСТИТИ ВСЕ
:: ================================================================
:START_ALL
cls
echo(
echo  ============================================
echo   ЗАПУСК СИСТЕМИ
echo  ============================================
echo(

:: 1. PostgreSQL
echo  [1/4] PostgreSQL...
call :ENSURE_POSTGRES
if !errorlevel!==0 (
    echo   OK  PostgreSQL на порту !DB_PORT!
) else (
    echo   ПОМИЛКА  PostgreSQL не запустився
    echo   Перевірте services.msc - служба PlanningPostgreSQL
    pause & goto MENU
)

:: 2. Backend
echo(
echo  [2/4] Backend...
call :CHECK_BACKEND_HTTP
if !HEALTH_OK!==1 (
    echo   OK  Backend вже запущено на порту !BACKEND_PORT!
) else (
    call :STOP_BACKEND
    call :START_BACKEND_WINDOW
    echo   Чекаю запуску backend ^(до 60 сек^)...
    call :WAIT_BACKEND
    if !HEALTH_OK!==1 (
        echo   OK  Backend запущено на порту !BACKEND_PORT!
    ) else (
        echo   УВАГА  Backend не відповів за 60 сек
        echo   Перевірте вікно "Metricore Backend" на помилки
    )
)

:: 3. nginx
echo(
echo  [3/4] nginx...
if exist "!NGINX_EXE!" (
    if exist "!NGINX_CONF_SRC!" (
        copy /y "!NGINX_CONF_SRC!" "!NGINX_CONF_DST!" >nul 2>&1
    )
    tasklist 2>nul | findstr /i "nginx.exe" >nul 2>&1
    if !errorlevel!==0 (
        "!NGINX_EXE!" -s reload >nul 2>&1
        echo   OK  nginx перезавантажено
    ) else (
        start "" "!NGINX_EXE!"
        timeout /t 2 /nobreak >nul
        echo   OK  nginx запущено
    )
) else (
    echo   ПРОПУСК  nginx не знайдено: !NGINX_EXE!
    echo   Запустіть [7] для налаштування шляху nginx
)

:: 4. Frontend
echo(
echo  [4/4] Frontend dev server...
call :CHECK_FRONTEND
if !FE_RUNNING!==1 (
    echo   OK  Frontend вже запущено на порту !FRONTEND_PORT!
) else (
    call :STOP_FRONTEND
    call :START_FRONTEND_WINDOW
    echo   Чекаю запуску frontend ^(до 120 сек^)...
    call :WAIT_FRONTEND
    if !FE_RUNNING!==1 (
        echo   OK  Frontend запущено на порту !FRONTEND_PORT!
    ) else (
        echo   УВАГА  Frontend не запустився за 120 сек
    )
)

:: Відкрити браузер
start "" "http://localhost:!FRONTEND_PORT!"

echo(
echo  ============================================
echo   СИСТЕМА ЗАПУЩЕНА
echo  ============================================
echo   localhost:!FRONTEND_PORT!       - розробка
echo   metricore.com.ua    - продакшн
echo  ============================================
call :LOG "START_ALL done"
echo(
pause
goto MENU


:: ================================================================
::  [2] ПЕРЕЗАПУСТИТИ BACKEND
:: ================================================================
:RESTART_BACKEND
cls
echo(
echo  Зупиняю backend...
call :STOP_BACKEND
timeout /t 2 /nobreak >nul
echo  Запускаю backend...
call :START_BACKEND_WINDOW
echo  Чекаю ^(до 60 сек^)...
call :WAIT_BACKEND
if !HEALTH_OK!==1 (
    echo   OK  Backend на порту !BACKEND_PORT!
) else (
    echo   Не вдалось. Перевірте вікно "Metricore Backend".
)
call :LOG "RESTART_BACKEND"
timeout /t 3 /nobreak >nul
goto MENU


:: ================================================================
::  [3] ПЕРЕЗАПУСТИТИ FRONTEND
:: ================================================================
:RESTART_FRONTEND
cls
echo(
echo  Зупиняю frontend...
call :STOP_FRONTEND
timeout /t 2 /nobreak >nul
echo  Запускаю frontend...
call :START_FRONTEND_WINDOW
echo  Чекаю ^(до 120 сек^)...
call :WAIT_FRONTEND
if !FE_RUNNING!==1 (
    echo   OK  Frontend на порту !FRONTEND_PORT!
) else (
    echo   Не вдалось. Перевірте вікно "Metricore Frontend".
)
call :LOG "RESTART_FRONTEND"
timeout /t 3 /nobreak >nul
goto MENU


:: ================================================================
::  [4] BUILD + DEPLOY (оновити metricore.com.ua)
:: ================================================================
:BUILD_DEPLOY
cls
echo(
echo  ============================================
echo   BUILD + DEPLOY
echo  ============================================
echo(
echo  Зупиняю nginx перед збіркою...
if exist "!NGINX_EXE!" (
    "!NGINX_EXE!" -s stop >nul 2>&1
    taskkill /IM nginx.exe /F >nul 2>&1
    timeout /t 2 /nobreak >nul
    echo   OK  nginx зупинено
)
echo(
echo  Збираю frontend ^(2-5 хвилин^)...
set "_OLD_BUNDLE=none"
for /f "tokens=*" %%f in ('dir /b "!FRONT_DIR!\build\static\js\main.*.js" 2^>nul') do set "_OLD_BUNDLE=%%f"
echo   Поточний bundle: !_OLD_BUNDLE!
echo(
pushd "!FRONT_DIR!"
python run_build.py
set "_BUILD_ERR=!errorlevel!"
popd
echo(
if "!_BUILD_ERR!"=="0" (
    set "_NEW_BUNDLE=none"
    for /f "tokens=*" %%f in ('dir /b "!FRONT_DIR!\build\static\js\main.*.js" 2^>nul') do set "_NEW_BUNDLE=%%f"
    echo   OK  Новий bundle: !_NEW_BUNDLE!
) else (
    echo   ПОМИЛКА збірки. Запускаю nginx назад...
    if exist "!NGINX_EXE!" start "" "!NGINX_EXE!"
    call :LOG "BUILD FAILED"
    pause & goto MENU
)
echo(
echo  Копіюю nginx.conf та запускаю nginx...
if exist "!NGINX_CONF_SRC!" (
    if not exist "!NGINX_DIR!\conf" mkdir "!NGINX_DIR!\conf" >nul 2>&1
    copy /y "!NGINX_CONF_SRC!" "!NGINX_CONF_DST!" >nul 2>&1
)
if exist "!NGINX_EXE!" (
    start "" "!NGINX_EXE!"
    timeout /t 2 /nobreak >nul
    echo   OK  nginx запущено
)
echo(
echo  ============================================
echo   DEPLOY ЗАВЕРШЕНО
echo   Ctrl+Shift+R в браузері для hard refresh
echo  ============================================
call :LOG "BUILD_DEPLOY done old=!_OLD_BUNDLE! new=!_NEW_BUNDLE!"
pause
goto MENU


:: ================================================================
::  [5] ЗУПИНИТИ ВСЕ
:: ================================================================
:STOP_ALL
cls
echo  Зупиняю frontend...
call :STOP_FRONTEND
echo  Зупиняю backend...
call :STOP_BACKEND
echo  Зупиняю nginx...
if exist "!NGINX_EXE!" "!NGINX_EXE!" -s stop >nul 2>&1
taskkill /IM nginx.exe /F >nul 2>&1
echo   OK  Все зупинено.
call :LOG "STOP_ALL"
timeout /t 3 /nobreak >nul
goto MENU


:: ================================================================
::  [6] СТАТУС
:: ================================================================
:CHECK_STATUS
cls
echo(
echo  ============================================
echo   СТАТУС СИСТЕМИ
echo  ============================================
echo   Проект  : !WEB_DIR!
echo   Backend : port !BACKEND_PORT!
echo(

set "_PG=DOWN"
"!PG_ISREADY!" -h 127.0.0.1 -p !DB_PORT! -q >nul 2>&1
if !errorlevel!==0 set "_PG=RUNNING"
echo   PostgreSQL : !_PG!   ^(port !DB_PORT!^)

set "_BE=DOWN"
netstat -ano 2>nul | findstr ":!BACKEND_PORT! " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 (
    set "_BE=RUNNING"
    call :CHECK_BACKEND_HTTP
    if !HEALTH_OK!==1 (set "_BE=RUNNING ^(HTTP OK^)") else (set "_BE=RUNNING ^(HTTP не відповів^)")
)
echo   Backend    : !_BE!   ^(port !BACKEND_PORT!^)

set "_FE=DOWN"
netstat -ano 2>nul | findstr ":!FRONTEND_PORT! " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 set "_FE=RUNNING"
echo   Frontend   : !_FE!   ^(port !FRONTEND_PORT!^)

set "_NGX=DOWN"
tasklist 2>nul | findstr /i "nginx.exe" >nul 2>&1
if !errorlevel!==0 set "_NGX=RUNNING"
echo   nginx      : !_NGX!   ^(port 80^)

echo(
echo   --- Build ---
set "_BND=^(немає^)"
for /f "tokens=*" %%f in ('dir /b "!FRONT_DIR!\build\static\js\main.*.js" 2^>nul') do set "_BND=%%f"
echo   Bundle: !_BND!
if exist "!FRONT_DIR!\build\index.html" (
    for /f "tokens=*" %%t in ('powershell -NoProfile -NonInteractive -Command "(Get-Item '!FRONT_DIR!\build\index.html').LastWriteTime.ToString('yyyy-MM-dd HH:mm')" 2^>nul') do echo   Built : %%t
)
echo(
echo  ============================================
pause
goto MENU


:: ================================================================
::  [7] МАЙСТЕР ПЕРЕНАЛАШТУВАННЯ
:: ================================================================
:WIZARD
cls
echo(
echo  ============================================
echo   ПЕРЕНАЛАШТУВАННЯ ШЛЯХІВ
echo  ============================================
echo(
echo   Поточний шлях проекту: !WEB_DIR!\..
echo   ^(Або залиш порожнім для автоматичного визначення^)
echo(

set "_NP="
set /p _NP=  Новий шлях до папки Metricore (Enter = !WEB_DIR!\..):
if "!_NP!"=="" (
    set "_NP=!WEB_DIR!\.."
    echo   Використовую: !_NP!
)
if "!_NP:~-1!"=="\" set "_NP=!_NP:~0,-1!"

set "_NF=!_NP!\planning_front"
set "_NB=!_NP!\planning_web"

echo(
if exist "!_NF!\package.json" (echo   OK  planning_front знайдено) else (echo   НЕ ЗНАЙДЕНО: !_NF!)
if exist "!_NB!\main.py"      (echo   OK  planning_web знайдено)  else (echo   НЕ ЗНАЙДЕНО: !_NB!)

echo(
set "_NPORT="
set /p _NPORT=  Backend порт [!BACKEND_PORT!]:
if "!_NPORT!"=="" set "_NPORT=!BACKEND_PORT!"

echo(
set "_NNE=!NGINX_EXE!"
echo   Поточний nginx: !_NNE!
set "_NNEW="
set /p _NNEW=  Шлях до nginx.exe (Enter = залишити):
if not "!_NNEW!"=="" set "_NNE=!_NNEW!"

set "_NND=!_NNE!"
for %%d in ("!_NNE!") do set "_NND=%%~dpd"
if "!_NND:~-1!"=="\" set "_NND=!_NND:~0,-1!"

:: Зберегти конфіг
echo(
echo   Зберігаю конфіг...
> "!_NB!\launcher_config.bat" echo set "NGINX_DIR=!_NND!"
>> "!_NB!\launcher_config.bat" echo set "BACKEND_PORT=!_NPORT!"

:: Оновити nginx.conf
set "_BFWD=!_NF!\build"
set "_BFWD=!_BFWD:\=/!"
call :WRITE_NGINX_CONF "!_NB!\nginx.conf" "!_BFWD!" "!_NPORT!"
echo   OK  nginx.conf оновлено

:: Скопіювати nginx.conf
if exist "!_NNE!" (
    if not exist "!_NND!\conf" mkdir "!_NND!\conf" >nul 2>&1
    copy /y "!_NB!\nginx.conf" "!_NND!\conf\nginx.conf" >nul 2>&1
    "!_NNE!" -t >nul 2>&1
    if !errorlevel!==0 (
        echo   OK  nginx.conf скопійовано і перевірено
        "!_NNE!" -s reload >nul 2>&1
        if !errorlevel!==0 (
            echo   OK  nginx перезавантажено
        ) else (
            taskkill /IM nginx.exe /F >nul 2>&1
            timeout /t 1 /nobreak >nul
            start "" "!_NNE!"
            echo   OK  nginx перезапущено
        )
    ) else (
        echo   ПОМИЛКА nginx -t. Перевірте конфіг.
    )
)

:: Застосувати до поточного сеансу
set "NGINX_DIR=!_NND!"
set "NGINX_EXE=!_NNE!"
set "BACKEND_PORT=!_NPORT!"
set "NGINX_CONF_SRC=!_NB!\nginx.conf"
set "NGINX_CONF_DST=!_NND!\conf\nginx.conf"
set "LAUNCHER_CONFIG=!_NB!\launcher_config.bat"

echo(
echo  ============================================
echo   Готово! Тепер запустіть [1] Запустити все.
echo  ============================================
call :LOG "WIZARD done port=!_NPORT! nginx=!_NNE!"
pause
goto MENU


:: ================================================================
::  [8] ВІДКРИТИ ПОСИЛАННЯ
:: ================================================================
:OPEN_URLS
start "" "http://localhost:!FRONTEND_PORT!"
start "" "http://localhost:!BACKEND_PORT!/docs"
start "" "http://metricore.com.ua"
echo  Відкрито три вкладки.
timeout /t 2 /nobreak >nul
goto MENU


:: ================================================================
::  [9] ДІАГНОСТИКА
:: ================================================================
:DIAGNOSTICS
cls
echo(
echo  ============================================
echo   METRICORE DIAGNOSTICS
echo  ============================================
echo   Час: %date% %time%
echo(
echo  --- Шляхи ---
echo   PROJECT_ROOT  : !WEB_DIR!
echo   FRONT_DIR     : !FRONT_DIR!
echo   NGINX_DIR     : !NGINX_DIR!
echo   BACKEND_PORT  : !BACKEND_PORT!
echo   FRONTEND_PORT : !FRONTEND_PORT!
echo(
echo  --- nginx proxy_pass (з nginx.conf) ---
if exist "!NGINX_CONF_SRC!" (
    findstr /i "proxy_pass" "!NGINX_CONF_SRC!" 2>nul
) else (
    echo   nginx.conf не знайдено: !NGINX_CONF_SRC!
)
echo(
echo  --- Frontend proxy (з package.json) ---
if exist "!FRONT_DIR!\package.json" (
    findstr /i "proxy" "!FRONT_DIR!\package.json" 2>nul
) else (
    echo   package.json не знайдено
)
echo(
echo  --- Порти (netstat) ---
echo   Backend  :%BACKEND_PORT%:
netstat -ano 2>nul | findstr ":!BACKEND_PORT! " | findstr "LISTENING"
echo   Frontend :%FRONTEND_PORT%:
netstat -ano 2>nul | findstr ":!FRONTEND_PORT! " | findstr "LISTENING"
echo   nginx :80:
netstat -ano 2>nul | findstr ":80 " | findstr "LISTENING"
echo(
echo  --- Python процеси ---
wmic process where "name='python.exe' or name='python3.exe'" get processid,commandline 2>nul | findstr /v "^$"
echo(
echo  --- Node процеси ---
wmic process where "name='node.exe'" get processid,commandline 2>nul | findstr /v "^$"
echo(
echo  --- git status planning_web ---
pushd "!WEB_DIR!" >nul 2>&1
git -C "!WEB_DIR!" log --oneline -3 2>nul
git -C "!WEB_DIR!" status --short 2>nul
popd >nul 2>&1
echo(
echo  --- GET /api/system/runtime-info ---
curl -sf --max-time 5 "http://127.0.0.1:!BACKEND_PORT!/api/system/runtime-info" 2>nul
if errorlevel 1 (
    echo   ПОМИЛКА: backend не відповідає на http://127.0.0.1:!BACKEND_PORT!
    echo   Спробуйте також: curl http://localhost:!BACKEND_PORT!/api/system/runtime-info
)
echo(
echo  ============================================
pause
goto MENU


:: ================================================================
::  [0] ВИХІД
:: ================================================================
:EXIT_LAUNCHER
echo  До побачення.
timeout /t 1 /nobreak >nul
exit /b 0


:: ================================================================
::  ПІДПРОГРАМИ
:: ================================================================

:ENSURE_POSTGRES
"!PG_ISREADY!" -h 127.0.0.1 -p !DB_PORT! -q >nul 2>&1
if not errorlevel 1 exit /b 0
set "_SVC=0"
for /f "tokens=3" %%s in ('sc query PlanningPostgreSQL 2^>nul ^| findstr "STATE"') do set "_SVC=%%s"
if "!_SVC!"=="1" net start PlanningPostgreSQL >nul 2>&1
set "_W=0"
:_pg_loop
"!PG_ISREADY!" -h 127.0.0.1 -p !DB_PORT! -q >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _W+=2
if !_W! geq 60 exit /b 1
timeout /t 2 /nobreak >nul
goto _pg_loop

:CHECK_BACKEND_HTTP
set "HEALTH_OK=0"
curl -sf --max-time 3 "http://127.0.0.1:!BACKEND_PORT!/docs" >nul 2>&1
if !errorlevel!==0 (set "HEALTH_OK=1" & exit /b 0)
powershell -NoProfile -NonInteractive -Command "try{$null=(New-Object Net.WebClient).DownloadString('http://127.0.0.1:!BACKEND_PORT!/docs');exit 0}catch{exit 1}" >nul 2>&1
if !errorlevel!==0 set "HEALTH_OK=1"
exit /b 0

:START_BACKEND_WINDOW
start "Metricore Backend" cmd /k "cd /d !WEB_DIR! && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 127.0.0.1 --port !BACKEND_PORT! --reload"
exit /b 0

:WAIT_BACKEND
set "_BW=0"
:_be_loop
call :CHECK_BACKEND_HTTP
if "!HEALTH_OK!"=="1" exit /b 0
set /a _BW+=2
if !_BW! geq 60 exit /b 0
if !_BW!==10 echo   ще чекаю... ^(!_BW!с^)
if !_BW!==30 echo   ще чекаю... ^(!_BW!с^)
timeout /t 2 /nobreak >nul
goto _be_loop

:STOP_BACKEND
if exist "!PID_BE!" (
    set /p _K=<"!PID_BE!"
    if defined _K taskkill /PID !_K! /T /F >nul 2>&1
    del "!PID_BE!" >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq Metricore Backend" /T /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":!BACKEND_PORT! " ^| findstr "LISTENING"') do (
    if not "%%a"=="" taskkill /PID %%a /F >nul 2>&1
)
exit /b 0

:CHECK_FRONTEND
set "FE_RUNNING=0"
netstat -ano 2>nul | findstr ":!FRONTEND_PORT! " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 set "FE_RUNNING=1"
exit /b 0

:START_FRONTEND_WINDOW
start "Metricore Frontend" cmd /k "cd /d !FRONT_DIR! && npm start"
exit /b 0

:WAIT_FRONTEND
set "_FW=0"
:_fe_loop
netstat -ano 2>nul | findstr ":!FRONTEND_PORT! " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (set "FE_RUNNING=1" & exit /b 0)
set /a _FW+=1
if !_FW! geq 120 exit /b 0
if !_FW!==20 echo   компілюю React... ^(!_FW!с^)
if !_FW!==60 echo   ще компілюю... ^(!_FW!с^)
timeout /t 1 /nobreak >nul
goto _fe_loop

:STOP_FRONTEND
if exist "!PID_FE!" (
    set /p _K=<"!PID_FE!"
    if defined _K taskkill /PID !_K! /T /F >nul 2>&1
    del "!PID_FE!" >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq Metricore Frontend" /T /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":!FRONTEND_PORT! " ^| findstr "LISTENING"') do (
    if not "%%a"=="" taskkill /PID %%a /F >nul 2>&1
)
exit /b 0

:WRITE_NGINX_CONF
set "_P=%~1"
set "_F=%~2"
set "_T=%~3"
if exist "!_P!" del "!_P!" >nul 2>&1
>> "!_P!" echo worker_processes  1;
>> "!_P!" echo(
>> "!_P!" echo events {
>> "!_P!" echo     worker_connections  1024;
>> "!_P!" echo }
>> "!_P!" echo(
>> "!_P!" echo http {
>> "!_P!" echo     include       mime.types;
>> "!_P!" echo     default_type  application/octet-stream;
>> "!_P!" echo     sendfile        on;
>> "!_P!" echo     keepalive_timeout  65;
>> "!_P!" echo(
>> "!_P!" echo     server {
>> "!_P!" echo         listen 80;
>> "!_P!" echo         server_name localhost 127.0.0.1 metricore.com.ua www.metricore.com.ua;
>> "!_P!" echo         client_max_body_size 100M;
>> "!_P!" echo(
>> "!_P!" echo         location /static/ {
>> "!_P!" echo             alias !_F!/static/;
>> "!_P!" echo             add_header Cache-Control "public, max-age=31536000, immutable";
>> "!_P!" echo         }
>> "!_P!" echo(
>> "!_P!" echo         location /api/ {
>> "!_P!" echo             proxy_pass http://127.0.0.1:!_T!;
>> "!_P!" echo             proxy_set_header Host $host;
>> "!_P!" echo             proxy_set_header X-Real-IP $remote_addr;
>> "!_P!" echo         }
>> "!_P!" echo(
>> "!_P!" echo         location /docs {
>> "!_P!" echo             proxy_pass http://127.0.0.1:!_T!/docs;
>> "!_P!" echo             proxy_set_header Host $host;
>> "!_P!" echo         }
>> "!_P!" echo(
>> "!_P!" echo         location /openapi.json {
>> "!_P!" echo             proxy_pass http://127.0.0.1:!_T!/openapi.json;
>> "!_P!" echo             proxy_set_header Host $host;
>> "!_P!" echo         }
>> "!_P!" echo(
>> "!_P!" echo         location / {
>> "!_P!" echo             root !_F!;
>> "!_P!" echo             try_files $uri /index.html;
>> "!_P!" echo             add_header Cache-Control "no-cache, no-store, must-revalidate";
>> "!_P!" echo             add_header Pragma "no-cache";
>> "!_P!" echo             add_header Expires "0";
>> "!_P!" echo         }
>> "!_P!" echo     }
>> "!_P!" echo }
exit /b 0

:LOG
set "_L=%~1"
for /f "tokens=2 delims==" %%i in ('wmic os get localdatetime /format:value 2^>nul') do set "_D=%%i"
echo [!_D:~0,4!-!_D:~4,2!-!_D:~6,2! !_D:~8,2!:!_D:~10,2!] !_L! >> "!LOG_FILE!"
exit /b 0
