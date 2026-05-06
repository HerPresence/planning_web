@echo off
title Planning System

echo ===============================
echo STARTING PLANNING SYSTEM
echo ===============================

echo Starting PostgreSQL...
"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" start -D "C:\Program Files\PostgreSQL\18\data"

timeout /t 3 > nul

echo Starting Backend FastAPI...
start "Planning Backend" cmd /k "cd /d T:\planning_web && venv\Scripts\activate && uvicorn main:app --host 127.0.0.1 --port 8002"

timeout /t 3 > nul

echo Starting Frontend React...
start "Planning Frontend" cmd /k "cd /d T:\planning_front && npm start"

echo ===============================
echo SYSTEM STARTED
echo ===============================
pause