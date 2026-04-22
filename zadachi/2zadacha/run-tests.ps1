# Mirrors tasks/22.04-broker/run-tests.sh for Windows (Python implementation).
# Usage: .\run-tests.ps1 [all|basic|size|rate]
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$exp = if ($args.Count -ge 1) { $args[0] } else { "all" }

Write-Host "[INFO] Starting brokers (docker compose)..." -ForegroundColor Green
docker compose up -d

Write-Host "[INFO] Waiting for RabbitMQ..." -ForegroundColor Green
for ($i = 1; $i -le 30; $i++) {
  docker compose exec -T rabbitmq rabbitmq-diagnostics ping 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Host "[INFO] RabbitMQ is ready"; break }
  Start-Sleep -Seconds 2
  if ($i -eq 30) { throw "RabbitMQ did not start in time" }
}

Write-Host "[INFO] Waiting for Redis..." -ForegroundColor Green
for ($i = 1; $i -le 15; $i++) {
  docker compose exec -T redis redis-cli ping 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Host "[INFO] Redis is ready"; break }
  Start-Sleep -Seconds 1
  if ($i -eq 15) { throw "Redis did not start in time" }
}

Write-Host "[INFO] Installing Python deps (if needed)..." -ForegroundColor Green
python -m pip install -q -r requirements.txt

Write-Host "[INFO] Running experiment: $exp" -ForegroundColor Green
python run_benchmark.py $exp

Write-Host "[INFO] Done. Results: .\results\ (CSV + report.md + JSON). Containers still running. Stop: docker compose down" -ForegroundColor Green
