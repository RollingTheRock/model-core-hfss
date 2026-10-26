# bootstrap-cloud.ps1 — One-shot environment bootstrap for the cloud desktop.
#
# What it does (idempotent, safe to re-run):
#   1. Creates .venv (Python 3.12+) if missing.
#   2. Activates it.
#   3. Sets the enterprise proxy for pip (network only).
#   4. Installs em-copilot-core in editable mode (pip install -e .).
#   5. Installs the locked ansys/mcp extras (pip install -r requirements-cloud.txt).
#   6. Generates .mcp.json via scripts/setup-mcp-json.py (proxy cleared in env).
#   7. Runs em-copilot-envcheck (Phase 0) and prints the verdict.
#
# What it does NOT do:
#   - Launch AEDT / consume a HFSS license. envcheck probes install only.
#   - Copy a .venv from another machine (absolute paths break — rebuilt here).
#   - Install AEDT itself (that is an IT task on the cloud desktop).
#
# Usage (PowerShell, cloud desktop, from the project root):
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned  # if blocked
#   .\scripts\bootstrap-cloud.ps1
#
# Override the proxy if the cloud desktop uses a different egress:
#   $env:PIP_PROXY = "http://your-proxy:8080"
#   .\scripts\bootstrap-cloud.ps1
#
# Exit codes: 0 = envcheck healthy; 1 = a step failed; 2 = wrong Python version.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# --- 0. Python version gate (requires-python >=3.12,<4) ---
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { $pyExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
if (-not $pyExe) { Write-Error "python not found on PATH. Install Python 3.12+ first."; exit 2 }
$pyVer = (& $pyExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
$major,$minor = $pyVer.Split('.')
if (-not ([int]$major -eq 3 -and [int]$minor -ge 12)) {
    Write-Error "Python $pyVer found, but 3.12+ is required (pyproject requires-python >=3.12,<4)."
    exit 2
}
Write-Host "[1/7] Python $pyVer OK ($pyExe)" -ForegroundColor Cyan

# --- 1. venv ---
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[2/7] Creating .venv ..." -ForegroundColor Cyan
    & $pyExe -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed"; exit 1 }
} else {
    Write-Host "[2/7] .venv already exists" -ForegroundColor Cyan
}
$venvExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$venvPip  = @($venvExe, "-m", "pip")
$venvMod  = @($venvExe, "-m")

# --- 2. Proxy for pip (network only; NOT for the MCP server) ---
$proxy = $env:PIP_PROXY
if (-not $proxy) { $proxy = "http://proxy.xfusion.com:8080" }
$env:HTTP_PROXY  = $proxy
$env:HTTPS_PROXY = $proxy
Write-Host "[3/7] pip proxy = $proxy" -ForegroundColor Cyan

# --- 3. pip upgrade + trusted hosts ---
$trusted = @("--trusted-host","pypi.org","--trusted-host","files.pythonhosted.org","--trusted-host","pypi.python.org")
Write-Host "[4/7] Upgrading pip ..." -ForegroundColor Cyan
& $venvPip[0] $venvPip[1..2] "install","--upgrade","pip" $trusted
if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed"; exit 1 }

# --- 4. em-copilot-core (editable) ---
Write-Host "[5/7] pip install -e . (em-copilot-core) ..." -ForegroundColor Cyan
& $venvPip[0] $venvPip[1..2] "install","-e","." $trusted
if ($LASTEXITCODE -ne 0) { Write-Error "em-copilot-core install failed"; exit 1 }

# --- 5. locked ansys/mcp extras ---
Write-Host "[6/7] pip install -r requirements-cloud.txt ..." -ForegroundColor Cyan
& $venvPip[0] $venvPip[1..2] "install","-r","requirements-cloud.txt" $trusted
if ($LASTEXITCODE -ne 0) { Write-Error "requirements-cloud install failed"; exit 1 }

# --- 6. .mcp.json (proxy CLEARED in the server env) ---
Write-Host "[7/7] Generating .mcp.json + running envcheck ..." -ForegroundColor Cyan
$setupScript = Join-Path $ProjectRoot "scripts\setup-mcp-json.py"
& $venvExe $setupScript
if ($LASTEXITCODE -ne 0) { Write-Error "setup-mcp-json.py failed"; exit 1 }

# --- 7. Phase 0 envcheck (offline, no license) ---
# Clear proxy so envcheck's own subprocess calls (ansys-aedt-mcp --help) work.
$env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; $env:NO_PROXY = "*"
& $venvExe "-m" "em_copilot.envcheck"
$envExit = $LASTEXITCODE

Write-Host ""
if ($envExit -eq 0) {
    Write-Host "BOOTSTRAP COMPLETE. envcheck exit 0." -ForegroundColor Green
    Write-Host "Expected on cloud desktop: 0 fail / 0 warn / 0 or 1 blocked (aedt.install)."
    Write-Host "If aedt.install is PASS, the cloud desktop has AEDT — proceed to /mcp."
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. In Claude Code: /mcp  -> 'pyaedt-em' should be connected."
    Write-Host "  2. Load the 'automate-pyaedt-em' skill and follow the 9-step pipeline"
    Write-Host "     (see docs/cloud-desktop-migration.md section 6)."
    exit 0
} else {
    Write-Error "envcheck reported failures (exit $envExit). See output above."
    exit 1
}
