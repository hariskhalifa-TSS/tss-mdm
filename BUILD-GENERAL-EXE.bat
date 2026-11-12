@echo off
setlocal
title TSS MDM - Build general EXE
cd /d "%~dp0"

echo.
echo   ==================================================
echo    TSS MDM  -  Build the GENERAL installer .exe
echo   ==================================================
echo.
echo   Produces TSS-MDM-Installer.exe on your Desktop.
echo   The server address is baked in, so on any Windows PC
echo   the user only approves UAC and types the enrollment key.
echo.
echo   (First run downloads a small build tool - this PC
echo    needs an internet connection.)
echo.

rem --- the MDM server address baked into the .exe ---
set "MDM_SERVER=https://tss-mdm-production.up.railway.app"
echo   Server baked into the installer: %MDM_SERVER%
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build-windows-exe.ps1" -Server "%MDM_SERVER%"

echo.
echo   If it said "Done", your installer is on the Desktop: TSS-MDM-Installer.exe
echo.
pause
