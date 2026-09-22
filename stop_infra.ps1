$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "Stopping Docker infrastructure..."
docker compose stop postgres redis

Write-Host "Docker containers stopped. Data is preserved in Docker volumes."
