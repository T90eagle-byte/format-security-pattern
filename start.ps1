$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
        throw "docker-compose was not found. Start Docker Desktop and check the Docker Compose installation."
    }

    if (-not (Test-Path -LiteralPath ".env")) {
        Copy-Item -LiteralPath ".env.example" -Destination ".env"
        Write-Host "Created .env from .env.example."
    }

    & docker-compose up --build -d
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose startup failed."
    }

    $readinessUrls = [ordered]@{
        "Gateway" = "http://localhost:8080/health"
        "Prometheus" = "http://localhost:9090/-/ready"
        "Grafana" = "http://localhost:3000/api/health"
    }
    $deadline = (Get-Date).AddMinutes(3)
    $notReady = @($readinessUrls.Keys)

    do {
        $notReady = @(
            foreach ($name in $readinessUrls.Keys) {
                try {
                    $response = Invoke-WebRequest `
                        -UseBasicParsing `
                        -TimeoutSec 3 `
                        -Uri $readinessUrls[$name]
                    if ($response.StatusCode -ne 200) {
                        $name
                    }
                }
                catch {
                    $name
                }
            }
        )

        if ($notReady.Count -gt 0 -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 3
        }
    } while ($notReady.Count -gt 0 -and (Get-Date) -lt $deadline)

    & docker-compose ps
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read container status."
    }

    if ($notReady.Count -gt 0) {
        throw "Components did not become ready: $($notReady -join ', ')."
    }

    Write-Host ""
    Write-Host "System is ready:"
    Write-Host "  Web UI:     http://localhost:8080"
    Write-Host "  Grafana:    http://localhost:3000"
    Write-Host "  Prometheus: http://localhost:9090"
    Write-Host "  MinIO:      http://localhost:9001"
    Write-Host ""
}
finally {
    Pop-Location
}
