import io
import secrets
from datetime import timedelta

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import PLATFORMS, AdminUser, DeviceGroup, EnrollmentToken, utcnow
from ..security import audit, require_role
from ..serializers import token_out

router = APIRouter(tags=["enrollment"])


class TokenIn(BaseModel):
    name: str
    platform: str | None = None
    ownership: str = "corporate"
    assigned_user: str | None = None
    group_id: int | None = None
    max_uses: int | None = Field(default=None, ge=1)
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


def _get(db, token_id) -> EnrollmentToken:
    t = db.get(EnrollmentToken, token_id)
    if not t:
        raise HTTPException(404, "Enrollment token not found")
    return t


@router.get("/api/enrollment/tokens")
def list_tokens(db: Session = Depends(get_db), _=Depends(require_role("technician"))):
    return [token_out(t) for t in db.scalars(select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()))]


@router.post("/api/enrollment/tokens", status_code=201)
def create_token(body: TokenIn, request: Request, db: Session = Depends(get_db),
                 me: AdminUser = Depends(require_role("technician"))):
    if body.platform and body.platform not in PLATFORMS:
        raise HTTPException(400, f"Platform must be one of {PLATFORMS}")
    if body.ownership not in ("corporate", "byod"):
        raise HTTPException(400, "Ownership must be corporate or byod")
    if body.group_id and not db.get(DeviceGroup, body.group_id):
        raise HTTPException(400, "Group not found")
    t = EnrollmentToken(
        token=secrets.token_urlsafe(18), name=body.name.strip() or "Enrollment", platform=body.platform,
        ownership=body.ownership, assigned_user=(body.assigned_user or None), group_id=body.group_id,
        max_uses=body.max_uses, created_by=me.email,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(t)
    db.flush()
    audit(db, request, me.email, "enrollment.token_created", "enrollment_token", t.id, name=t.name)
    db.commit()
    db.refresh(t)
    return token_out(t)


@router.post("/api/enrollment/tokens/{token_id}/revoke")
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_db),
                 me: AdminUser = Depends(require_role("admin"))):
    t = _get(db, token_id)
    t.revoked = True
    audit(db, request, me.email, "enrollment.token_revoked", "enrollment_token", t.id)
    db.commit()
    return token_out(t)


@router.get("/api/enrollment/tokens/{token_id}/qr")
def token_qr(token_id: int, db: Session = Depends(get_db), _=Depends(require_role("technician"))):
    t = _get(db, token_id)
    img = qrcode.make(f"{settings.PUBLIC_URL}/enroll/{t.token}", box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@router.get("/enroll/{token}", response_class=HTMLResponse)
def enroll_landing(token: str, db: Session = Depends(get_db)):
    """Public page a user lands on after scanning the QR code: shows per-platform steps."""
    t = db.scalar(select(EnrollmentToken).where(EnrollmentToken.token == token))
    if not t or not t.is_usable():
        return HTMLResponse("<h2 style='font-family:sans-serif'>This enrollment link is invalid or has expired.</h2>"
                            "<p style='font-family:sans-serif'>Ask your IT team for a new one.</p>", status_code=404)
    url = settings.PUBLIC_URL
    ps = (f"powershell -ExecutionPolicy Bypass -Command \"iwr {url}/downloads/tss-agent.ps1 -OutFile $env:TEMP\\tss-agent.ps1; "
          f"& $env:TEMP\\tss-agent.ps1 -Server {url} -Token {t.token} -Install\"")
    py = f"curl -fsSLO {url}/downloads/tss_agent.py && sudo python3 tss_agent.py --server {url} --token {t.token} --install"
    return HTMLResponse(f"""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Enroll device · {settings.APP_NAME}</title><style>body{{font-family:system-ui,sans-serif;max-width:680px;margin:32px auto;padding:0 16px;color:#1d2333}}
code,pre{{background:#f1f3f8;border-radius:6px;padding:10px;display:block;white-space:pre-wrap;word-break:break-all;font-size:13px}}
h1{{font-size:22px}} .card{{border:1px solid #e3e6ee;border-radius:10px;padding:16px;margin:12px 0}}</style></head><body>
<h1>Enroll this device in {settings.APP_NAME}</h1><p>Enrollment profile: <b>{t.name}</b> ({t.ownership})</p>
<div class=card><h3>Windows 10/11</h3><p>Open PowerShell <b>as Administrator</b> and run:</p><code>{ps}</code></div>
<div class=card><h3>macOS / Linux</h3><p>Open Terminal and run:</p><code>{py}</code></div>
<div class=card><h3>Android</h3><p>The TSS Agent app (Android Enterprise) is part of the next release. Your enrollment code is:</p><code>{t.token}</code></div>
<div class=card><h3>iPhone / iPad</h3><p>Apple enrollment needs an APNs certificate and Apple Business Manager; it is part of the next release.</p></div>
</body></html>""")
