# Metal Lab one-click launcher (what the desktop icon runs).
# 1. Fetches the latest version with git (skipped, with a message, if that fails).
# 2. Reinstalls the Python package only when its dependency files changed.
# 3. Applies settings agreed since setup to config\config.yaml (once each, with a backup).
# 4. Starts the lab and opens the dashboard; if it is already running, just opens the dashboard.
# It never arms trading: the lab starts in whatever state you left it (shadow mode unless you armed it with the CLI).
$ErrorActionPreference = "Continue"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
$Host.UI.RawUI.WindowTitle = "Metal Lab"
$Url = "http://127.0.0.1:8765/"
$vpy = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $vpy)) {
  Write-Host "Metal Lab is not set up yet. Run scripts\windows\setup.ps1 first." -ForegroundColor Red
  Read-Host "Press Enter to close"; exit 1
}

# Already running? Then only open the dashboard.
$running = $false
try { $running = ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$($Url)api/status").StatusCode -eq 200) } catch { }
if ($running) {
  Write-Host "Metal Lab is already running; opening the dashboard."
  try { Start-Process $Url } catch { Write-Host "Open $Url in your browser." }
  exit 0
}

# 1. Update
if (Get-Command git -ErrorAction SilentlyContinue) {
  Write-Host "Checking for updates..." -ForegroundColor Cyan
  git pull --ff-only
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not update (offline, or files changed locally). Starting the version you have." -ForegroundColor Yellow
  }
} else {
  Write-Host "git not found; starting the version you have." -ForegroundColor Yellow
}

# 2. Reinstall only when dependencies changed
$deps = @("backend\requirements-windows.txt", "backend\pyproject.toml") | Where-Object { Test-Path $_ }
$hash = ($deps | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash }) -join ""
$stamp = Join-Path $Root ".venv\metal-lab-deps.sha256"
if (-not (Test-Path $stamp) -or (Get-Content $stamp -Raw).Trim() -ne $hash) {
  Write-Host "Dependencies changed; updating the Python environment..." -ForegroundColor Cyan
  & $vpy -m pip install -r backend\requirements-windows.txt
  $ok = ($LASTEXITCODE -eq 0)
  & $vpy -m pip install -e backend --no-deps
  if ($ok -and $LASTEXITCODE -eq 0) { Set-Content -Path $stamp -Value $hash }
  else { Write-Host "Updating the Python environment failed; it will be retried next launch." -ForegroundColor Yellow }
}

# 3. Apply settings agreed since setup to config\config.yaml (once each, backup kept next to it), and any one-time
#    fresh lab the operator asked for (old database archived, never while bot trades are open)
& $vpy -m metals_lab.cli apply-settings

# 4. Start: open the browser once the server answers, and keep the server in this window (close it to stop the lab)
Write-Host "Starting Metal Lab. Close this window (or press Ctrl+C) to stop it." -ForegroundColor Green
Start-Job -ScriptBlock {
  param($u)
  for ($i = 0; $i -lt 60; $i++) {
    try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$($u)api/status").StatusCode -eq 200) { Start-Process $u; return } } catch { }
    Start-Sleep -Seconds 1
  }
} -ArgumentList $Url | Out-Null
& $vpy -m metals_lab.cli serve
