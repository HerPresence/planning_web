# windows_ssas/ssas_query.ps1
# SSAS DAX query: TRY1=ADODB COM (MSOLAP), TRY2=ADOMD.NET LoadFrom
# Runs on Windows machine that has access to SSAS.
# ALL output via Write-Output (stream 1) so Python subprocess captures it.
# DAX query passed via -QueryFile (UTF-8 temp file).
param(
    [Parameter(Mandatory=$true)]  [string]$Server,
    [Parameter(Mandatory=$true)]  [string]$Database,
    [Parameter(Mandatory=$true)]  [string]$QueryFile,
    [Parameter(Mandatory=$false)] [string]$Login    = "",
    [Parameter(Mandatory=$false)] [string]$Password = "",
    [Parameter(Mandatory=$false)] [int]   $MaxRows  = 0
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Out-Json($obj) { Write-Output ($obj | ConvertTo-Json -Depth 10 -Compress) }

function Write-ExceptionDetail {
    param([string]$tag, $ex)
    Write-Output "[$tag] Message: $($ex.Message)"
    Write-Output "[$tag] Type:    $($ex.GetType().FullName)"
    try { Write-Output "[$tag] HResult: 0x$('{0:X8}' -f [uint32]$ex.HResult)" } catch {}
    if ($ex.InnerException) {
        Write-Output "[$tag] Inner:   $($ex.InnerException.Message)"
    }
}

function Read-AdoRecordset {
    param($rs, [int]$maxRows)
    $columns = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $rs.Fields.Count; $i++) { $columns.Add($rs.Fields.Item($i).Name) }
    $rows  = [System.Collections.Generic.List[object]]::new()
    $count = 0
    while (-not $rs.EOF) {
        $row = [ordered]@{}
        foreach ($col in $columns) {
            $v = $rs.Fields.Item($col).Value
            $row[$col] = if ($null -eq $v -or $v -is [System.DBNull]) { $null } else { $v }
        }
        $rows.Add($row)
        $count++
        if ($count % 5000 -eq 0) { Write-Output "[TRY1] ...fetched $count rows" }
        if ($maxRows -gt 0 -and $count -ge $maxRows) { break }
        $rs.MoveNext()
    }
    return @{ columns = @($columns); rows = @($rows); count = $count }
}

# ---- Read DAX query from UTF-8 temp file ----------------------------------
if (-not (Test-Path $QueryFile)) {
    Out-Json @{ error = "Query file not found: $QueryFile" }
    exit 1
}
$Query = [System.IO.File]::ReadAllText($QueryFile, [System.Text.Encoding]::UTF8)
Write-Output "[SSAS] Query loaded: $($Query.Length) chars"
Write-Output "[SSAS] Server=$Server  DB=$Database"

# ===========================================================================
# TRY1: ADODB COM via MSOLAP (native Windows, no managed DLL needed)
# Tries Server, then localhost, then "." to handle local SSAS where
# connecting to own external IP returns WSAETIMEDOUT.
# ===========================================================================
Write-Output "[TRY1] ---- ADODB COM start ----"

$serverCandidates = [System.Collections.Generic.List[string]]::new()
$serverCandidates.Add($Server)
if ($Server -ne "localhost") { $serverCandidates.Add("localhost") }
if ($Server -ne ".")         { $serverCandidates.Add(".")         }
Write-Output "[TRY1] Candidates: $($serverCandidates -join ', ')"

$adoResult = $null

foreach ($srv in $serverCandidates) {
    if ($Login -and $Password) {
        $tryConn = "Provider=MSOLAP;Data Source=$srv;Initial Catalog=$Database;User ID=$Login;Password=$Password;Connect Timeout=15;"
    } else {
        $tryConn = "Provider=MSOLAP;Data Source=$srv;Initial Catalog=$Database;Integrated Security=SSPI;Connect Timeout=15;"
    }
    $tryConnLog = $tryConn -replace "Password=[^;]+", "Password=***"
    Write-Output "[TRY1][OPEN] Trying Data Source=$srv"

    $adoConn = $null
    try {
        $adoConn = New-Object -ComObject ADODB.Connection
        Write-Output "[TRY1][NEW] OK"
    } catch {
        Write-Output "[TRY1][NEW] FAILED -- ADODB not available."
        Write-ExceptionDetail "TRY1][NEW" $_.Exception
        break
    }

    try {
        $adoConn.ConnectionTimeout = 15
        $adoConn.CommandTimeout    = 30
        $adoConn.Open($tryConn)
        Write-Output "[TRY1][OPEN] OK -- connected with Data Source=$srv"
    } catch {
        Write-Output "[TRY1][OPEN] FAILED with Data Source=$srv"
        Write-ExceptionDetail "TRY1][OPEN" $_.Exception
        try { $adoConn.Close() } catch {}
        $adoConn = $null
        Write-Output "[TRY1] Next candidate..."
        continue
    }

    Write-Output "[TRY1][EXECUTE] Running DAX (timeout=300s)..."
    try {
        $adoCmd = New-Object -ComObject ADODB.Command
        $adoCmd.ActiveConnection = $adoConn
        $adoCmd.CommandText      = $Query
        $adoCmd.CommandTimeout   = 300
        $adoRs     = $adoCmd.Execute()
        $adoResult = Read-AdoRecordset $adoRs $MaxRows
        try { $adoRs.Close()   } catch {}
        try { $adoConn.Close() } catch {}
        Write-Output "[TRY1-OK] $($adoResult.count) rows"
        Write-Output "[TRY1-OK] Columns: $($adoResult.columns -join ' | ')"
        break
    } catch {
        Write-Output "[TRY1][EXECUTE] FAILED."
        Write-ExceptionDetail "TRY1][EXECUTE" $_.Exception
        try { $adoConn.Close() } catch {}
        $adoResult = $null
        break
    }
}

if ($null -ne $adoResult) {
    Out-Json $adoResult
    exit 0
}

Write-Output "[TRY1] ADODB COM failed -- trying ADOMD.NET"

# ===========================================================================
# TRY2: ADOMD.NET via LoadFrom
# Searches: windows_ssas/lib/ then Windows AS OLEDB system paths
# ===========================================================================
Write-Output "[TRY2] ---- ADOMD.NET LoadFrom start ----"

$libDir = Join-Path $scriptDir "lib"
$searchDirs = [System.Collections.Generic.List[string]]::new()
$searchDirs.Add($libDir)
foreach ($ver in @("160","150","140","110")) {
    $searchDirs.Add("C:\Program Files\Microsoft Analysis Services\AS OLEDB\$ver")
    $searchDirs.Add("C:\Program Files\Microsoft.NET\ADOMD.NET\$ver")
    if ($Env:ProgramW6432) {
        $searchDirs.Add("$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\$ver")
    }
}

$adomdAsm = $null

foreach ($dir in ($searchDirs | Select-Object -Unique)) {
    if (-not (Test-Path $dir -PathType Container)) { continue }
    $dlls = Get-ChildItem -Path $dir -Filter "*.dll" -ErrorAction SilentlyContinue
    if (-not $dlls) { continue }
    $adomdDll = $dlls | Where-Object { $_.Name -eq "Microsoft.AnalysisServices.AdomdClient.dll" } | Select-Object -First 1
    if (-not $adomdDll) { continue }
    Write-Output "[TRY2] Found: $($adomdDll.FullName)"
    foreach ($comp in ($dlls | Where-Object { $_.Name -ne $adomdDll.Name })) {
        try { [void][System.Reflection.Assembly]::LoadFrom($comp.FullName) } catch {}
    }
    try {
        $adomdAsm = [System.Reflection.Assembly]::LoadFrom($adomdDll.FullName)
        Write-Output "[TRY2] Loaded v$($adomdAsm.GetName().Version)"
        break
    } catch {
        Write-Output "[TRY2] LoadFrom failed: $($_.Exception.Message)"
    }
}

if ($null -eq $adomdAsm) {
    Out-Json @{ error = "Both ADODB COM and ADOMD.NET failed. Run on Windows with SSAS/MSOLAP installed." }
    exit 1
}

$connStr = if ($Login -and $Password) {
    "Provider=MSOLAP;Data Source=$Server;Initial Catalog=$Database;User ID=$Login;Password=$Password;Connect Timeout=30;"
} else {
    "Provider=MSOLAP;Data Source=$Server;Initial Catalog=$Database;Integrated Security=SSPI;Connect Timeout=30;"
}

$connType = $adomdAsm.GetType("Microsoft.AnalysisServices.AdomdClient.AdomdConnection")
$cmdType  = $adomdAsm.GetType("Microsoft.AnalysisServices.AdomdClient.AdomdCommand")
if (-not $connType -or -not $cmdType) {
    Out-Json @{ error = "AdomdConnection/AdomdCommand types not found in $($adomdAsm.FullName)" }
    exit 1
}

try {
    Write-Output "[TRY2] Connecting..."
    $conn = [Activator]::CreateInstance($connType, @($connStr))
    $conn.Open()
    Write-Output "[TRY2] Connected OK."
    $cmd = [Activator]::CreateInstance($cmdType)
    $cmd.Connection     = $conn
    $cmd.CommandText    = $Query
    $cmd.CommandTimeout = 300
    Write-Output "[TRY2] Executing DAX..."
    $reader     = $cmd.ExecuteReader()
    $fieldCount = $reader.FieldCount
    $columns    = @()
    for ($i = 0; $i -lt $fieldCount; $i++) { $columns += $reader.GetName($i) }
    Write-Output "[TRY2] Columns ($fieldCount): $($columns -join ' | ')"
    $rows     = [System.Collections.Generic.List[object]]::new()
    $rowCount = 0
    while ($reader.Read()) {
        $row = [ordered]@{}
        for ($i = 0; $i -lt $fieldCount; $i++) {
            $v = $reader.GetValue($i)
            $row[$columns[$i]] = if ($null -eq $v -or $v -is [System.DBNull]) { $null } else { $v }
        }
        $rows.Add($row)
        $rowCount++
        if ($rowCount % 5000 -eq 0) { Write-Output "[TRY2] ...fetched $rowCount rows" }
        if ($MaxRows -gt 0 -and $rowCount -ge $MaxRows) { break }
    }
    $reader.Close()
    $conn.Close()
    Write-Output "[TRY2-OK] $rowCount rows"
    Out-Json @{ columns = @($columns); rows = @($rows); count = $rowCount }
    exit 0
} catch {
    Write-Output "[TRY2] FAILED."
    Write-ExceptionDetail "TRY2" $_.Exception
    Out-Json @{ error = $_.Exception.Message }
    exit 1
}
