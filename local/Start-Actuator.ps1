$ErrorActionPreference = 'Stop'
$pythonExe = 'D:\wharttest\runtime\actuator-venv\Scripts\python.exe'
$pidPath = 'D:\wharttest\guicase-next\local\actuator-process.json'
if (Test-Path -LiteralPath $pidPath) {
    $record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $record.ProcessId -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $pythonExe -and $existing.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.StartTimeTicks) {
        Write-Output ('UI actuator already running: PID ' + $existing.Id)
        return
    }
}
$config = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env')) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $config[$matches[1]] = $matches[2] }
}
$env:TEMP = 'D:\wharttest\tmp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\wharttest\runtime\playwright-browsers'
$env:WHARTTEST_ACTUATOR_API_USERNAME = $config.DJANGO_ADMIN_USERNAME
$env:WHARTTEST_ACTUATOR_API_PASSWORD = $config.DJANGO_ADMIN_PASSWORD
$env:WHARTTEST_ACTUATOR_ID = 'guicase-windows-actuator'
$env:WHARTTEST_ACTUATOR_NAME = 'SGUI Windows Actuator'
$process = Start-Process -FilePath $pythonExe -ArgumentList @(
    'D:\wharttest\guicase-next\Actuator\main.py', '--no-gui',
    '--config', 'D:\wharttest\guicase-next\local\actuator-windows.toml'
) -WorkingDirectory 'D:\wharttest\guicase-next\Actuator' -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput 'D:\wharttest\logs\guicase-next-actuator.stdout.log' `
    -RedirectStandardError 'D:\wharttest\logs\guicase-next-actuator.stderr.log'
$env:WHARTTEST_ACTUATOR_API_PASSWORD = $null
[pscustomobject]@{ProcessId=$process.Id; StartTimeTicks=$process.StartTime.ToUniversalTime().Ticks.ToString(); Executable=$pythonExe} |
    ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding utf8
Write-Output ('UI actuator started: PID ' + $process.Id)
