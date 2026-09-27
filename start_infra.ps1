$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "Starting Docker infrastructure: PostgreSQL + Redis..."
docker compose up -d postgres redis

Write-Host ""
Write-Host "Infrastructure is ready or still warming up."
Write-Host "PostgreSQL: localhost:5433, database cvservice_db, user postgres"
Write-Host "Redis:      localhost:6379"
Write-Host ""
Write-Host "To run the app with Docker PostgreSQL:"
Write-Host ".\start_project.ps1 -UseDockerDb"
