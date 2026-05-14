# ssas_query.ps1  SSAS DAX query via ADOMD.NET (cross-platform: Mac pwsh + Windows powershell)
# ALL output uses Write-Output so Python subprocess captures stdout (stream 1).
# Query is passed via -QueryFile (temp UTF-8 file) to avoid shell escaping issues.
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
        try { Write-Output "[$tag] Inner.HResult: 0x$('{0:X8}' -f [uint32]$ex.InnerException.HResult)" } catch {}
    }
}

# Read DAX query from temp file (UTF-8)
if (-not (Test-Path $QueryFile)) {
    Out-Json @{ error = "Query file not found: $QueryFile" }
    exit 1
}
$Query = [System.IO.File]::ReadAllText($QueryFile, [System.Text.Encoding]::UTF8)
Write-Output "[ETL] Query loaded: $($Query.Length) chars from $QueryFile"

# Build connection string
if ($Login -and $Password) {
    $connStr = "Provider=MSOLAP;Data Source=$Server;Initial Catalog=$Database;User ID=$Login;Password=$Password;Connect Timeout=30;"
} else {
    $connStr = "Provider=MSOLAP;Data Source=$Server;Initial Catalog=$Database;Integrated Security=SSPI;Connect Timeout=30;"
}
$connStrLog = $connStr -replace "Password=[^;]+", "Password=***"
Write-Output "[ETL] Server=$Server  DB=$Database"
Write-Output "[ETL] ConnStr: $connStrLog"

# Search order: etl/lib/ first (cross-platform NuGet DLL), then Windows system paths
$searchDirs = [System.Collections.Generic.List[string]]::new()
$searchDirs.Add((Join-Path $scriptDir "lib"))
$searchDirs.Add("C:\Program Files\Microsoft Analysis Services\AS OLEDB\160")
$searchDirs.Add("C:\Program Files\Microsoft Analysis Services\AS OLEDB\150")
$searchDirs.Add("C:\Program Files\Microsoft Analysis Services\AS OLEDB\140")
$searchDirs.Add("C:\Program Files\Microsoft.NET\ADOMD.NET\160")
$searchDirs.Add("C:\Program Files\Microsoft.NET\ADOMD.NET\150")
$searchDirs.Add("C:\Program Files\Microsoft.NET\ADOMD.NET\140")
if ($Env:ProgramW6432) {
    $searchDirs.Add("$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\160")
    $searchDirs.Add("$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\150")
    $searchDirs.Add("$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\140")
}

$adomdAsm = $null

foreach ($dir in $searchDirs) {
    if (-not (Test-Path $dir -PathType Container)) { continue }
    $dlls = Get-ChildItem -Path $dir -Filter "*.dll" -ErrorAction SilentlyContinue
    if (-not $dlls) { continue }

    $adomdDll = $dlls | Where-Object { $_.Name -eq "Microsoft.AnalysisServices.AdomdClient.dll" } | Select-Object -First 1
    if (-not $adomdDll) { continue }

    Write-Output "[ETL] Found AdomdClient: $($adomdDll.FullName)"

    # Pre-load ALL companion DLLs from same directory before loading AdomdClient
    # Handles both old (Core.dll, Tabular.dll) and new (Runtime.Core.dll, Runtime.Windows.dll) layouts
    foreach ($comp in ($dlls | Where-Object { $_.Name -ne $adomdDll.Name })) {
        try {
            [void][System.Reflection.Assembly]::LoadFrom($comp.FullName)
            Write-Output "[ETL] Companion loaded: $($comp.Name)"
        } catch {
            Write-Output "[ETL] Companion skip ($($comp.Name)): $($_.Exception.Message)"
        }
    }

    try {
        $asm = [System.Reflection.Assembly]::LoadFrom($adomdDll.FullName)
        $adomdAsm = $asm
        Write-Output "[ETL] ADOMD.NET v$($asm.GetName().Version) loaded OK"
        break
    } catch {
        Write-Output "[ETL] LoadFrom failed ($dir): $($_.Exception.Message)"
    }
}

if ($null -eq $adomdAsm) {
    Out-Json @{
        error = "ADOMD.NET not found. On Mac: run setup_mac.sh. On Windows: install Microsoft Analysis Services client tools."
    }
    exit 1
}

$connType = $adomdAsm.GetType("Microsoft.AnalysisServices.AdomdClient.AdomdConnection")
$cmdType  = $adomdAsm.GetType("Microsoft.AnalysisServices.AdomdClient.AdomdCommand")

if (-not $connType -or -not $cmdType) {
    Out-Json @{ error = "Types AdomdConnection/AdomdCommand not found in loaded assembly: $($adomdAsm.FullName)" }
    exit 1
}

try {
    Write-Output "[ETL] Connecting to SSAS..."
    $conn = [Activator]::CreateInstance($connType, @($connStr))
    $conn.Open()
    Write-Output "[ETL] Connected OK."

    $cmd = [Activator]::CreateInstance($cmdType)
    $cmd.Connection     = $conn
    $cmd.CommandText    = $Query
    $cmd.CommandTimeout = 300

    Write-Output "[ETL] Executing DAX query (timeout=300s)..."
    $reader     = $cmd.ExecuteReader()
    $fieldCount = $reader.FieldCount
    $columns    = @()
    for ($i = 0; $i -lt $fieldCount; $i++) { $columns += $reader.GetName($i) }
    Write-Output "[ETL] Columns ($fieldCount): $($columns -join ' | ')"

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
        if ($rowCount % 5000 -eq 0) { Write-Output "[ETL] ...fetched $rowCount rows so far" }
        if ($MaxRows -gt 0 -and $rowCount -ge $MaxRows) { break }
    }
    $reader.Close()
    $conn.Close()
    Write-Output "[ETL] Fetch complete: $rowCount rows"

    Out-Json @{ columns = @($columns); rows = @($rows); count = $rowCount }
    exit 0

} catch {
    Write-Output "[ETL] Error during connection/query:"
    Write-ExceptionDetail "ETL" $_.Exception
    Out-Json @{ error = $_.Exception.Message }
    exit 1
}
