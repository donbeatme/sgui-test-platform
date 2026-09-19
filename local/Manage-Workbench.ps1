param([ValidateSet('Start','Stop','Status','Build')][string]$Action='Status')
$ErrorActionPreference='Stop'
$dockerExe='D:\wharttest\runtime\docker-desktop\resources\bin\docker.exe'
$taskRoot='D:\wharttest\guicase-next'
$env:TEMP='D:\wharttest\tmp'
$env:TMP=$env:TEMP
$env:npm_config_cache='D:\wharttest\cache\npm'
$env:COMPOSE_PARALLEL_LIMIT='2'
$composeArgs=@('compose','--env-file',"$taskRoot\local\.env",'-p','guicase-next','-f',"$taskRoot\local\compose.yml")
function Invoke-WorkbenchCompose {
    param([string[]]$Arguments)
    & $dockerExe @composeArgs @Arguments
    if($LASTEXITCODE -ne 0){throw 'Docker Compose failed. Review the output above.'}
}
switch($Action){
    'Build' {
        Push-Location "$taskRoot\Vue"
        try { & D:\nodejs\npm.cmd run build; if($LASTEXITCODE -ne 0){throw 'Frontend build failed.'} }
        finally { Pop-Location }
    }
    'Start' {
        & D:\wharttest\deployment\Start-Docker.ps1
        # Run one WHartTest stack at a time on this 16 GB computer.
        $oldRunning=@(& $dockerExe ps --format '{{.Names}}' --filter 'name=wharttest-')
        if($oldRunning.Count -gt 0){& D:\wharttest\deployment\Manage-WHartTest.ps1 -Action Stop}
        if(-not(Test-Path -LiteralPath "$taskRoot\Vue\dist\index.html")){throw 'Build the frontend first: Manage-Workbench.ps1 -Action Build'}
        Invoke-WorkbenchCompose -Arguments @('up','-d','--wait','--wait-timeout','300')
        & "$PSScriptRoot\Start-Actuator.ps1"
        Write-Output 'SGUI is ready: http://127.0.0.1:8778/workbench . Use your existing platform account.'
    }
    'Stop' {
        & "$PSScriptRoot\Stop-Actuator.ps1"
        Invoke-WorkbenchCompose -Arguments @('stop')
    }
    'Status' { Invoke-WorkbenchCompose -Arguments @('ps','-a') }
}
