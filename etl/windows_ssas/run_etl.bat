@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  ETL: Windows SSAS -^> stg_pnl_olap
echo  %date% %time%
echo ============================================================

cd /d "%~dp0"

rem Use planning_web venv (two levels up: windows_ssas -> etl -> planning_web)
set VENV_ACTIVATE=%~dp0..\..\venv\Scripts\activate.bat

if exist "%VENV_ACTIVATE%" (
    echo [OK] Activating venv: %VENV_ACTIVATE%
    call "%VENV_ACTIVATE%"
) else (
    echo [WARN] venv not found at %VENV_ACTIVATE%, using system Python
)

rem Check required packages
python -c "import psycopg2, dotenv" 2>nul
if errorlevel 1 (
    echo [INFO] Installing required packages...
    pip install psycopg2-binary python-dotenv
)

echo.
echo [RUN] python etl_pnl_ssas.py
python etl_pnl_ssas.py

set ETL_EXIT=%errorlevel%

echo.
if %ETL_EXIT% equ 0 (
    echo [OK] ETL finished successfully.
) else (
    echo [ERROR] ETL failed with exit code %ETL_EXIT%.
)

echo ============================================================
echo  Done: %date% %time%
echo ============================================================

endlocal
exit /b %ETL_EXIT%
