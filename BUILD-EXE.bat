@echo off
setlocal EnableDelayedExpansion
title TSS MDM - Build EXE
cd /d "%~dp0"

echo.
echo   ================================================
echo    TSS MDM  -  Build a .exe installer
echo   ================================================
echo.
echo   This turns a downloaded TSS-MDM-Setup .bat into a
echo   double-click .exe you can hand to IT.
echo.

rem --- find a TSS-MDM-Setup*.bat: here, then Downloads ---
set "SRC="
for %%F in ("%~dp0TSS-MDM-Setup*.bat") do set "SRC=%%~fF"
if not defined SRC for %%F in ("%USERPROFILE%\Downloads\TSS-MDM-Setup*.bat") do set "SRC=%%~fF"

if not defined SRC (
  echo   I couldn't find a TSS-MDM-Setup .bat automatically.
  echo   Download the Windows installer from your console first
  echo   ^(Enrollment - open a token - Windows installer^),
  echo   then either put it in this folder or paste its full path below.
  echo.
  set /p "SRC=Full path to the .bat (or just press Enter to cancel): "
)

if not defined SRC goto :done
if not exist "!SRC!" (
  echo   File not found: !SRC!
  goto :done
)

echo.
echo   Using installer: !SRC!
echo   Building .exe (first run downloads a small build tool)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build-windows-exe.ps1" -BatPath "!SRC!"

:done
echo.
pause
