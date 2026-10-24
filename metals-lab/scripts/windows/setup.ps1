# Metal Lab - guided Windows setup. Read-only: it never arms trading and never sends orders.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
Write-Host "Metal Lab setup in $Root" -ForegroundColor Cyan

# 1. Python 3.10-3.14 (versions with official MetaTrader5 Windows wheels)
$ErrorActionPreference = "Continue"
$py = $null
$candidates = @("py -3.12", "py -3.13", "py -3.14", "py -3.11", "py -3.10", "python")
foreach ($cand in $candidates) {
  $c = $cand.Split(" ")
  $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
  $v = & $exe @rest -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
  if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(1[0-4])$') { $py = $c; Write-Host "Using Python $v ($($c -join ' '))"; break }
  elseif ($v) { Write-Host "Skipping Python $v ($($c -join ' ')): MetaTrader5 needs 3.10-3.14" -ForegroundColor Yellow }
}
$ErrorActionPreference = "Stop"
if (-not $py) { Write-Host "No suitable Python found. Install 64-bit Python 3.12 from python.org (tick 'Add python.exe to PATH') and re-run." -ForegroundColor Red; exit 1 }

# 2. Virtual environment + dependencies
if (-not (Test-Path ".venv")) { $exe = $py[0]; $rest = @($py | Select-Object -Skip 1); & $exe @rest -m venv .venv }
$vpy = Join-Path $Root ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
& $vpy -m pip install -r backend\requirements-windows.txt
& $vpy -m pip install -e backend --no-deps
& $vpy -c "import MetaTrader5 as m; print('MetaTrader5 package', m.__version__)"

# 3. Configuration (no credentials anywhere)
if (-not (Test-Path "config\config.yaml")) { Copy-Item config\config.example.yaml config\config.yaml; Write-Host "Created config\config.yaml" }
if (-not (Test-Path "config\news_calendar.csv")) { Copy-Item config\news_calendar.example.csv config\news_calendar.csv; Write-Host "Created config\news_calendar.csv (news status stays UNKNOWN until you verify it)" }

# 4. Dashboard (prebuilt copy ships in dashboard\out; rebuild only if Node.js is present and you want to)
if (Test-Path "dashboard\out\index.html") { Write-Host "Dashboard build found." }
elseif (Get-Command node -ErrorAction SilentlyContinue) {
  Push-Location dashboard; npm ci; npm run build; Pop-Location
} else { Write-Host "No dashboard build and no Node.js: install Node 20+ and run 'npm ci; npm run build' in dashboard\" -ForegroundColor Yellow }

# 5. Desktop and Start-menu icon (runs scripts\windows\launch.ps1: update, start, open the dashboard)
& (Join-Path $PSScriptRoot "install-shortcut.ps1")

# 6. MT5 terminal
$term = Get-Process terminal64 -ErrorAction SilentlyContinue
if (-not $term) {
  Write-Host "MetaTrader 5 is not running. Start it, log into your DEMO account (File > Login), enable Algo Trading, then re-run or run scripts\windows\check.bat." -ForegroundColor Yellow
  exit 0
}

# 7. Read-only demo checks
& $vpy -m metals_lab.cli check
Write-Host ""
Write-Host "Setup finished. Trading is NOT armed." -ForegroundColor Green
Write-Host "Start the lab:  double-click the Metal Lab icon on your desktop (or scripts\windows\start.bat)"
Write-Host "Arm demo trading later, manually:  .venv\Scripts\metals-lab arm --operator ""your name"""
