from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AdminUser, aware, utcnow
from ..security import (audit, create_access_token, get_current_user, hash_password, password_problems,
                        verify_password)
from ..serializers import user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.scalar(select(AdminUser).where(AdminUser.email == email))
    generic = HTTPException(401, "Invalid email or password")
    if not user or not user.is_active:
        audit(db, request, email, "auth.login_failed", reason="unknown_or_disabled")
        db.commit()
        raise generic
    # Lockout is disabled when MDM_MAX_FAILED_LOGINS <= 0.
    lockout_on = settings.MAX_FAILED_LOGINS > 0
    if lockout_on and user.locked_until and aware(user.locked_until) > utcnow():
        raise HTTPException(423, "Account temporarily locked after too many failed attempts. Try again later.")
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        if lockout_on and user.failed_logins >= settings.MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
            user.failed_logins = 0
            audit(db, request, email, "auth.locked", "admin_user", user.id)
        audit(db, request, email, "auth.login_failed", "admin_user", user.id, reason="bad_password")
        db.commit()
        raise generic
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    audit(db, request, email, "auth.login", "admin_user", user.id)
    db.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": user_out(user)}


@router.get("/me")
def me(user: AdminUser = Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, db: Session = Depends(get_db),
                    user: AdminUser = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(400, "New password must be different")
    if problems := password_problems(body.new_password):
        raise HTTPException(400, "Password needs " + ", ".join(problems))
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1  # sign out other sessions
    audit(db, request, user.email, "auth.password_changed", "admin_user", user.id)
    db.commit()
    return {"access_token": create_access_token(user), "user": user_out(user)}


@router.post("/logout-all")
def logout_all(request: Request, db: Session = Depends(get_db), user: AdminUser = Depends(get_current_user)):
    user.token_version += 1
    audit(db, request, user.email, "auth.logout_all", "admin_user", user.id)
    db.commit()
    return {"ok": True}
