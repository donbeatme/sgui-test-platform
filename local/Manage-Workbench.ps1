param([ValidateSet('Start','Stop','Status','Build')][string]$Action='Status')
$ErrorActionPreference='Stop'
$dockerExe=(Get-Command docker -ErrorAction Stop).Source
$taskRoot=Split-Path -Parent $PSScriptRoot
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
        try { & npm.cmd run build; if($LASTEXITCODE -ne 0){throw 'Frontend build failed.'} }
        finally { Pop-Location }
    }
    'Start' {
        & $dockerExe info --format '{{.ServerVersion}}'
        if($LASTEXITCODE -ne 0){throw 'Start Docker with Linux containers first.'}
        if(-not(Test-Path -LiteralPath "$taskRoot\Vue\dist\index.html")){throw 'Build the frontend first: Manage-Workbench.ps1 -Action Build'}
        foreach($volume in @('guicase-next_postgres-data','guicase-next_qdrant-data')) {
            & $dockerExe volume create $volume
            if($LASTEXITCODE -ne 0){throw "Unable to prepare Docker volume: $volume"}
        }
        Invoke-WorkbenchCompose -Arguments @('up','-d','--wait','--wait-timeout','600')
        & "$PSScriptRoot\Manage-BrowserTools.ps1" -Action Start
        Write-Output 'SGUI is ready: http://127.0.0.1:8778/workbench . Use your existing platform account.'
        Write-Output 'Start the optional fixed-step UI actuator separately; see readme.md.'
    }
    'Stop' {
        & "$PSScriptRoot\Manage-BrowserTools.ps1" -Action Stop
        Invoke-WorkbenchCompose -Arguments @('stop')
    }
    'Status' {
        Invoke-WorkbenchCompose -Arguments @('ps','-a')
        & "$PSScriptRoot\Manage-BrowserTools.ps1" -Action Status
    }
}
