<#
.SYNOPSIS
  Turn a downloaded TSS MDM .bat installer into a true double-click .exe (optionally code-signed).
  Run this ONCE on a Windows machine to produce the .exe you distribute to IT.

.EXAMPLE
  # 1) In the console, open an enrollment token -> download the Windows installer (.bat)
  # 2) Build an .exe from it:
  .\build-windows-exe.ps1 -BatPath "$HOME\Downloads\TSS-MDM-Setup-HeadOffice.bat"

.EXAMPLE
  # With code signing (recommended so Windows SmartScreen doesn't warn users):
  .\build-windows-exe.ps1 -BatPath .\TSS-MDM-Setup-HeadOffice.bat -PfxPath .\tss-codesign.pfx -PfxPassword (Read-Host -AsSecureString)

.NOTES
  Needs internet the first time to install the ps2exe module from the PowerShell Gallery.
  The .exe self-elevates (UAC) exactly like the .bat and carries the same embedded server URL + token.
#>
param(
  [Parameter(Mandatory = $true)][string]$BatPath,
  [string]$OutPath,
  [string]$IconPath,
  [string]$PfxPath,
  [System.Security.SecureString]$PfxPassword
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $BatPath)) { throw "Installer not found: $BatPath" }
if (-not $OutPath) { $OutPath = [IO.Path]::ChangeExtension($BatPath, '.exe') }

# ps2exe can't wrap a .bat directly, so wrap it in a tiny PowerShell stub that runs the batch elevated.
$work = Join-Path $env:TEMP ("tssmdm-build-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$batCopy = Join-Path $work 'install.bat'
Copy-Item $BatPath $batCopy -Force
$stub = Join-Path $work 'stub.ps1'
@'
# TSS MDM installer stub: writes the embedded batch to disk and runs it.
$ErrorActionPreference = 'Stop'
$bat = Join-Path $env:TEMP ('TSS-MDM-Setup-' + [guid]::NewGuid().ToString('N') + '.bat')
[IO.File]::WriteAllText($bat, $PSScriptRoot_BAT)
Start-Process -FilePath $bat -Wait
Remove-Item $bat -Force -ErrorAction SilentlyContinue
'@ | Set-Content $stub -Encoding UTF8
# Inline the batch content as a here-string into the stub.
$batText = Get-Content $batCopy -Raw
$escaped = $batText -replace "'", "''"
(Get-Content $stub -Raw).Replace('$PSScriptRoot_BAT', "@'`r`n$escaped`r`n'@") | Set-Content $stub -Encoding UTF8

if (-not (Get-Module -ListAvailable -Name ps2exe)) {
  Write-Host 'Installing ps2exe module (first time only)...' -ForegroundColor Yellow
  try { Install-Module ps2exe -Scope CurrentUser -Force -AllowClobber }
  catch { throw "Could not install ps2exe. On an offline machine, run: Save-Module ps2exe -Path <usb> on an online PC, copy it over, then Import-Module ps2exe.`n$_" }
}
Import-Module ps2exe

$args = @{
  inputFile   = $stub
  outputFile  = $OutPath
  noConsole   = $false
  requireAdmin = $true
  title       = 'TSS MDM Agent Setup'
  company     = 'The Souled Store'
  product     = 'TSS MDM Agent'
  version     = '0.1.0'
}
if ($IconPath -and (Test-Path $IconPath)) { $args.iconFile = $IconPath }
Write-Host "Building $OutPath ..."
Invoke-ps2exe @args

if ($PfxPath) {
  if (-not (Test-Path $PfxPath)) { throw "PFX not found: $PfxPath" }
  $st = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if (-not $st) { throw "signtool.exe not found. Install the Windows SDK, or sign the .exe manually with your certificate." }
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword))
  & $st.Source sign /f $PfxPath /p $plain /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $OutPath
  if ($LASTEXITCODE -ne 0) { throw 'Signing failed.' }
  Write-Host 'Signed.' -ForegroundColor Green
}

Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "`nDone: $OutPath" -ForegroundColor Green
Write-Host 'Distribute this .exe to IT. Double-click -> UAC prompt -> device enrols automatically.'
