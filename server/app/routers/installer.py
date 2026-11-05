"""Per-token installer downloads for IT to deploy on company-owned devices.

The generated files embed the server URL + enrollment token, so IT just runs one file:
  - Windows: a self-elevating .bat (double-click -> UAC prompt -> enrolls + installs SYSTEM task)
  - macOS:   a .command (double-click -> prompts for password -> installs launchd daemon)
Both fetch the current agent from /downloads, so agent updates propagate without rebuilding.
A true signed .exe is produced from the .bat by installer/build-windows-exe.ps1 (run on Windows).
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import EnrollmentToken
from ..security import require_role

router = APIRouter(prefix="/api/enrollment/tokens", tags=["installer"])


def _token(db: Session, token_id: int) -> EnrollmentToken:
    t = db.get(EnrollmentToken, token_id)
    if not t:
        raise HTTPException(404, "Enrollment token not found")
    if not t.is_usable():
        raise HTTPException(400, "This token is expired, revoked or used up — create a new one")
    return t


def _safe(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")
    return keep or "TSS-MDM"


def windows_bat(server: str, token: str, name: str) -> str:
    """Self-elevating batch installer. CRLF line endings added by the response."""
    return r"""@echo off
setlocal
title TSS MDM Agent Setup
set "SERVER=__SERVER__"
set "TOKEN=__TOKEN__"

rem --- self-elevate to Administrator (MDM enrollment needs admin rights) ---
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo Requesting administrator permission...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)

echo.
echo   Enrolling this device into TSS MDM
echo   Server: %SERVER%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { $o=Join-Path $env:TEMP 'tss-agent.ps1'; Invoke-WebRequest -UseBasicParsing '%SERVER%/downloads/tss-agent.ps1' -OutFile $o; & $o -Server '%SERVER%' -Token '%TOKEN%' -Install } catch { Write-Host ('ERROR: ' + $_) -ForegroundColor Red; exit 1 }"

if %errorLevel% neq 0 (
  echo.
  echo   Setup failed. Check that this PC can reach %SERVER% and try again.
) else (
  echo.
  echo   Done. This device is now managed by TSS MDM and will check in automatically.
)
echo.
pause
""".replace("__SERVER__", server).replace("__TOKEN__", token)


def macos_command(server: str, token: str, name: str) -> str:
    return f"""#!/bin/bash
# TSS MDM agent setup for macOS — double-click to run.
set -e
SERVER="{server}"
TOKEN="{token}"
echo
echo "  Enrolling this Mac into TSS MDM ($SERVER)"
echo "  You'll be asked for your password to install the background agent."
echo
curl -fsSL "$SERVER/downloads/tss_agent.py" -o /tmp/tss_agent.py
sudo python3 /tmp/tss_agent.py --server "$SERVER" --token "$TOKEN" --install
echo
echo "  Done. This Mac is now managed by TSS MDM."
read -n1 -r -p "Press any key to close..."
"""


@router.get("/{token_id}/installer")
def download_installer(token_id: int, os: str = "windows", db: Session = Depends(get_db),
                       _=Depends(require_role("technician"))):
    t = _token(db, token_id)
    server = settings.PUBLIC_URL
    base = _safe(t.name)
    if os == "windows":
        body = windows_bat(server, t.token, t.name).replace("\n", "\r\n")
        fname = f"TSS-MDM-Setup-{base}.bat"
    elif os in ("macos", "mac"):
        body = macos_command(server, t.token, t.name)
        fname = f"TSS-MDM-Setup-{base}.command"
    else:
        raise HTTPException(400, "os must be 'windows' or 'macos'")
    return PlainTextResponse(body, media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
