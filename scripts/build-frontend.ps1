# Local Vue dev on port 8000 (vite server). Does not touch Docker frontend.
# Backend API proxy target: http://localhost:5000 (see QuantDinger-Vue-src/.env.development)

$ErrorActionPreference = 'Stop'
$VueSrc = Join-Path (Split-Path -Parent $PSScriptRoot) 'QuantDinger-Vue-src'

if (-not (Test-Path $VueSrc)) {
    Write-Error "QuantDinger-Vue-src not found at $VueSrc"
}

Write-Host "Starting Vite dev server at http://localhost:8000 (Ctrl+C to stop) ..." -ForegroundColor Green
Push-Location $VueSrc
try {
    pnpm run serve
} finally {
    Pop-Location
}
