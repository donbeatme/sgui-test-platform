$ErrorActionPreference = 'Stop'
$pidPath = 'D:\wharttest\guicase-next\local\actuator-process.json'
if (-not (Test-Path -LiteralPath $pidPath)) { return }
$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$process = Get-Process -Id $record.ProcessId -ErrorAction SilentlyContinue
if (-not $process) { return }
if ($process.Path -ne 'D:\wharttest\runtime\actuator-venv\Scripts\python.exe' -or
    $process.StartTime.ToUniversalTime().Ticks.ToString() -ne $record.StartTimeTicks) {
    throw 'The recorded PID now belongs to another process; it was left running.'
}
# The Windows venv launcher owns a child Python process. Stop only this verified
# launcher's descendants so the real actuator and its browsers also exit.
$owned = @($process.Id)
$snapshot = @(Get-CimInstance Win32_Process)
for ($index = 0; $index -lt $owned.Count; $index++) {
    $owned += @($snapshot | Where-Object { $_.ParentProcessId -eq $owned[$index] -and $_.ProcessId -notin $owned } | Select-Object -ExpandProperty ProcessId)
}
for ($index = $owned.Count - 1; $index -ge 0; $index--) {
    $item = Get-Process -Id $owned[$index] -ErrorAction SilentlyContinue
    $expected = $snapshot | Where-Object { $_.ProcessId -eq $owned[$index] }
    if ($item -and $expected -and [Math]::Abs(($item.StartTime.ToUniversalTime() - $expected.CreationDate.ToUniversalTime()).TotalMilliseconds) -lt 1) {
        Stop-Process -Id $item.Id -ErrorAction SilentlyContinue
    }
}
Write-Output 'Local UI actuator stopped.'
