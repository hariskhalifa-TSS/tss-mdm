from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ROLES, AdminUser
from ..security import audit, generate_password, hash_password, require_role
from ..serializers import user_out

router = APIRouter(prefix="/api/users", tags=["admin users"])
SUPER = require_role("super_admin")


class UserIn(BaseModel):
    email: str
    name: str
    role: str = "viewer"


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None


def _active_super_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AdminUser)
                     .where(AdminUser.role == "super_admin", AdminUser.is_active.is_(True)))


@router.get("")
def list_users(db: Session = Depends(get_db), _=Depends(SUPER)):
    return [user_out(u) for u in db.scalars(select(AdminUser).order_by(AdminUser.created_at))]


@router.post("", status_code=201)
def create_user(body: UserIn, request: Request, db: Session = Depends(get_db), me: AdminUser = Depends(SUPER)):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    if body.role not in ROLES:
        raise HTTPException(400, f"Role must be one of {ROLES}")
    if db.scalar(select(AdminUser).where(AdminUser.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    temp = generate_password()
    u = AdminUser(email=email, name=body.name.strip() or email, role=body.role,
                  password_hash=hash_password(temp), must_change_password=True)
    db.add(u)
    db.flush()
    audit(db, request, me.email, "user.created", "admin_user", u.id, email=email, role=body.role)
    db.commit()
    return {"user": user_out(u), "temporary_password": temp}


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db),
                me: AdminUser = Depends(SUPER)):
    u = db.get(AdminUser, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    demoting = (body.role and body.role != "super_admin") or body.is_active is False
    if u.role == "super_admin" and demoting and _active_super_count(db) <= 1:
        raise HTTPException(400, "Cannot remove the last active super admin")
    changes = body.model_dump(exclude_none=True)
    if "role" in changes and changes["role"] not in ROLES:
        raise HTTPException(400, f"Role must be one of {ROLES}")
    for k, v in changes.items():
        setattr(u, k, v)
    if "role" in changes or changes.get("is_active") is False:
        u.token_version += 1
    audit(db, request, me.email, "user.updated", "admin_user", u.id, **changes)
    db.commit()
    return user_out(u)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, db: Session = Depends(get_db), me: AdminUser = Depends(SUPER)):
    u = db.get(AdminUser, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    temp = generate_password()
    u.password_hash = hash_password(temp)
    u.must_change_password = True
    u.failed_logins = 0
    u.locked_until = None
    u.token_version += 1
    audit(db, request, me.email, "user.password_reset", "admin_user", u.id)
    db.commit()
    return {"temporary_password": temp}


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), me: AdminUser = Depends(SUPER)):
    u = db.get(AdminUser, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == me.id:
        raise HTTPException(400, "You cannot delete your own account")
    if u.role == "super_admin" and _active_super_count(db) <= 1:
        raise HTTPException(400, "Cannot delete the last super admin")
    audit(db, request, me.email, "user.deleted", "admin_user", u.id, email=u.email)
    db.delete(u)
    db.commit()
