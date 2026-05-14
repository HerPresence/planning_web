@echo off
title Install OLAP dependencies / Check ADOMD.NET
cd /d "%~dp0"

echo =============================================
echo  OLAP / SSAS Setup Check
echo =============================================
echo.
echo  Transport: PowerShell + ADOMD.NET (OLE DB)
echo  Python pyadomd / pythonnet NOT required.
echo.

REM ── 1. pyodbc (for sql_odbc sources — regular SQL Server) ────────────────────
echo [1/3] Installing pyodbc (for SQL Server ODBC sources)...
call venv\Scripts\pip install pyodbc
if errorlevel 1 (
    echo  WARNING: pip install pyodbc failed. SQL Server ODBC sources may not work.
) else (
    echo  [OK] pyodbc installed.
)
echo.

REM ── 2. Check for ADOMD.NET / MSOLAP OLE DB client ────────────────────────────
echo [2/3] Checking for Microsoft Analysis Services ADOMD.NET client...
echo.

set "FOUND_ADOMD="

set "P160=%ProgramFiles%\Microsoft Analysis Services\AS OLEDB\160\Microsoft.AnalysisServices.AdomdClient.dll"
set "P150=%ProgramFiles%\Microsoft Analysis Services\AS OLEDB\150\Microsoft.AnalysisServices.AdomdClient.dll"
set "P140=%ProgramFiles%\Microsoft Analysis Services\AS OLEDB\140\Microsoft.AnalysisServices.AdomdClient.dll"
set "P110=%ProgramFiles%\Microsoft Analysis Services\AS OLEDB\110\Microsoft.AnalysisServices.AdomdClient.dll"

if exist "%P160%" ( echo  [OK] Found ADOMD.NET 16.x: %P160% & set FOUND_ADOMD=1 )
if exist "%P150%" ( echo  [OK] Found ADOMD.NET 15.x: %P150% & set FOUND_ADOMD=1 )
if exist "%P140%" ( echo  [OK] Found ADOMD.NET 14.x: %P140% & set FOUND_ADOMD=1 )
if exist "%P110%" ( echo  [OK] Found ADOMD.NET 11.x: %P110% & set FOUND_ADOMD=1 )

if not defined FOUND_ADOMD (
    echo  [MISSING] Microsoft.AnalysisServices.AdomdClient.dll not found.
    echo.
    echo  For SSAS Tabular / DAX sources (source_type = olap_ssas_dax):
    echo  Install Microsoft Analysis Services OLE DB Provider (MSOLAP):
    echo.
    echo    Option A: SQL Server Feature Pack
    echo      https://www.microsoft.com/en-us/download/details.aspx?id=104594
    echo      Download: SSASOLEDB.msi
    echo.
    echo    Option B: Microsoft ADOMD.NET standalone
    echo      https://learn.microsoft.com/en-us/analysis-services/client-libraries
    echo.
    echo  NOTE: Python pyadomd / pythonnet are NOT required.
    echo        SSAS DAX is executed via PowerShell script (tools\read_ssas_dax.ps1).
)
echo.

REM ── 3. Verify PowerShell script is in place ───────────────────────────────────
echo [3/3] Checking PowerShell SSAS script...
if exist "tools\read_ssas_dax.ps1" (
    echo  [OK] tools\read_ssas_dax.ps1 found.
) else (
    echo  [MISSING] tools\read_ssas_dax.ps1 not found!
    echo  Make sure the file exists at: %~dp0tools\read_ssas_dax.ps1
)
echo.

echo =============================================
echo  Done. Restart the backend server.
echo =============================================
echo.

REM Optional: run SSAS test
set /p RUN_TEST="Run SSAS connection test now? (y/n): "
if /i "%RUN_TEST%"=="y" (
    echo.
    echo Running test_olap.py ssas ...
    call venv\Scripts\python test_olap.py ssas
)

pause
