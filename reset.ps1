param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $Force) {
    Write-Warning "A full reset deletes PostgreSQL, MinIO, Prometheus, and Grafana data."
    $confirmation = Read-Host "Type RESET to continue"
    if ($confirmation -cne "RESET") {
        Write-Host "Reset cancelled."
        return
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
        throw "docker-compose was not found."
    }

    & docker-compose down -v
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose reset failed."
    }

    Write-Host "Project containers and named volumes were removed."
}
finally {
    Pop-Location
}
