$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
        throw "docker-compose was not found."
    }

    & docker-compose down
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose shutdown failed."
    }

    Write-Host "Containers stopped. Data in Docker volumes was preserved."
}
finally {
    Pop-Location
}
