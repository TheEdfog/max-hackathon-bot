param(
    [switch]$WithInfra,
    [switch]$UseDockerDb,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if ($WithInfra) {
    Write-Host "Starting Docker infrastructure..."
    docker compose up -d postgres redis
}

if ($UseDockerDb) {
    $env:DATABASE_URL = "postgresql+psycopg://postgres:MyNewStrongPassword123!@localhost:5433/cvservice_db"
    $env:REDIS_URL = "redis://localhost:6379/0"
    Write-Host "Using Docker PostgreSQL on localhost:5433."
} else {
    Write-Host "Using DATABASE_URL from .env."
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$arguments = @(
    "-m", "uvicorn",
    "apps.web.main:app",
    "--host", "127.0.0.1",
    "--port", "8000"
)

if ($Reload) {
    $arguments += "--reload"
}

Write-Host "Starting RezyumIT at http://127.0.0.1:8000"
& $python @arguments
