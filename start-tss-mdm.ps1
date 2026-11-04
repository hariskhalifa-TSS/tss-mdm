# TSS MDM - local launcher for Windows. Finds (or installs) Python, sets up, starts the server.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log  = Join-Path $Root 'start-log.txt'
Start-Transcript -Path $Log -Force | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-Py($exe, $extra = @()) {
  try {
    $v = & $exe @extra -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(1[0-9])$' -and [int]$Matches[1] -ge 10) { return $true }
  } catch {}
  return $false
}

function Find-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($ver in '-3.12', '-3.13', '-3.11', '-3') { if (Test-Py 'py' @($ver)) { return ,@('py', $ver) } }
  }
  $cands = @()
  $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
  $cands += Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $cands += $cmd.Source }
  foreach ($c in $cands) { if (Test-Py $c) { return ,@($c) } }
  return $null
}

try {
  Write-Host "`n=== TSS MDM local launcher ===`n" -ForegroundColor Cyan
  $py = Find-Python
  if (-not $py) {
    Write-Host 'Python 3 not found - installing Python 3.12 (about 1-2 minutes)...' -ForegroundColor Yellow
    $installer = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer -UseBasicParsing
    Start-Process $installer -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0' -Wait
    $py = Find-Python
    if (-not $py) { throw 'Python install did not complete. Install Python 3.12 from python.org manually, then run this again.' }
  }
  Write-Host "Using Python: $($py -join ' ')"

  $server = Join-Path $Root 'server'
  $venvPy = Join-Path $server '.venv\Scripts\python.exe'
  if (-not (Test-Path $venvPy)) {
    Write-Host 'First run: creating environment...'
    $exe = $py[0]; $args1 = @($py | Select-Object -Skip 1)
    & $exe @args1 -m venv (Join-Path $server '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
  }
  Write-Host 'Installing / checking packages (first time takes a minute)...'
  & $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $server 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { throw 'Package install failed (check internet connection).' }

  $env:MDM_SUPERADMIN_EMAIL = 'haris.khalifa@thesouledstore.com'
  $env:MDM_SUPERADMIN_NAME  = 'Haris Khalifa'
  $env:MDM_PUBLIC_URL       = 'http://localhost:8000'
  $env:MDM_AGENTS_DIR       = Join-Path $Root 'agents'

  $busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
  if ($busy) { throw "Port 8000 is already in use (process id $($busy[0].OwningProcess)). Close that program or the other TSS MDM window." }

  # Open the browser once the server answers
  $pwFile = Join-Path $server 'data\initial_superadmin.txt'
  Start-Job -ArgumentList $pwFile -ScriptBlock {
    param($pwFile)
    for ($i = 0; $i -lt 60; $i++) {
      try { Invoke-WebRequest 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
    }
    Start-Process 'http://localhost:8000'
    if (Test-Path $pwFile) { Start-Process notepad.exe $pwFile }
  } | Out-Null

  Write-Host "`nTSS MDM is running at http://localhost:8000  (close this window to stop it)`n" -ForegroundColor Green
  Set-Location $server
  & $venvPy -m uvicorn app.main:app --host 127.0.0.1 --port 8000
}
catch {
  Write-Host "`nERROR: $_" -ForegroundColor Red
  Write-Host "A log was saved to $Log - tell Claude it failed and it can read the log."
}
finally {
  Stop-Transcript | Out-Null
  Read-Host "`nPress Enter to close"
}
