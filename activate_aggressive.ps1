# AGGRESSIVE_MODE_ACTIVATE.ps1
# Script rápido para ativar MODO AGGRESSIVE
# USO: .\activate_aggressive.ps1

Write-Host "`n================================" -ForegroundColor Yellow
Write-Host "  MODO AGGRESSIVE - QUICK ACTIVATION" -ForegroundColor Yellow
Write-Host "================================`n" -ForegroundColor Yellow

# Get root directory
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Check if .env exists
$envFile = Join-Path $root ".env"
if (!(Test-Path $envFile)) {
    Write-Host "✗ .env file not found at: $envFile" -ForegroundColor Red
    Write-Host "  Creating new .env with TRADING_AGGRESSION=aggressive..." -ForegroundColor Yellow
    "TRADING_AGGRESSION=aggressive
BOT_ARENA_MODE=paper" | Out-File -FilePath $envFile -Encoding UTF8
    Write-Host "✓ .env created" -ForegroundColor Green
} else {
    # Check if TRADING_AGGRESSION exists
    $content = Get-Content $envFile -Raw
    if ($content -match "TRADING_AGGRESSION=") {
        Write-Host "✓ .env exists with TRADING_AGGRESSION" -ForegroundColor Green
        # Update value
        $content = $content -replace 'TRADING_AGGRESSION=.*', 'TRADING_AGGRESSION=aggressive'
        $content | Out-File -FilePath $envFile -Encoding UTF8
    } else {
        Write-Host "⚠ Adding TRADING_AGGRESSION to existing .env..." -ForegroundColor Yellow
        "`nTRADING_AGGRESSION=aggressive" | Out-File -FilePath $envFile -Encoding UTF8 -Append
    }
    Write-Host "✓ .env updated" -ForegroundColor Green
}

# Display current config
Write-Host "`n[1] Current Configuration:" -ForegroundColor Cyan
Get-Content $envFile | Select-String "TRADING_AGGRESSION|BOT_ARENA_MODE" | ForEach-Object { Write-Host "  $_" }

# Validate Python config
Write-Host "`n[2] Validating Python Config..." -ForegroundColor Cyan
try {
    $pythonCheck = python -c "import config; print(f'Aggression: {config.get_aggression_level()}\nMin Confidence: {config.get_min_confidence()}\nMin Edge: {config.get_min_edge_after_fees():.6f}')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Config loaded:" -ForegroundColor Green
        $pythonCheck | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "✗ Python config error: $pythonCheck" -ForegroundColor Red
    }
} catch {
    Write-Host "✗ Python not accessible from PATH" -ForegroundColor Red
}

# Show status check command
Write-Host "`n[3] Next Steps:" -ForegroundColor Cyan
Write-Host "  a) Run status check:" -ForegroundColor White
Write-Host "     python tools/aggressive_status.py" -ForegroundColor Gray
Write-Host ""
Write-Host "  b) Start arena:" -ForegroundColor White
Write-Host "     python arena.py" -ForegroundColor Gray
Write-Host ""
Write-Host "  c) Monitor in real-time:" -ForegroundColor White
Write-Host "     Get-Content logs/arena.log.* -Tail 20 -Wait" -ForegroundColor Gray

Write-Host "`n================================" -ForegroundColor Yellow
Write-Host "  🔴 MODO AGGRESSIVE ATIVADO" -ForegroundColor Yellow
Write-Host "================================`n" -ForegroundColor Yellow
