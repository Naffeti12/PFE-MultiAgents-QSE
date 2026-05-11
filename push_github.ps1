# ============================================================
# Script PowerShell - Push projet PFE vers GitHub
# Usage : .\push_github.ps1  (depuis C:\PFE)
# ============================================================

$TOKEN    = "YOUR_TOKEN_HERE"
$USERNAME = "Naffeti12"
$REPO     = "PFE-MultiAgents-QSE"
$REMOTE   = "https://${USERNAME}:${TOKEN}@github.com/${USERNAME}/${REPO}.git"

# --- Etape 1 : Creer le repo GitHub via API ---
Write-Host "=== Etape 1 : Creation du repository GitHub ===" -ForegroundColor Cyan
$body = '{"name":"PFE-MultiAgents-QSE","description":"Plateforme Multi-Agents QSE - TIM Tunisie PFE 2025-2026","private":false}'
$headers = @{ Authorization = "token $TOKEN"; Accept = "application/vnd.github.v3+json" }
try {
    $r = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body -ContentType "application/json"
    Write-Host "Repository cree : $($r.html_url)" -ForegroundColor Green
} catch {
    Write-Host "Repo existant ou erreur - on continue..." -ForegroundColor Yellow
}

# --- Etape 2 : Git init ---
Write-Host "`n=== Etape 2 : Git init ===" -ForegroundColor Cyan
Set-Location "C:\PFE"
git init -b main
git config user.email "mednfft.1@gmail.com"
git config user.name "Neffati Mohamed"

# --- Etape 3 : Staging ---
Write-Host "`n=== Etape 3 : Staging des fichiers ===" -ForegroundColor Cyan
git add main_orchestrator.py
git add requirements.txt
git add README.md
git add .env.example
git add .gitignore
git add agents/
git add orchestrator/
git add loaders/
if (Test-Path "qalitas_main.py")      { git add qalitas_main.py }
if (Test-Path "qalitas_dashboard.py") { git add qalitas_dashboard.py }

# --- Etape 4 : Commit (message sans tirets problematiques) ---
Write-Host "`n=== Etape 4 : Commit ===" -ForegroundColor Cyan
$msg = "feat: plateforme multi-agents QSE pipeline complet 4 agents"
git commit -m $msg

# --- Etape 5 : Push ---
Write-Host "`n=== Etape 5 : Push vers GitHub ===" -ForegroundColor Cyan
git remote remove origin 2>$null
git remote add origin $REMOTE
git push -u origin main --force

Write-Host "`n=== DONE ===" -ForegroundColor Green
Write-Host "Lien : https://github.com/$USERNAME/$REPO" -ForegroundColor Green
