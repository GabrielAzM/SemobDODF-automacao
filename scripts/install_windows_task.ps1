param(
    [string]$TaskName = "DODF SEMOB Report",
    [string]$At = "06:30"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$RunScript = Join-Path $Root "scripts\run_local_report.ps1"

if (-not (Test-Path $RunScript)) {
    throw "Script de execucao nao encontrado: $RunScript"
}

$Time = [datetime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$ActionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ActionArgs -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Envia o relatorio diario do DODF SEMOB por email." `
    -Force | Out-Null

Write-Host "Tarefa '$TaskName' criada para rodar todo dia as $At."
Write-Host "Para testar agora, rode: Start-ScheduledTask -TaskName `"$TaskName`""
