from datetime import timedelta

from .config import settings
from .models import AdminUser, Device, DeviceGroup, EnrollmentToken, aware, utcnow


def iso(dt):
    dt = aware(dt)
    return dt.isoformat() if dt else None


def is_online(d: Device) -> bool:
    seen = aware(d.last_seen_at)
    return bool(seen and utcnow() - seen < timedelta(minutes=settings.OFFLINE_AFTER_MINUTES))


def user_out(u: AdminUser) -> dict:
    return {
        "id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active,
        "must_change_password": u.must_change_password, "last_login_at": iso(u.last_login_at),
        "locked": bool(u.locked_until and aware(u.locked_until) > utcnow()), "created_at": iso(u.created_at),
    }


def group_out(g: DeviceGroup, count: int | None = None) -> dict:
    return {"id": g.id, "name": g.name, "description": g.description, "created_at": iso(g.created_at),
            "device_count": count if count is not None else len(g.devices)}


def token_out(t: EnrollmentToken) -> dict:
    return {
        "id": t.id, "token": t.token, "name": t.name, "platform": t.platform, "ownership": t.ownership,
        "assigned_user": t.assigned_user, "group_id": t.group_id, "group_name": t.group.name if t.group else None,
        "max_uses": t.max_uses, "uses": t.uses, "expires_at": iso(t.expires_at), "revoked": t.revoked,
        "usable": t.is_usable(), "created_by": t.created_by, "created_at": iso(t.created_at),
    }


def device_out(d: Device, full: bool = False) -> dict:
    out = {
        "id": d.id, "name": d.name, "platform": d.platform, "os_version": d.os_version,
        "manufacturer": d.manufacturer, "model": d.model, "serial_number": d.serial_number,
        "ownership": d.ownership, "assigned_user": d.assigned_user, "group_id": d.group_id,
        "group_name": d.group.name if d.group else None, "status": d.status, "online": is_online(d),
        "enrolled_at": iso(d.enrolled_at), "last_seen_at": iso(d.last_seen_at),
        "battery_level": d.battery_level, "storage_total_gb": d.storage_total_gb,
        "storage_free_gb": d.storage_free_gb, "ip_address": d.ip_address,
    }
    if full:
        out.update({
            "hardware_id": d.hardware_id, "imei": d.imei, "agent_version": d.agent_version,
            "is_charging": d.is_charging, "ram_total_gb": d.ram_total_gb, "cpu": d.cpu,
            "mac_address": d.mac_address, "wifi_ssid": d.wifi_ssid, "latitude": d.latitude,
            "longitude": d.longitude, "is_encrypted": d.is_encrypted, "passcode_set": d.passcode_set,
            "is_rooted": d.is_rooted, "installed_apps": d.installed_apps or [], "extra": d.extra or {},
            "notes": d.notes,
        })
    return out
