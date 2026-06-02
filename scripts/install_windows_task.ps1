param(
    [string]$MainTaskName = "DODF SEMOB Report 05h40",
    [string]$FallbackTaskName = "DODF SEMOB Report 06h40",
    [string]$LegacyTaskName = "DODF SEMOB Report",
    [string]$MainAt = "05:40",
    [string]$MainWaitUntil = "06:10",
    [string]$FallbackAt = "06:40",
    [int]$PollIntervalSeconds = 300
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$RunScript = Join-Path $Root "scripts\run_local_report.ps1"

if (-not (Test-Path $RunScript)) {
    throw "Script de execucao nao encontrado: $RunScript"
}

function New-DodfTask {
    param(
        [string]$TaskName,
        [string]$At,
        [string]$ExtraArgs,
        [string]$Description
    )

    $Time = [datetime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
    $ActionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" $ExtraArgs"
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ActionArgs -WorkingDirectory $Root
    $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description $Description `
        -Force | Out-Null
}

$Legacy = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
if ($Legacy) {
    Unregister-ScheduledTask -TaskName $LegacyTaskName -Confirm:$false
    Write-Host "Tarefa antiga '$LegacyTaskName' removida."
}

New-DodfTask `
    -TaskName $MainTaskName `
    -At $MainAt `
    -ExtraArgs "-RequireToday -WaitUntil `"$MainWaitUntil`" -PollIntervalSeconds $PollIntervalSeconds" `
    -Description "Tenta enviar o relatorio DODF SEMOB entre $MainAt e $MainWaitUntil quando a edicao do dia estiver publicada."

New-DodfTask `
    -TaskName $FallbackTaskName `
    -At $FallbackAt `
    -ExtraArgs "-RequireToday -PollIntervalSeconds $PollIntervalSeconds" `
    -Description "Tentativa reserva do relatorio DODF SEMOB as $FallbackAt, sem duplicar se a edicao do dia ja foi enviada."

Write-Host "Tarefa principal '$MainTaskName' criada para $MainAt, tentando ate $MainWaitUntil."
Write-Host "Tarefa reserva '$FallbackTaskName' criada para $FallbackAt."
Write-Host "Para testar a principal agora: Start-ScheduledTask -TaskName `"$MainTaskName`""
Write-Host "Para testar a reserva agora: Start-ScheduledTask -TaskName `"$FallbackTaskName`""
