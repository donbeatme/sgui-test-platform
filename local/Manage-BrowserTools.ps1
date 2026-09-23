param([ValidateSet('Start','Stop','Status')][string]$Action='Status')
$ErrorActionPreference = 'Stop'
$settings = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env')) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $settings[$matches[1]] = $matches[2] }
}
if ($settings.SGUI_BROWSER_MODE -ne 'windows-edge') { return }
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $PSScriptRoot 'browser-mcp-process.json'
$configPath = Join-Path $PSScriptRoot 'playwright-mcp-windows.local.json'
$outputRoot = Join-Path $projectRoot 'data\playwright-screenshots'
$logRoot = Join-Path $projectRoot 'data\browser-mcp-logs'
$nodeExe = $settings.SGUI_BROWSER_NODE
$cliPath = $settings.SGUI_BROWSER_MCP_CLI
$ownedProcess = $null
if (Test-Path -LiteralPath $recordPath) {
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $candidate = Get-Process -Id $record.ProcessId -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Path -eq $record.Executable -and
        $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.StartTimeTicks) {
        $ownedProcess = $candidate
    }
}
switch ($Action) {
    'Status' {
        if ($ownedProcess) { Write-Output ('Windows Edge MCP running: PID ' + $ownedProcess.Id) }
        else { Write-Output 'Windows Edge MCP stopped.' }
    }
    'Start' {
        if ($ownedProcess) {
            Write-Output ('Windows Edge MCP already running: PID ' + $ownedProcess.Id)
            return
        }
        foreach ($requiredPath in @($nodeExe, $cliPath, $configPath)) {
            if (-not $requiredPath -or -not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
                throw 'Windows browser MCP requires SGUI_BROWSER_NODE, SGUI_BROWSER_MCP_CLI and its local JSON config. See BrowserTools.md.'
            }
        }
        $browserConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $listenPort = [int]$browserConfig.server.port
        if ($browserConfig.server.host -ne '127.0.0.1' -or $listenPort -lt 1) {
            throw 'The Windows browser MCP must listen on 127.0.0.1 with an explicit port.'
        }
        if (Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue) {
            throw "Port $listenPort is already in use by an untracked process; no process was stopped."
        }
        New-Item -ItemType Directory -Path $outputRoot,$logRoot -Force | Out-Null
        $browserTemp = Join-Path $projectRoot 'data\browser-mcp-tmp'
        New-Item -ItemType Directory -Path $browserTemp -Force | Out-Null
        $env:TEMP = $browserTemp
        $env:TMP = $browserTemp
        # Keep explicitly named relative screenshots inside the shared volume too.
        $arguments = '"' + $cliPath + '" --config "' + $configPath + '"'
        $started = Start-Process -FilePath $nodeExe -ArgumentList $arguments -WorkingDirectory $outputRoot `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'stdout.log') `
            -RedirectStandardError (Join-Path $logRoot 'stderr.log')
        $startedAt = $started.StartTime.ToUniversalTime().Ticks.ToString()
        [pscustomobject]@{ProcessId=$started.Id; StartTimeTicks=$startedAt; Executable=$nodeExe} |
            ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
        $ready = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            $started.Refresh()
            if ($started.HasExited) { throw "Browser MCP exited. See $logRoot\stderr.log" }
            if (Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue |
                Where-Object OwningProcess -eq $started.Id) { $ready = $true; break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw "Browser MCP did not start listening; see $logRoot\stderr.log" }
        Write-Output ('Windows Edge MCP ready: PID ' + $started.Id)
    }
    'Stop' {
        if (-not $ownedProcess) { return }
        # Stop only this verified service and its own browser children.
        $snapshot = @(Get-CimInstance Win32_Process)
        $owned = @($ownedProcess.Id)
        for ($index = 0; $index -lt $owned.Count; $index++) {
            $owned += @($snapshot | Where-Object { $_.ParentProcessId -eq $owned[$index] -and $_.ProcessId -notin $owned } |
                Select-Object -ExpandProperty ProcessId)
        }
        for ($index = $owned.Count - 1; $index -ge 0; $index--) {
            $item = Get-Process -Id $owned[$index] -ErrorAction SilentlyContinue
            $expected = $snapshot | Where-Object ProcessId -eq $owned[$index]
            if ($item -and $expected -and
                [Math]::Abs(($item.StartTime.ToUniversalTime() - $expected.CreationDate.ToUniversalTime()).TotalMilliseconds) -lt 1) {
                Stop-Process -Id $item.Id -ErrorAction SilentlyContinue
            }
        }
        Write-Output 'Windows Edge MCP stopped.'
    }
}
