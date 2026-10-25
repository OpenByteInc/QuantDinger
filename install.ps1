# QuantDinger interactive installer for Windows PowerShell.
#
# Usage:
#   irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
#
# Optional environment overrides:
#   $env:QUANTDINGER_INSTALL_REF = "main"
#   $env:QUANTDINGER_INSTALL_DIR = "C:\QuantDinger"

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$InstallDir = if ($env:QUANTDINGER_INSTALL_DIR) { $env:QUANTDINGER_INSTALL_DIR } else { Join-Path $HOME "quantdinger" }
$InstallRef = if ($env:QUANTDINGER_INSTALL_REF) { $env:QUANTDINGER_INSTALL_REF } else { "main" }
$GithubRaw = "https://raw.githubusercontent.com/OpenByteInc/QuantDinger/$InstallRef"
$ComposeFile = "docker-compose.yml"
$EnvFile = ".env"
$EnvTemplate = ".env.example"
$LegacyEnv = "backend.env"
$script:EnvExistedBeforeInstall = $false

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
    [string[]]$lines = @(Get-Content -LiteralPath $Path)
    $found = $false
    [string[]]$next = @(
        foreach ($line in $lines) {
            if ($line -match "^$([regex]::Escape($Key))=") {
                "$Key=$Value"
                $found = $true
            } else {
                $line
            }
        }
    )
    if (-not $found) { $next += "$Key=$Value" }
    Set-Content -LiteralPath $Path -Value $next -Encoding UTF8
}

function Repair-EnvLayout($Path, [string[]]$KnownKeys) {
    if (-not (Test-Path $Path)) { return }
    $content = Get-Content -LiteralPath $Path -Raw
    if (-not $content -or -not $KnownKeys) { return }
    $keyPattern = (($KnownKeys | ForEach-Object { [regex]::Escape($_) }) -join "|")
    $pattern = "(?<=[^`r`n])(?=(?:$keyPattern)=)"
    $repaired = [regex]::Replace($content, $pattern, [Environment]::NewLine)
    if ($repaired -ne $content) {
        Set-Content -LiteralPath $Path -Value $repaired.TrimEnd("`r", "`n") -Encoding UTF8
    }
}

function Get-PostgresPasswordFromUrl($DatabaseUrl) {
    if (-not $DatabaseUrl) { return "" }
    try {
        $uri = [Uri]$DatabaseUrl
        if ($uri.Scheme -notin @("postgres", "postgresql")) { return "" }
        $separator = $uri.UserInfo.IndexOf(":")
        if ($separator -lt 0) { return "" }
        return [Uri]::UnescapeDataString($uri.UserInfo.Substring($separator + 1))
    } catch {
        return ""
    }
}

function Get-PostgresPasswordFromContainer {
    try {
        $raw = docker inspect --format '{{json .Config.Env}}' quantdinger-db 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return "" }
        foreach ($entry in ($raw | ConvertFrom-Json)) {
            if ($entry -like "POSTGRES_PASSWORD=*") {
                return $entry.Substring("POSTGRES_PASSWORD=".Length)
            }
        }
    } catch {
        return ""
    }
    return ""
}

function Test-ExistingPostgresVolume {
    $projectName = if ($env:COMPOSE_PROJECT_NAME) {
        $env:COMPOSE_PROJECT_NAME
    } else {
        ((Split-Path -Leaf $InstallDir).ToLowerInvariant() -replace '[^a-z0-9_-]', '').TrimStart([char[]]"_-")
    }
    if (-not $projectName) { $projectName = "quantdinger" }
    try {
        $volume = docker volume ls --quiet `
            --filter "label=com.docker.compose.project=$projectName" `
            --filter "label=com.docker.compose.volume=postgres_data" 2>$null
        return [bool]$volume
    } catch {
        return $false
    }
}

function Sync-MissingEnvKeys($Target, $Template, $Legacy = "") {
    if (-not (Test-Path -LiteralPath $Template)) {
        Fail "Environment template not found: $Template"
    }
    if (-not (Test-Path -LiteralPath $Target)) {
        if ($Legacy -and (Test-Path -LiteralPath $Legacy)) {
            Copy-Item -LiteralPath $Legacy -Destination $Target
            Write-Host "Created $Target from legacy $Legacy."
            $Legacy = ""
        } else {
            Copy-Item -LiteralPath $Template -Destination $Target
            Write-Host "Created $Target from $Template."
        }
    }
    $assignment = '^\s*(?:export\s+)?(?<key>[A-Za-z_][A-Za-z0-9_]*)\s*='
    $known = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($line in (Get-Content -LiteralPath $Target)) {
        if ($line -match $assignment) { [void]$known.Add($Matches.key) }
    }

    [System.Collections.Generic.List[string]]$additions = @()
    [System.Collections.Generic.List[string]]$sources = @()
    if ($Legacy -and (Test-Path -LiteralPath $Legacy)) { $sources.Add($Legacy) }
    $sources.Add($Template)
    foreach ($source in $sources) {
        foreach ($line in (Get-Content -LiteralPath $source)) {
            if ($line -match $assignment -and $known.Add($Matches.key)) {
                $additions.Add($line)
            }
        }
    }

    if ($additions.Count -eq 0) {
        Write-Host "Environment is up to date: $Target"
        return
    }

    $existingContent = Get-Content -LiteralPath $Target -Raw
    if ($existingContent) {
        $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $backup = "$Target.bak.$stamp"
        $backupIndex = 1
        while (Test-Path -LiteralPath $backup) {
            $backup = "$Target.bak.$stamp.$backupIndex"
            $backupIndex += 1
        }
        Copy-Item -LiteralPath $Target -Destination $backup
        Write-Host "Environment backup created: $backup"
    }

    [System.Collections.Generic.List[string]]$next = @()
    if ($existingContent) {
        $next.Add($existingContent.TrimEnd("`r", "`n"))
        $next.Add("")
    }
    $next.Add("# Added automatically during QuantDinger install/update")
    $next.AddRange($additions)
    [System.IO.File]::WriteAllLines(
        [System.IO.Path]::GetFullPath($Target),
        $next,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Added $($additions.Count) missing environment key(s) to $Target; existing values were preserved."
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
    $script:EnvExistedBeforeInstall = (Test-Path -LiteralPath $EnvFile) -or (Test-Path -LiteralPath $LegacyEnv)
}

function Download-Files {
    Write-Host "Downloading compose and unified environment template..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "$GithubRaw/docker-compose.ghcr.yml" -OutFile $ComposeFile
    Invoke-WebRequest -Uri "$GithubRaw/.env.example" -OutFile $EnvTemplate
    Sync-MissingEnvKeys $EnvFile $EnvTemplate $LegacyEnv
    Repair-EnvLayout $EnvFile @(
        "FRONTEND_PORT",
        "MOBILE_PORT",
        "BACKEND_PORT",
        "POSTGRES_PASSWORD",
        "IMAGE_PREFIX"
    )
}

function Collect-Settings {
    $existingUser = Get-EnvValue $EnvFile "ADMIN_USER"
    $existingPassword = Get-EnvValue $EnvFile "ADMIN_PASSWORD"
    $script:AdminCredentialsReused = $false
    if (
        $existingPassword -and
        $existingPassword -ne "123456" -and
        $existingUser.Length -ge 3 -and
        $existingUser.Length -le 50 -and
        $existingPassword.Length -ge 6
    ) {
        $script:AdminUser = $existingUser
        $script:AdminPassword = $existingPassword
        $script:AdminCredentialsReused = $true
        Write-Host "Existing administrator credentials detected; keeping the configured username and password." -ForegroundColor Yellow
        Write-Host "Change administrator credentials from Profile after signing in."
    } else {
        while ($true) {
            $script:AdminUser = Read-Value "Admin username" ($existingUser -replace '^$', 'quantdinger')
            if ($AdminUser.Length -lt 3 -or $AdminUser.Length -gt 50) {
                Write-Host "Admin username must be 3-50 characters." -ForegroundColor Red
                continue
            }
            break
        }

        while ($true) {
            $pass1 = Read-SecretPlain "Admin password"
            $pass2 = Read-SecretPlain "Confirm admin password"
            if ($pass1.Length -lt 6) {
                Write-Host "Admin password must be at least 6 characters." -ForegroundColor Red
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
    $script:AdminEmail = Read-Value "Admin email (optional)" (Get-EnvValue $EnvFile "ADMIN_EMAIL")

    $script:FrontendPort = Read-Value "Frontend port" ((Get-EnvValue $EnvFile "FRONTEND_PORT") -replace '^$', '8888')
    $script:MobilePort = Read-Value "Mobile H5 port" ((Get-EnvValue $EnvFile "MOBILE_PORT") -replace '^$', '8889')
    $script:BackendPort = Read-Value "Backend bind address" ((Get-EnvValue $EnvFile "BACKEND_PORT") -replace '^$', '127.0.0.1:5000')

    $existingPgPassword = Get-EnvValue $EnvFile "POSTGRES_PASSWORD"
    if (-not $existingPgPassword) {
        $existingPgPassword = Get-PostgresPasswordFromContainer
        if ($existingPgPassword) {
            Write-Host "Recovered the existing PostgreSQL credential from the current container." -ForegroundColor Yellow
        }
    }
    if (-not $existingPgPassword) {
        $existingPgPassword = Get-PostgresPasswordFromUrl (Get-EnvValue $EnvFile "DATABASE_URL")
        if ($existingPgPassword) {
            Write-Host "Recovered the existing PostgreSQL credential from DATABASE_URL." -ForegroundColor Yellow
        }
    }
    if (-not $existingPgPassword -and ($EnvExistedBeforeInstall -or (Test-ExistingPostgresVolume))) {
        Fail "The existing installation has no recoverable PostgreSQL password. Refusing to generate a replacement because it could disconnect existing data. Restore POSTGRES_PASSWORD in $EnvFile before continuing."
    }
    if ($existingPgPassword) { $script:PostgresPassword = $existingPgPassword } else { $script:PostgresPassword = New-HexSecret 18 }

    Write-Host ""
    Write-Host "Image source:"
    Write-Host "  1) global/default"
    Write-Host "  2) mainland China mirror (docker.m.daocloud.io/library/)"
    $choice = Read-Value "Select image source" "1"
    $existingImagePrefix = Get-EnvValue $EnvFile "IMAGE_PREFIX"
    if ($existingImagePrefix) {
        $script:ImagePrefix = $existingImagePrefix
    } elseif ($choice -eq "2") {
        $script:ImagePrefix = "docker.m.daocloud.io/library/"
    } else {
        $script:ImagePrefix = ""
    }

    $existingSecret = Get-EnvValue $EnvFile "SECRET_KEY"
    if ($existingSecret -and $existingSecret -ne "quantdinger-secret-key-change-me" -and $existingSecret.Length -ge 10) {
        $script:SecretKey = $existingSecret
    } else {
        $script:SecretKey = New-HexSecret 32
    }
}

function Write-Settings {
    Set-EnvValue $EnvFile "SECRET_KEY" $SecretKey
    Set-EnvValue $EnvFile "ADMIN_USER" $AdminUser
    Set-EnvValue $EnvFile "ADMIN_PASSWORD" $AdminPassword
    Set-EnvValue $EnvFile "ADMIN_EMAIL" $AdminEmail
    Set-EnvValue $EnvFile "FRONTEND_URL" "http://localhost:$FrontendPort,http://localhost:$MobilePort"
    Set-EnvValue $EnvFile "FRONTEND_PORT" $FrontendPort
    Set-EnvValue $EnvFile "MOBILE_PORT" $MobilePort
    Set-EnvValue $EnvFile "BACKEND_PORT" $BackendPort
    Set-EnvValue $EnvFile "POSTGRES_PASSWORD" $PostgresPassword
    Set-EnvValue $EnvFile "IMAGE_PREFIX" $ImagePrefix
}

function Start-Stack {
    Write-Host "Pulling images..." -ForegroundColor Yellow
    docker compose -f $ComposeFile pull
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose could not pull the required images."
    }
    Write-Host "Starting services..." -ForegroundColor Yellow
    docker compose -f $ComposeFile up -d
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose could not start the services."
    }
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
    Write-Host "Backend did not become healthy within the expected startup window. Check logs with:" -ForegroundColor Red
    Write-Host "  cd $InstallDir"
    Write-Host "  docker compose -f $ComposeFile logs -f backend"
    Fail "Installation did not complete successfully."
}

function Print-Summary {
    $apiPort = ($BackendPort -split ':')[-1]
    Write-Host ""
    Write-Host "QuantDinger is ready." -ForegroundColor Green
    Write-Host ""
    Write-Host "Web UI:      http://localhost:$FrontendPort"
    Write-Host "Mobile H5:   http://localhost:$MobilePort"
    Write-Host "API:         http://127.0.0.1:$apiPort"
    Write-Host "Directory:   $InstallDir"
    Write-Host "Username:    $AdminUser"
    if ($AdminCredentialsReused) {
        Write-Host "Password:    existing administrator password"
    } else {
        Write-Host "Password:    the password you entered during installation"
    }
    Write-Host ""
    Write-Host "Useful commands:"
    Write-Host "  cd $InstallDir"
    Write-Host "  docker compose -f $ComposeFile ps"
    Write-Host "  docker compose -f $ComposeFile logs -f backend"
    Write-Host "  Rerun this installer to update images and append newly added .env keys safely."
    Write-Host ""
    Write-Host "Trading involves substantial risk. Start with paper trading and small test accounts." -ForegroundColor Yellow
}

if ($env:QUANTDINGER_INSTALL_LIB_ONLY -ne "true") {
    Check-Prerequisites
    Prepare-Directory
    Download-Files
    Collect-Settings
    Write-Settings
    Start-Stack
    Wait-ForBackend
    Print-Summary
}
