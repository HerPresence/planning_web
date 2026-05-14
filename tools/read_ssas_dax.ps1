# read_ssas_dax.ps1
# Approach 1: ADODB COM  (msolap140.dll via COM, no managed DLL needed)
# Approach 2: LoadFrom   (ADOMD.NET managed assembly via reflection)
# NOTE: ALL diagnostic lines use Write-Output (not Write-Host) so they are
#       captured by Python subprocess. Write-Host goes to stream-6 (Information)
#       which subprocess does NOT capture. Write-Output goes to stdout.
param(
    [Parameter(Mandatory=$true)]  [string]$Server,
    [Parameter(Mandatory=$true)]  [string]$Database,
    [Parameter(Mandatory=$true)]  [string]$Query,
    [Parameter(Mandatory=$false)] [string]$Login    = "",
    [Parameter(Mandatory=$false)] [string]$Password = "",
    [Parameter(Mandatory=$false)] [int]   $MaxRows  = 0
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8

Write-Output "[DEBUG] NEW SCRIPT VERSION LOADED"
Write-Output "[DEBUG] Script: $($MyInvocation.MyCommand.Path)"
Write-Output "[DEBUG] PSVersion: $($PSVersionTable.PSVersion)"
Write-Output "[DEBUG] CLR: $([System.Environment]::Version)"

function Out-Json($obj) {
    Write-Output ($obj | ConvertTo-Json -Depth 10 -Compress)
}

function Write-Diag($msg) {
    Write-Output $msg
}

function Write-ExceptionDetail {
    param([string]$tag, $ex, $stack)
    Write-Output "[$tag] Exception.Message:          $($ex.Message)"
    Write-Output "[$tag] Exception.GetType().FullName: $($ex.GetType().FullName)"
    try { Write-Output "[$tag] Exception.HResult:          0x$('{0:X8}' -f [uint32]$ex.HResult)" } catch {}
    if ($ex.InnerException) {
        Write-Output "[$tag] InnerException.Message:      $($ex.InnerException.Message)"
        Write-Output "[$tag] InnerException.Type:         $($ex.InnerException.GetType().FullName)"
        try { Write-Output "[$tag] InnerException.HResult:     0x$('{0:X8}' -f [uint32]$ex.InnerException.HResult)" } catch {}
    }
    if ($stack) { Write-Output "[$tag] ScriptStackTrace: $stack" }
}

# ---- connection string --------------------------------------------------
if ($Login -and $Password) {
    $connStr = "Provider=MSOLAP;Data Source=$Server;Initial Catalog=$Database;User ID=$Login;Password=$Password;Connect Timeout=15;"
} else {
    $connStr = "Provider=MSOLAP;Data Source=$Server;Initial Catalog=$Database;Integrated Security=SSPI;Connect Timeout=15;"
}
$connStrLog = $connStr -replace "Password=[^;]+", "Password=***"
Write-Output "[SSAS] Connection (no password): $connStrLog"

# =========================================================================
# Approach 1: ADODB COM
# =========================================================================
Write-Output "[TRY1] ---- ADODB COM approach start ----"

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
            if ($null -eq $v -or $v -is [System.DBNull]) { $row[$col] = "" } else { $row[$col] = "$v" }
        }
        $rows.Add($row)
        $count++
        if ($maxRows -gt 0 -and $count -ge $maxRows) { break }
        $rs.MoveNext()
    }
    return @{ columns = @($columns); rows = @($rows); count = $count }
}

$adoResult = $null

$serverCandidates = [System.Collections.Generic.List[string]]::new()
$serverCandidates.Add($Server)
if ($Server -ne "localhost") { $serverCandidates.Add("localhost") }
if ($Server -ne ".")         { $serverCandidates.Add(".")         }

Write-Output "[TRY1] Will try server candidates: $($serverCandidates -join ', ')"

foreach ($srv in $serverCandidates) {
    Write-Output "[TRY1][OPEN] start -- Data Source=$srv"

    if ($Login -and $Password) {
        $tryConn = "Provider=MSOLAP;Data Source=$srv;Initial Catalog=$Database;User ID=$Login;Password=$Password;Connect Timeout=15;"
    } else {
        $tryConn = "Provider=MSOLAP;Data Source=$srv;Initial Catalog=$Database;Integrated Security=SSPI;Connect Timeout=15;"
    }
    $tryConnLog = $tryConn -replace "Password=[^;]+", "Password=***"
    Write-Output "[TRY1][OPEN] ConnStr: $tryConnLog"

    $adoConn = $null
    try {
        $adoConn = New-Object -ComObject ADODB.Connection
        Write-Output "[TRY1][NEW] OK -- ADODB.Connection object created."
    } catch {
        Write-Output "[TRY1][NEW] FAILED -- cannot create ADODB.Connection."
        Write-ExceptionDetail "TRY1][NEW" $_.Exception $_.ScriptStackTrace
        break
    }

    try {
        $adoConn.ConnectionTimeout = 15
        $adoConn.CommandTimeout    = 30
        $adoConn.Open($tryConn)
        Write-Output "[TRY1][OPEN] OK -- connection opened with Data Source=$srv"
    } catch {
        Write-Output "[TRY1][OPEN] FAILED with Data Source=$srv"
        Write-ExceptionDetail "TRY1][OPEN" $_.Exception $_.ScriptStackTrace
        try { $adoConn.Close() } catch {}
        $adoConn = $null
        Write-Output "[TRY1][OPEN] Trying next server candidate..."
        continue
    }

    Write-Output "[TRY1][EXECUTE] start -- executing DAX query..."
    try {
        $adoCmd = New-Object -ComObject ADODB.Command
        $adoCmd.ActiveConnection = $adoConn
        $adoCmd.CommandText      = $Query
        $adoCmd.CommandTimeout   = 30

        $adoRs = $adoCmd.Execute()
        Write-Output "[TRY1][EXECUTE] OK -- query returned recordset."

        $adoResult = Read-AdoRecordset $adoRs $MaxRows
        try { $adoRs.Close()   } catch {}
        try { $adoConn.Close() } catch {}

        Write-Output "[TRY1-OK] ADODB COM success: $($adoResult.count) rows, columns: $($adoResult.columns -join ', ')"
        break
    } catch {
        Write-Output "[TRY1][EXECUTE] FAILED."
        Write-ExceptionDetail "TRY1][EXECUTE" $_.Exception $_.ScriptStackTrace
        try { $adoConn.Close() } catch {}
        $adoResult = $null
        break
    }
}

if ($null -ne $adoResult) {
    Out-Json $adoResult
    exit 0
}

Write-Output "[TRY1] ---- ADODB COM approach FAILED, trying approach 2 ----"

# =========================================================================
# Approach 2: ADOMD.NET managed assembly via LoadFrom
# =========================================================================
Write-Output "[TRY2] ---- ADOMD.NET LoadFrom approach start ----"

$searchDirs = @(
    "C:\Program Files\Microsoft Analysis Services\AS OLEDB\160",
    "C:\Program Files\Microsoft Analysis Services\AS OLEDB\150",
    "C:\Program Files\Microsoft Analysis Services\AS OLEDB\140",
    "C:\Program Files\Microsoft Analysis Services\AS OLEDB\110",
    "C:\Program Files\Microsoft.NET\ADOMD.NET\160",
    "C:\Program Files\Microsoft.NET\ADOMD.NET\150",
    "C:\Program Files\Microsoft.NET\ADOMD.NET\140",
    "$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\160",
    "$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\150",
    "$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\140",
    "$Env:ProgramW6432\Microsoft Analysis Services\AS OLEDB\110",
    "$Env:ProgramFiles\Microsoft Analysis Services\AS OLEDB\160",
    "$Env:ProgramFiles\Microsoft Analysis Services\AS OLEDB\150",
    "$Env:ProgramFiles\Microsoft Analysis Services\AS OLEDB\140",
    "$Env:ProgramFiles\Microsoft Analysis Services\AS OLEDB\110"
)

$adomdAsm   = $null
$try2Errors = [System.Collections.Generic.List[string]]::new()

foreach ($dir in ($searchDirs | Select-Object -Unique)) {
    if (-not (Test-Path $dir -PathType Container)) { continue }
    Write-Output "[TRY2] Directory exists: $dir"

    $allDlls = Get-ChildItem -Path $dir -Filter "*.dll" -ErrorAction SilentlyContinue
    if (-not $allDlls) { Write-Output "[TRY2] No DLLs here."; continue }
    Write-Output "[TRY2] DLLs: $($allDlls.Name -join ', ')"

    $adomdDll = $allDlls | Where-Object { $_.Name -eq "Microsoft.AnalysisServices.AdomdClient.dll" } | Select-Object -First 1
    if (-not $adomdDll) {
        $adomdDll = $allDlls | Where-Object { $_.Name -like "*AdomdClient*.dll" } | Select-Object -First 1
    }
    if (-not $adomdDll) { Write-Output "[TRY2] AdomdClient.dll not in this directory."; continue }

    foreach ($compName in @("Microsoft.AnalysisServices.Core.dll", "Microsoft.AnalysisServices.Tabular.dll", "Microsoft.AnalysisServices.dll")) {
        $comp = $allDlls | Where-Object { $_.Name -eq $compName } | Select-Object -First 1
        if (-not $comp) { continue }
        try {
            $ca = [System.Reflection.Assembly]::LoadFrom($comp.FullName)
            Write-Output "[TRY2] Companion loaded: $($comp.Name) v$($ca.GetName().Version)"
        } catch {
            Write-Output "[TRY2] Companion skip ($($comp.Name)): $($_.Exception.Message)"
        }
    }

    Write-Output "[TRY2] LoadFrom: $($adomdDll.FullName)"
    try {
        $asm      = [System.Reflection.Assembly]::LoadFrom($adomdDll.FullName)
        $adomdAsm = $asm
        Write-Output "[TRY2-OK] Loaded: $($asm.FullName)"
        Write-Output "[TRY2-OK] Version:  $($asm.GetName().Version)"
        Write-Output "[TRY2-OK] Location: $($asm.Location)"
        break
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match "already exists" -or $msg -match "already been added") {
            $adomdAsm = [System.AppDomain]::CurrentDomain.GetAssemblies() |
                Where-Object { $_.GetName().Name -eq "Microsoft.AnalysisServices.AdomdClient" } |
                Select-Object -First 1
            if ($adomdAsm) { Write-Output "[TRY2-OK] Already in AppDomain: $($adomdAsm.GetName().Version)"; break }
        }
        $loaderDetail = ""
        if ($_.Exception -is [System.Reflection.ReflectionTypeLoadException]) {
            $loaderDetail = " | LoaderExceptions: " + (($_.Exception.LoaderExceptions | ForEach-Object { $_.Message }) -join " ; ")
        }
        Write-Output "[TRY2-FAIL] $msg$loaderDetail"
        $try2Errors.Add("[$($adomdDll.FullName)]: $msg$loaderDetail")
    }
}

if ($null -ne $adomdAsm) {
    $connType = $adomdAsm.GetType("Microsoft.AnalysisServices.AdomdClient.AdomdConnection")
    $cmdType  = $adomdAsm.GetType("Microsoft.AnalysisServices.AdomdClient.AdomdCommand")

    if (-not $connType -or -not $cmdType) {
        Out-Json @{ error = "Types AdomdConnection/AdomdCommand not found in $($adomdAsm.FullName)." }
        exit 1
    }

    try {
        $conn2 = [Activator]::CreateInstance($connType, @($connStr))
        $conn2.Open()
        Write-Output "[TRY2] ADOMD connection opened."

        $cmd2 = [Activator]::CreateInstance($cmdType)
        $cmd2.Connection     = $conn2
        $cmd2.CommandText    = $Query
        $cmd2.CommandTimeout = 120

        $reader     = $cmd2.ExecuteReader()
        $fieldCount = $reader.FieldCount
        $columns    = @()
        for ($i = 0; $i -lt $fieldCount; $i++) { $columns += $reader.GetName($i) }
        Write-Output "[TRY2] Columns ($fieldCount): $($columns -join ', ')"

        $rows     = [System.Collections.Generic.List[object]]::new()
        $rowCount = 0
        while ($reader.Read()) {
            $row = [ordered]@{}
            for ($i = 0; $i -lt $fieldCount; $i++) {
                $v = $reader.GetValue($i)
                $row[$columns[$i]] = if ($null -eq $v -or $v -is [System.DBNull]) { "" } else { "$v" }
            }
            $rows.Add($row)
            $rowCount++
            if ($MaxRows -gt 0 -and $rowCount -ge $MaxRows) { break }
        }
        $reader.Close()
        $conn2.Close()
        Write-Output "[TRY2-OK] ADOMD.NET: $rowCount rows"

        Out-Json @{ columns = @($columns); rows = @($rows); count = $rowCount }
        exit 0
    } catch {
        $msg = $_.Exception.Message
        Write-Output "[TRY2-FAIL] Connection/query error: $msg"
        Write-ExceptionDetail "TRY2][FAIL" $_.Exception $_.ScriptStackTrace
        $try2Errors.Add("[ADOMD-QUERY]: $msg")
    }
}

# ---- all approaches failed ----------------------------------------------
Write-Output "[FINAL] All approaches failed."
$allErrors = @("[TRY1] ADODB COM failed.") + @($try2Errors)
Out-Json @{
    error       = "All SSAS connection approaches failed. " + ($allErrors -join " | ")
    searched    = $searchDirs
    load_errors = @($try2Errors)
}
exit 1
