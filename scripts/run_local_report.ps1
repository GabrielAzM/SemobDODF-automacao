param(
    [switch]$RequireToday,
    [string]$WaitUntil = "",
    [int]$PollIntervalSeconds = 300,
    [switch]$ForceSend,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root "venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "dodf_semob_$Stamp.log"

if (-not (Test-Path $Python)) {
    throw "Python do venv nao encontrado. Rode primeiro: .\scripts\setup_local_gmail_api.ps1"
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Push-Location $Root
try {
    Write-Host "Rodando relatorio DODF SEMOB..."
    Write-Host "Log: $LogFile"
    $PythonArgs = @("dodf_semob_report.py")
    if ($RequireToday) {
        $PythonArgs += "--require-today"
    }
    if ($WaitUntil) {
        $PythonArgs += "--wait-until"
        $PythonArgs += $WaitUntil
    }
    if ($PollIntervalSeconds) {
        $PythonArgs += "--poll-interval-seconds"
        $PythonArgs += [string]$PollIntervalSeconds
    }
    if ($ForceSend) {
        $PythonArgs += "--force-send"
    }
    if ($DryRun) {
        $PythonArgs += "--dry-run"
    }

    & $Python @PythonArgs *>&1 | Tee-Object -FilePath $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "O relatorio falhou. Veja o log: $LogFile"
    }
} finally {
    Pop-Location
}

Write-Host "Relatorio concluido."
