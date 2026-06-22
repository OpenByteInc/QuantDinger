# Helper script to generate a secure SECRET_KEY for QuantDinger (Windows PowerShell)
# Usage: .\scripts\generate-secret-key.ps1

$envFile = "backend_api_python\.env"

# Check if .env exists.
if (-not (Test-Path $envFile)) {
    Write-Host "Error: $envFile not found" -ForegroundColor Red
    Write-Host "Please run: Copy-Item backend_api_python\env.example -Destination backend_api_python\.env"
    exit 1
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
}

if (-not $pythonCommand) {
    Write-Host "Error: Failed to find Python. Install Python or ensure py/python is in PATH." -ForegroundColor Red
    exit 1
}

# Generate a secure random key.
$newKey = & $pythonCommand.Source -c "import secrets; print(secrets.token_hex(32))"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to generate SECRET_KEY. Make sure Python is installed." -ForegroundColor Red
    exit 1
}

# Update SECRET_KEY in .env file.
$content = Get-Content $envFile
$content = $content -replace '^SECRET_KEY=.*', "SECRET_KEY=$newKey"
$content | Set-Content $envFile

Write-Host "[OK] SECRET_KEY generated and updated in $envFile" -ForegroundColor Green
Write-Host ""
Write-Host "Generated key: $newKey" -ForegroundColor Cyan
Write-Host ""
Write-Host "You can now start the application:"
Write-Host "  docker-compose up -d --build"
