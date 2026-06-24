# QuantDinger interactive installer for Windows PowerShell.
#
# Usage:
#   irm https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.ps1 | iex
#
# Optional environment overrides:
#   $env:QUANTDINGER_INSTALL_REF = "main"
#   $env:QUANTDINGER_INSTALL_DIR = "C:\QuantDinger"

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$InstallDir = if ($env:QUANTDINGER_INSTALL_DIR) { $env:QUANTDINGER_INSTALL_DIR } else { Join-Path $HOME "quantdinger" }
$InstallRef = if ($env:QUANTDINGER_INSTALL_REF) { $env:QUANTDINGER_INSTALL_REF } else { "main" }
$GithubRaw = "https://raw.githubusercontent.com/brokermr810/QuantDinger/$InstallRef"
$ComposeFile = "docker-compose.yml"
$BackendEnv = "backend.env"
$RootEnv = ".env"

function Fail($Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

function Get-EnvValue($Path, $Key) {
    if (-not (Test-Path $Path)) { return "" }
    $line = Get-Content $Path | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -Last 1
    if (-not $line) { return "" }
    return $line.Substring($Key.Length + 1)
}

function Set-EnvValue($Path, $Key, $Value) {
    if (-not (Test-Path $Path)) { New-Item -ItemType File -Path $Path | Out-Null }
    $lines = @(Get-Content $Path)
    $found = $false
    $next = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            "$Key=$Value"
            $found = $true
        } else {
            $line
        }
    }
    if (-not $found) { $next += "$Key=$Value" }
    Set-Content -Path $Path -Value $next -Encoding UTF8
}

function New-HexSecret([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buffer)
    } finally {
        $rng.Dispose()
    }
    return (($buffer | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Read-Value($Prompt, $Default = "") {
    if ($Default) {
        $value = Read-Host "$Prompt [$Default]"
        if (-not $value) { return $Default }
        return $value
    }
    return (Read-Host $Prompt)
}

function Read-SecretPlain($Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Check-Prerequisites {
    Write-Host "QuantDinger installer" -ForegroundColor Blue
    Write-Host "Install directory: $InstallDir"
    Write-Host "Source ref: $InstallRef"
    Write-Host ""

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "Docker is required. Install Docker Desktop first."
    }

    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker is installed but the Docker daemon is not running."
    }

    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose v2 is required."
    }
}

function Prepare-Directory {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Set-Location $InstallDir
}

function Download-Files {
    Write-Host "Downloading compose and backend environment template..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "$GithubRaw/docker-compose.ghcr.yml" -OutFile $ComposeFile
    if (-not (Test-Path $BackendEnv)) {
        Invoke-WebRequest -Uri "$GithubRaw/backend_api_python/env.example" -OutFile $BackendEnv
    }
    if (-not (Test-Path $RootEnv)) {
        New-Item -ItemType File -Path $RootEnv | Out-Null
    }
}

function Collect-Settings {
    $script:AdminUser = Read-Value "Admin username" ((Get-EnvValue $BackendEnv "ADMIN_USER") -replace '^$', 'quantdinger')
    $script:AdminEmail = Read-Value "Admin email (optional)" (Get-EnvValue $BackendEnv "ADMIN_EMAIL")

    $existingPassword = Get-EnvValue $BackendEnv "ADMIN_PASSWORD"
    if ($existingPassword -and $existingPassword -ne "123456") {
        $entered = Read-SecretPlain "Admin password (leave blank to keep existing)"
        if ($entered) { $script:AdminPassword = $entered } else { $script:AdminPassword = $existingPassword }
    } else {
        while ($true) {
            $pass1 = Read-SecretPlain "Admin password"
            $pass2 = Read-SecretPlain "Confirm admin password"
            if (-not $pass1) {
                Write-Host "Admin password cannot be empty." -ForegroundColor Red
                continue
            }
            if ($pass1 -eq "123456") {
                Write-Host "Do not use the built-in default password 123456." -ForegroundColor Red
                continue
            }
            if ($pass1 -ne $pass2) {
                Write-Host "Passwords do not match." -ForegroundColor Red
                continue
            }
            $script:AdminPassword = $pass1
            break
        }
    }

    $script:FrontendPort = Read-Value "Frontend port" ((Get-EnvValue $RootEnv "FRONTEND_PORT") -replace '^$', '8888')
    $script:BackendPort = Read-Value "Backend bind address" ((Get-EnvValue $RootEnv "BACKEND_PORT") -replace '^$', '127.0.0.1:5000')

    $existingPgPassword = Get-EnvValue $RootEnv "POSTGRES_PASSWORD"
    if ($existingPgPassword) { $script:PostgresPassword = $existingPgPassword } else { $script:PostgresPassword = New-HexSecret 18 }

    Write-Host ""
    Write-Host "Image source:"
    Write-Host "  1) global/default"
    Write-Host "  2) mainland China mirror (docker.m.daocloud.io/library/)"
    $choice = Read-Value "Select image source" "1"
    $existingImagePrefix = Get-EnvValue $RootEnv "IMAGE_PREFIX"
    if ($existingImagePrefix) {
        $script:ImagePrefix = $existingImagePrefix
    } elseif ($choice -eq "2") {
        $script:ImagePrefix = "docker.m.daocloud.io/library/"
    } else {
        $script:ImagePrefix = ""
    }

    $existingSecret = Get-EnvValue $BackendEnv "SECRET_KEY"
    if ($existingSecret -and $existingSecret -ne "quantdinger-secret-key-change-me") {
        $script:SecretKey = $existingSecret
    } else {
        $script:SecretKey = New-HexSecret 32
    }
}

function Write-Settings {
    Set-EnvValue $BackendEnv "SECRET_KEY" $SecretKey
    Set-EnvValue $BackendEnv "ADMIN_USER" $AdminUser
    Set-EnvValue $BackendEnv "ADMIN_PASSWORD" $AdminPassword
    Set-EnvValue $BackendEnv "ADMIN_EMAIL" $AdminEmail
    Set-EnvValue $BackendEnv "FRONTEND_URL" "http://localhost:$FrontendPort"

    Set-EnvValue $RootEnv "FRONTEND_PORT" $FrontendPort
    Set-EnvValue $RootEnv "BACKEND_PORT" $BackendPort
    Set-EnvValue $RootEnv "POSTGRES_PASSWORD" $PostgresPassword
    Set-EnvValue $RootEnv "IMAGE_PREFIX" $ImagePrefix
}

function Start-Stack {
    Write-Host "Pulling images..." -ForegroundColor Yellow
    docker compose -f $ComposeFile pull
    Write-Host "Starting services..." -ForegroundColor Yellow
    docker compose -f $ComposeFile up -d
}

function Wait-ForBackend {
    Write-Host "Waiting for backend health check..." -ForegroundColor Yellow
    $apiPort = ($BackendPort -split ':')[-1]
    $url = "http://127.0.0.1:$apiPort/api/health"
    for ($i = 1; $i -le 45; $i++) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null
            Write-Host "Backend is ready." -ForegroundColor Green
            return
        } catch {
            Write-Host "  waiting... ($i/45)"
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "Backend is still starting. Check logs with:" -ForegroundColor Yellow
    Write-Host "  cd $InstallDir"
    Write-Host "  docker compose -f $ComposeFile logs -f backend"
}

function Print-Summary {
    $apiPort = ($BackendPort -split ':')[-1]
    Write-Host ""
    Write-Host "QuantDinger is ready." -ForegroundColor Green
    Write-Host ""
    Write-Host "Web UI:      http://localhost:$FrontendPort"
    Write-Host "API:         http://127.0.0.1:$apiPort"
    Write-Host "Directory:   $InstallDir"
    Write-Host "Username:    $AdminUser"
    Write-Host "Password:    the password you entered during installation"
    Write-Host ""
    Write-Host "Useful commands:"
    Write-Host "  cd $InstallDir"
    Write-Host "  docker compose -f $ComposeFile ps"
    Write-Host "  docker compose -f $ComposeFile logs -f backend"
    Write-Host "  docker compose -f $ComposeFile pull; docker compose -f $ComposeFile up -d"
    Write-Host ""
    Write-Host "Trading involves substantial risk. Start with paper trading and small test accounts." -ForegroundColor Yellow
}

Check-Prerequisites
Prepare-Directory
Download-Files
Collect-Settings
Write-Settings
Start-Stack
Wait-ForBackend
Print-Summary
