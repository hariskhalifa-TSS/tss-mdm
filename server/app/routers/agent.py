"""Endpoints called by device agents (not by admins)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import PLATFORMS, Device, DeviceCommand, EnrollmentToken, utcnow
from ..security import audit, client_ip, get_current_device, hash_secret, new_device_secret

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Inventory fields an agent may report. Anything else goes into `extra`.
INVENTORY_FIELDS = {
    "name", "os_version", "manufacturer", "model", "serial_number", "hardware_id", "imei", "agent_version",
    "battery_level", "is_charging", "storage_total_gb", "storage_free_gb", "ram_total_gb", "cpu", "ip_address",
    "mac_address", "wifi_ssid", "latitude", "longitude", "is_encrypted", "passcode_set", "is_rooted",
}


class Inventory(BaseModel):
    model_config = {"extra": "allow"}
    name: str | None = Field(default=None, max_length=255)
    os_version: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    hardware_id: str | None = None
    imei: str | None = None
    agent_version: str | None = None
    battery_level: float | None = Field(default=None, ge=0, le=100)
    is_charging: bool | None = None
    storage_total_gb: float | None = Field(default=None, ge=0)
    storage_free_gb: float | None = Field(default=None, ge=0)
    ram_total_gb: float | None = Field(default=None, ge=0)
    cpu: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    wifi_ssid: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_encrypted: bool | None = None
    passcode_set: bool | None = None
    is_rooted: bool | None = None
    installed_apps: list[dict] | None = None


class EnrollIn(BaseModel):
    token: str
    platform: str
    inventory: Inventory


class CheckinIn(BaseModel):
    inventory: Inventory | None = None
    command_results: list[dict] = []  # [{id, status: succeeded|failed, result}]


def _apply_inventory(device: Device, inv: Inventory, request: Request):
    data = inv.model_dump(exclude_none=True)
    apps = data.pop("installed_apps", None)
    extra = {k: v for k, v in data.items() if k not in INVENTORY_FIELDS}
    for k in INVENTORY_FIELDS & data.keys():
        val = data[k]
        setattr(device, k, val[:255] if isinstance(val, str) else val)
    if apps is not None:
        device.installed_apps = apps[:2000]
    if extra:
        device.extra = {**(device.extra or {}), **extra}
    if not device.ip_address:
        device.ip_address = client_ip(request)
    device.extra = {**(device.extra or {}), "public_ip": client_ip(request)}


@router.post("/enroll", status_code=201)
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_db)):
    platform = body.platform.lower()
    if platform not in PLATFORMS:
        raise HTTPException(400, f"Unsupported platform. Use one of {PLATFORMS}")
    t = db.scalar(select(EnrollmentToken).where(EnrollmentToken.token == body.token).with_for_update())
    if not t or not t.is_usable():
        raise HTTPException(403, "Enrollment token is invalid, expired, revoked or used up")
    if t.platform and t.platform != platform:
        raise HTTPException(403, f"This token only enrolls {t.platform} devices")

    inv = body.inventory
    # Re-enrollment: same hardware id on same platform replaces the old record's credentials.
    device = None
    if inv.hardware_id:
        device = db.scalar(select(Device).where(Device.hardware_id == inv.hardware_id, Device.platform == platform))
    secret = new_device_secret()
    if device:
        device.status = "active"
        action = "device.re_enrolled"
    else:
        device = Device(platform=platform, name=inv.name or f"{platform}-device", secret_hash="")
        db.add(device)
        action = "device.enrolled"
    device.secret_hash = hash_secret(secret)
    device.ownership = t.ownership
    device.assigned_user = t.assigned_user
    device.group_id = t.group_id
    device.enrollment_token_id = t.id
    device.enrolled_at = utcnow()
    device.last_seen_at = utcnow()
    _apply_inventory(device, inv, request)
    t.uses += 1
    db.flush()
    audit(db, request, f"device:{device.id}", action, "device", device.id, token=t.name, platform=platform)
    db.commit()
    return {"device_id": device.id, "device_secret": secret,
            "checkin_interval_seconds": settings.CHECKIN_INTERVAL_SECONDS}


@router.post("/checkin")
def checkin(body: CheckinIn, request: Request, db: Session = Depends(get_db),
            device: Device = Depends(get_current_device)):
    if body.inventory:
        _apply_inventory(device, body.inventory, request)
    device.last_seen_at = utcnow()
    for r in body.command_results:
        cmd = db.get(DeviceCommand, r.get("id"))
        if cmd and cmd.device_id == device.id and r.get("status") in ("succeeded", "failed"):
            cmd.status = r["status"]
            cmd.result = str(r.get("result", ""))[:4000]
    pending = list(db.scalars(select(DeviceCommand).where(DeviceCommand.device_id == device.id,
                                                          DeviceCommand.status == "pending")))
    for c in pending:
        c.status = "sent"
    db.commit()
    return {"commands": [{"id": c.id, "command": c.command, "payload": c.payload} for c in pending],
            "checkin_interval_seconds": settings.CHECKIN_INTERVAL_SECONDS,
            "latest_agent_version": settings.AGENT_VERSION}
