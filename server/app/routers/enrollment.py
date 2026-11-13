from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..enroll_key import generate_key
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
        raise HTTPException(404, "Enrollment key not found")
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

    # Keys are short enough that a collision, while very unlikely, is worth retrying.
    for attempt in range(5):
        t = EnrollmentToken(
            token=generate_key(), name=body.name.strip() or "Enrollment", platform=body.platform,
            ownership=body.ownership, assigned_user=(body.assigned_user or None), group_id=body.group_id,
            max_uses=body.max_uses, created_by=me.email,
            expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
        )
        db.add(t)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
    else:
        raise HTTPException(500, "Could not allocate an enrollment key; try again")

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
