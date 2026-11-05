import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import ROLES, AdminUser, AuditLog, Device, DeviceCommand, DeviceGroup, utcnow
from ..security import audit, require_role
from ..serializers import device_out, group_out, iso

router = APIRouter(prefix="/api", tags=["devices"])

# Commands the queue accepts today. Agents report "unsupported" for ones they can't run.
ALLOWED_COMMANDS = {"refresh_inventory", "send_message", "lock", "reboot", "shutdown"}
# Destructive actions that require admin role (not just technician).
SENSITIVE_COMMANDS = {"reboot", "shutdown"}


class DeviceUpdate(BaseModel):
    name: str | None = None
    assigned_user: str | None = None
    group_id: int | None = None
    ownership: str | None = None
    notes: str | None = None


class CommandIn(BaseModel):
    command: str
    payload: dict = {}


class GroupIn(BaseModel):
    name: str
    description: str = ""


def _device(db, device_id) -> Device:
    d = db.get(Device, device_id)
    if not d:
        raise HTTPException(404, "Device not found")
    return d


def _query(platform, status, group_id, online, q):
    stmt = select(Device)
    if platform:
        stmt = stmt.where(Device.platform == platform)
    if status:
        stmt = stmt.where(Device.status == status)
    if group_id:
        stmt = stmt.where(Device.group_id == group_id)
    cutoff = utcnow() - timedelta(minutes=settings.OFFLINE_AFTER_MINUTES)
    if online is True:
        stmt = stmt.where(Device.last_seen_at >= cutoff)
    elif online is False:
        stmt = stmt.where(or_(Device.last_seen_at < cutoff, Device.last_seen_at.is_(None)))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Device.name).like(like), func.lower(Device.serial_number).like(like),
                              func.lower(Device.model).like(like), func.lower(Device.assigned_user).like(like),
                              func.lower(Device.ip_address).like(like)))
    return stmt


@router.get("/devices")
def list_devices(platform: str | None = None, status: str | None = None, group_id: int | None = None,
                 online: bool | None = None, q: str | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                 db: Session = Depends(get_db), _=Depends(require_role("viewer"))):
    stmt = _query(platform, status, group_id, online, q)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Device.last_seen_at.desc().nullslast())
                      .offset((page - 1) * page_size).limit(page_size))
    return {"total": total, "page": page, "page_size": page_size, "items": [device_out(d) for d in rows]}


@router.get("/devices/export.csv")
def export_devices(platform: str | None = None, status: str | None = None, group_id: int | None = None,
                   q: str | None = None, db: Session = Depends(get_db), _=Depends(require_role("viewer"))):
    rows = [device_out(d) for d in db.scalars(_query(platform, status, group_id, None, q))]
    buf = io.StringIO()
    cols = ["id", "name", "platform", "os_version", "manufacturer", "model", "serial_number", "ownership",
            "assigned_user", "group_name", "status", "online", "battery_level", "storage_free_gb",
            "storage_total_gb", "ip_address", "enrolled_at", "last_seen_at"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=devices.csv"})


@router.get("/devices/{device_id}")
def get_device(device_id: str, db: Session = Depends(get_db), _=Depends(require_role("viewer"))):
    d = _device(db, device_id)
    out = device_out(d, full=True)
    cmds = db.scalars(select(DeviceCommand).where(DeviceCommand.device_id == d.id)
                      .order_by(DeviceCommand.created_at.desc()).limit(20))
    out["commands"] = [{"id": c.id, "command": c.command, "payload": c.payload, "status": c.status,
                        "result": c.result, "created_by": c.created_by, "created_at": iso(c.created_at)} for c in cmds]
    logs = db.scalars(select(AuditLog).where(AuditLog.target_type == "device", AuditLog.target_id == d.id)
                      .order_by(AuditLog.at.desc()).limit(20))
    out["activity"] = [{"at": iso(a.at), "actor": a.actor, "action": a.action, "details": a.details} for a in logs]
    return out


@router.patch("/devices/{device_id}")
def update_device(device_id: str, body: DeviceUpdate, request: Request, db: Session = Depends(get_db),
                  me: AdminUser = Depends(require_role("technician"))):
    d = _device(db, device_id)
    changes = body.model_dump(exclude_unset=True)
    if "group_id" in changes and changes["group_id"] and not db.get(DeviceGroup, changes["group_id"]):
        raise HTTPException(400, "Group not found")
    if "ownership" in changes and changes["ownership"] not in ("corporate", "byod"):
        raise HTTPException(400, "Ownership must be corporate or byod")
    for k, v in changes.items():
        setattr(d, k, v)
    audit(db, request, me.email, "device.updated", "device", d.id, **changes)
    db.commit()
    db.refresh(d)
    return device_out(d, full=True)


@router.post("/devices/{device_id}/commands", status_code=201)
def queue_command(device_id: str, body: CommandIn, request: Request, db: Session = Depends(get_db),
                  me: AdminUser = Depends(require_role("technician"))):
    d = _device(db, device_id)
    if d.status != "active":
        raise HTTPException(400, "Device is retired")
    if body.command not in ALLOWED_COMMANDS:
        raise HTTPException(400, f"Command must be one of {sorted(ALLOWED_COMMANDS)}")
    if body.command in SENSITIVE_COMMANDS and ROLES.index(me.role) < ROLES.index("admin"):
        raise HTTPException(403, f"'{body.command}' requires admin role")
    c = DeviceCommand(device_id=d.id, command=body.command, payload=body.payload, created_by=me.email)
    db.add(c)
    db.flush()
    audit(db, request, me.email, "device.command_queued", "device", d.id, command=body.command)
    db.commit()
    return {"id": c.id, "status": c.status}


@router.post("/devices/{device_id}/retire")
def retire_device(device_id: str, request: Request, db: Session = Depends(get_db),
                  me: AdminUser = Depends(require_role("admin"))):
    d = _device(db, device_id)
    d.status = "retired"
    audit(db, request, me.email, "device.retired", "device", d.id, name=d.name)
    db.commit()
    return device_out(d)


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(device_id: str, request: Request, db: Session = Depends(get_db),
                  me: AdminUser = Depends(require_role("admin"))):
    d = _device(db, device_id)
    audit(db, request, me.email, "device.deleted", "device", d.id, name=d.name, serial=d.serial_number)
    db.delete(d)
    db.commit()


# ---------------- groups ----------------
@router.get("/groups")
def list_groups(db: Session = Depends(get_db), _=Depends(require_role("viewer"))):
    counts = dict(db.execute(select(Device.group_id, func.count()).group_by(Device.group_id)).all())
    return [group_out(g, counts.get(g.id, 0)) for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, request: Request, db: Session = Depends(get_db),
                 me: AdminUser = Depends(require_role("admin"))):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if db.scalar(select(DeviceGroup).where(DeviceGroup.name == name)):
        raise HTTPException(409, "Group already exists")
    g = DeviceGroup(name=name, description=body.description)
    db.add(g)
    db.flush()
    audit(db, request, me.email, "group.created", "group", g.id, name=name)
    db.commit()
    return group_out(g, 0)


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: int, request: Request, db: Session = Depends(get_db),
                 me: AdminUser = Depends(require_role("admin"))):
    g = db.get(DeviceGroup, group_id)
    if not g:
        raise HTTPException(404, "Group not found")
    for d in g.devices:
        d.group_id = None
    audit(db, request, me.email, "group.deleted", "group", g.id, name=g.name)
    db.delete(g)
    db.commit()
