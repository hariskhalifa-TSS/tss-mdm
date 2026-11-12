@echo off
setlocal EnableDelayedExpansion
title TSS MDM Agent Setup

rem ====================================================================
rem  TSS MDM - GENERAL device enrollment installer (any Windows PC).
rem  Asks for the server URL + an enrollment token, then enrolls.
rem  This file is meant to be built into TSS-MDM-Installer.exe.
rem ====================================================================

if not defined SERVER set /p "SERVER=Enter your TSS MDM server URL (e.g. https://your-app.up.railway.app): "
if not defined TOKEN  set /p "TOKEN=Enter the enrollment token: "

rem --- trim spaces and a trailing slash ---
for /f "tokens=* delims= " %%a in ("%SERVER%") do set "SERVER=%%a"
for /f "tokens=* delims= " %%a in ("%TOKEN%") do set "TOKEN=%%a"
if "%SERVER:~-1%"=="/" set "SERVER=%SERVER:~0,-1%"

rem --- force https:// (http gets redirected and breaks enrollment) ---
echo %SERVER% | findstr /i "^https://" >nul
if %errorLevel% neq 0 (
  echo %SERVER% | findstr /i "^http://" >nul
  if !errorLevel!==0 ( set "SERVER=https://!SERVER:~7!" ) else ( set "SERVER=https://!SERVER!" )
)

rem --- reject localhost: it means THIS PC, not your server ---
echo %SERVER% | findstr /i "localhost 127.0.0.1" >nul
if %errorLevel%==0 (
  echo.
  echo   That URL points at THIS computer, not your MDM server.
  echo   Use your server address, e.g. https://your-app.up.railway.app
  echo.
  pause
  exit /b 1
)

rem --- self-elevate to Administrator (enrollment installs a system service) ---
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo Requesting administrator permission...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%SERVER%','%TOKEN%'"
  exit /b
)
if not "%~1"=="" set "SERVER=%~1"
if not "%~2"=="" set "TOKEN=%~2"

echo.
echo   Checking connection to %SERVER% ...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { $r=Invoke-WebRequest -UseBasicParsing '%SERVER%/api/health' -TimeoutSec 15; if ($r.Content -match 'ok') { exit 0 } else { exit 2 } } catch { exit 1 }"
if %errorLevel% neq 0 (
  echo.
  echo   Could not reach the MDM server at %SERVER%
  echo   - Check the address ^(open %SERVER% in a browser^)
  echo   - Check this PC has internet access
  echo.
  pause
  exit /b 1
)

echo   Connected. Enrolling this device...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { $o=Join-Path $env:TEMP 'tss-agent.ps1'; Invoke-WebRequest -UseBasicParsing '%SERVER%/downloads/tss-agent.ps1' -OutFile $o; & $o -Server '%SERVER%' -Token '%TOKEN%' -Install } catch { Write-Host ('ERROR: ' + $_) -ForegroundColor Red; exit 1 }"

if %errorLevel% neq 0 (
  echo.
  echo   Setup failed. Make sure the enrollment token is Active and not expired.
) else (
  echo.
  echo   Done. This device is now enrolled and will appear under Devices in your dashboard.
)
echo.
pause
