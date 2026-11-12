<#
  TSS MDM general installer. Compiled to TSS-MDM-Installer.exe by build-windows-exe.ps1.
  Runs elevated (ps2exe -requireAdmin), asks for the server URL + token, enrols this device.
  Values can also be passed as -Server / -Token (for a baked one-click build).
#>
param([string]$Server, [string]$Token)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Pause-Exit([int]$code) { Write-Host ''; Read-Host 'Press Enter to close'; exit $code }

try {
  Write-Host ''
  Write-Host '  TSS MDM - enroll this device' -ForegroundColor Cyan
  Write-Host ''

  if (-not $Server) { $Server = Read-Host '  Server URL (e.g. https://your-app.up.railway.app)' }
  if (-not $Token)  { $Token  = Read-Host '  Enrollment token' }

  $Server = $Server.Trim().TrimEnd('/')
  $Token  = $Token.Trim()
  if (-not $Server -or -not $Token) { Write-Host '  Server URL and token are both required.' -ForegroundColor Red; Pause-Exit 1 }

  # Always use https (http gets redirected and breaks enrollment)
  if ($Server -match '^http://')      { $Server = 'https://' + $Server.Substring(7) }
  elseif ($Server -notmatch '^https://') { $Server = 'https://' + $Server }

  if ($Server -match 'localhost|127\.0\.0\.1') {
    Write-Host '  That URL points at THIS PC, not your server. Use your Railway address.' -ForegroundColor Red
    Pause-Exit 1
  }

  Write-Host "  Checking connection to $Server ..."
  try {
    $h = Invoke-WebRequest -UseBasicParsing "$Server/api/health" -TimeoutSec 20
    if ($h.Content -notmatch 'ok') { throw "unexpected response" }
  } catch {
    Write-Host "  Could not reach $Server" -ForegroundColor Red
    Write-Host "  - Open $Server in a browser to confirm it's your live server"
    Write-Host "  - Check this PC has internet access"
    Pause-Exit 1
  }

  Write-Host '  Connected. Downloading agent and enrolling...'
  $usedExe = $false
  try {
    # Preferred: tiny native Rust agent (lowest resource use). May not exist until CI has built it.
    $exe = Join-Path $env:TEMP 'tss-agent.exe'
    Invoke-WebRequest -UseBasicParsing "$Server/downloads/tss-agent.exe" -OutFile $exe
    if ((Get-Item $exe).Length -lt 100000) { throw 'exe too small / not a real build' }
    & $exe --server $Server --token $Token --install
    if ($LASTEXITCODE -ne 0) { throw "native agent install returned $LASTEXITCODE" }
    $usedExe = $true
  } catch {
    Write-Host '  Native agent not available yet; using the built-in agent.' -ForegroundColor Yellow
  }
  if (-not $usedExe) {
    # Fallback: PowerShell agent, run IN-MEMORY so execution-policy / script-blocking never applies.
    $content = (Invoke-WebRequest -UseBasicParsing "$Server/downloads/tss-agent.ps1").Content
    $sb = [scriptblock]::Create($content)
    & $sb -Server $Server -Token $Token -Install
  }

  Write-Host ''
  Write-Host '  Done. This device is enrolled and will appear under Devices.' -ForegroundColor Green
  Pause-Exit 0
}
catch {
  Write-Host ''
  Write-Host ("  ERROR: " + $_.Exception.Message) -ForegroundColor Red
  Write-Host '  (Tip: make sure the enrollment token is Active and not expired.)'
  Pause-Exit 1
}
