param(
    [string]$CredentialsPath = "",
    [string]$MailFrom = "gabrielzvd616@gmail.com",
    [string]$MailTo = "thaysdiasr@gmail.com",
    [switch]$SkipOAuth
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
$CredentialsDest = Join-Path $Root "credentials.json"
$EnvPath = Join-Path $Root ".env"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Criando ambiente virtual em venv..."
    python -m venv (Join-Path $Root "venv")
}

if (-not (Test-Path $VenvPython)) {
    throw "Python do venv nao encontrado em $VenvPython"
}

Write-Host "Instalando dependencias..."
& $VenvPython -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao instalar dependencias."
}

if ($CredentialsPath) {
    Copy-Item -LiteralPath (Resolve-Path $CredentialsPath) -Destination $CredentialsDest -Force
} elseif (-not (Test-Path $CredentialsDest)) {
    $Candidate = Get-ChildItem -Path $Root -Filter "client_secret_*.json" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $Candidate) {
        throw "Nao achei credentials.json nem client_secret_*.json na pasta do projeto."
    }

    Copy-Item -LiteralPath $Candidate.FullName -Destination $CredentialsDest -Force
}

$Lines = @()
if (Test-Path $EnvPath) {
    $Lines = @(Get-Content -Path $EnvPath)
}

$Updates = [ordered]@{
    "EMAIL_DELIVERY" = "gmail_api"
    "MAIL_FROM" = $MailFrom
    "MAIL_TO" = $MailTo
    "GMAIL_CREDENTIALS_FILE" = "credentials.json"
    "GMAIL_TOKEN_FILE" = "token.json"
    "ATTACH_PDF" = "true"
    "SEND_EMPTY_REPORT" = "true"
    "DODF_BASE_URL" = "https://dodf.df.gov.br"
    "TIMEZONE" = "America/Sao_Paulo"
}

foreach ($Key in $Updates.Keys) {
    $Found = $false
    for ($Index = 0; $Index -lt $Lines.Count; $Index += 1) {
        if ($Lines[$Index] -match "^\s*$([regex]::Escape($Key))=") {
            $Lines[$Index] = "$Key=$($Updates[$Key])"
            $Found = $true
            break
        }
    }

    if (-not $Found) {
        $Lines += "$Key=$($Updates[$Key])"
    }
}

Set-Content -Path $EnvPath -Value $Lines -Encoding UTF8

Write-Host ".env configurado para envio local via Gmail API."
Write-Host "Credenciais OAuth em credentials.json."

if ($SkipOAuth) {
    Write-Host "OAuth nao executado por causa de -SkipOAuth."
    exit 0
}

Write-Host "Abrindo autorizacao do Gmail API. Faca login na conta remetente e permita o envio."
Push-Location $Root
try {
    & $VenvPython dodf_semob_report.py --init-gmail-api
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao inicializar Gmail API."
    }
} finally {
    Pop-Location
}

Write-Host "Pronto. Agora teste com: .\scripts\run_local_report.ps1"
