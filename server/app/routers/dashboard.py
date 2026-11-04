from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuditLog, Device, utcnow
from ..security import require_role
from ..serializers import iso

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(require_role("viewer"))):
    active = select(Device).where(Device.status == "active").subquery()
    cutoff = utcnow() - timedelta(minutes=settings.OFFLINE_AFTER_MINUTES)
    total = db.scalar(select(func.count()).select_from(active))
    online = db.scalar(select(func.count()).select_from(active).where(active.c.last_seen_at >= cutoff))
    by_platform = dict(db.execute(select(active.c.platform, func.count()).group_by(active.c.platform)).all())
    by_ownership = dict(db.execute(select(active.c.ownership, func.count()).group_by(active.c.ownership)).all())
    low_battery = db.scalar(select(func.count()).select_from(active).where(active.c.battery_level < 20))
    low_storage = db.scalar(select(func.count()).select_from(active).where(active.c.storage_free_gb < 5))
    unencrypted = db.scalar(select(func.count()).select_from(active).where(active.c.is_encrypted.is_(False)))
    retired = db.scalar(select(func.count()).select_from(Device).where(Device.status == "retired"))

    # enrollments per day, last 14 days
    since = utcnow() - timedelta(days=13)
    days = {(since + timedelta(days=i)).date().isoformat(): 0 for i in range(14)}
    for (dt,) in db.execute(select(Device.enrolled_at).where(Device.enrolled_at >= since.replace(hour=0, minute=0))):
        key = dt.date().isoformat()
        if key in days:
            days[key] += 1
    return {
        "total": total, "online": online, "offline": total - online, "retired": retired,
        "by_platform": by_platform, "by_ownership": by_ownership,
        "alerts": {"low_battery": low_battery, "low_storage": low_storage, "unencrypted": unencrypted},
        "enrollments_14d": [{"date": k, "count": v} for k, v in days.items()],
    }


@router.get("/audit")
def audit_log(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500), action: str | None = None,
              actor: str | None = None, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(AuditLog.actor.like(f"%{actor}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditLog.at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"total": total, "items": [{"id": a.id, "at": iso(a.at), "actor": a.actor, "action": a.action,
                                       "target_type": a.target_type, "target_id": a.target_id,
                                       "details": a.details, "ip": a.ip} for a in rows]}
