<#
.SYNOPSIS
  Build TSS-MDM-Installer.exe directly from the installer PowerShell script using ps2exe.
  The .exe requests admin (UAC) on launch, asks for the server URL + token, and enrolls the device.
  Run this once on a Windows machine (needs internet the first time to fetch the ps2exe module).

.EXAMPLE
  .\build-windows-exe.ps1
  .\build-windows-exe.ps1 -OutPath "$HOME\Desktop\TSS-MDM-Installer.exe"
  .\build-windows-exe.ps1 -Server https://your-app.up.railway.app -Token abc123   # baked one-click build
  .\build-windows-exe.ps1 -PfxPath .\codesign.pfx -PfxPassword (Read-Host -AsSecureString)
#>
param(
  [string]$Ps1Path,
  [string]$OutPath,
  [string]$Server,
  [string]$Token,
  [string]$BatPath,          # ignored; kept so older shortcuts don't error
  [string]$IconPath,
  [string]$PfxPath,
  [System.Security.SecureString]$PfxPassword
)
$ErrorActionPreference = 'Stop'

if (-not $Ps1Path) { $Ps1Path = Join-Path $PSScriptRoot 'tss-mdm-installer.ps1' }
if (-not (Test-Path $Ps1Path)) { throw "Installer script not found: $Ps1Path" }
if (-not $OutPath) { $OutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'TSS-MDM-Installer.exe' }

if (-not (Get-Module -ListAvailable -Name ps2exe)) {
  Write-Host 'Installing ps2exe module (first time only)...' -ForegroundColor Yellow
  try {
    if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) {
      Install-PackageProvider -Name NuGet -Force -Scope CurrentUser | Out-Null
    }
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue
    Install-Module ps2exe -Scope CurrentUser -Force -AllowClobber
  } catch {
    throw "Could not install ps2exe automatically. On an online PC run: Save-Module ps2exe -Path <usb>, copy it over, then Import-Module ps2exe.`n$_"
  }
}
Import-Module ps2exe

$opt = @{
  inputFile    = $Ps1Path
  outputFile   = $OutPath
  noConsole    = $false
  requireAdmin = $true
  title        = 'TSS MDM Installer'
  company      = 'The Souled Store'
  product      = 'TSS MDM Agent'
  version      = '0.2.0'
}
if ($IconPath -and (Test-Path $IconPath)) { $opt.iconFile = $IconPath }
# Bake server/token into the exe so it runs with no prompts
$extra = @()
if ($Server) { $extra += "-Server `"$Server`"" }
if ($Token)  { $extra += "-Token `"$Token`"" }
if ($extra.Count) { $opt.arguments = ($extra -join ' ') }

Write-Host "Building $OutPath ..."
Invoke-ps2exe @opt
if (-not (Test-Path $OutPath)) { throw 'Build did not produce the .exe.' }

if ($PfxPath) {
  if (-not (Test-Path $PfxPath)) { throw "PFX not found: $PfxPath" }
  $st = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if (-not $st) { throw 'signtool.exe not found (install the Windows SDK) - the .exe is built but unsigned.' }
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword))
  & $st.Source sign /f $PfxPath /p $plain /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $OutPath
  if ($LASTEXITCODE -ne 0) { throw 'Signing failed.' }
  Write-Host 'Signed.' -ForegroundColor Green
}

Write-Host ''
Write-Host "Done: $OutPath" -ForegroundColor Green
Write-Host 'Double-click it on any Windows PC -> approve UAC -> enter server URL + token -> device enrolls.'
