import hashlib
import hmac
import secrets
from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import ROLES, AdminUser, AuditLog, Device, utcnow

ALGO = "HS256"


# ---------- passwords ----------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def password_problems(pw: str) -> list[str]:
    issues = []
    if len(pw) < 12:
        issues.append("at least 12 characters")
    if not any(c.isupper() for c in pw):
        issues.append("an uppercase letter")
    if not any(c.islower() for c in pw):
        issues.append("a lowercase letter")
    if not any(c.isdigit() for c in pw):
        issues.append("a number")
    if not any(not c.isalnum() for c in pw):
        issues.append("a symbol")
    return issues


def generate_password() -> str:
    # Guaranteed to satisfy the policy
    core = secrets.token_urlsafe(14)
    return f"Tss-{core}-9a!"


# ---------- admin JWT ----------
def create_access_token(user: AdminUser) -> str:
    now = utcnow()
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "tv": user.token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGO)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")
    user = db.get(AdminUser, int(payload["sub"]))
    if not user or not user.is_active or payload.get("tv") != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session no longer valid")
    return user


def require_role(min_role: str, allow_pending_password: bool = False):
    """Dependency: the user's role must be >= min_role in the hierarchy."""
    needed = ROLES.index(min_role)

    def dep(user: AdminUser = Depends(get_current_user)) -> AdminUser:
        if user.must_change_password and not allow_pending_password:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")
        if ROLES.index(user.role) < needed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_role} role")
        return user

    return dep


# ---------- device credentials ----------
def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def get_current_device(request: Request, db: Session = Depends(get_db)) -> Device:
    """Agents authenticate with:  Authorization: Device <device_id>:<device_secret>"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Device ") or ":" not in auth:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Device credentials required")
    device_id, secret = auth[7:].split(":", 1)
    device = db.get(Device, device_id)
    if not device or not hmac.compare_digest(device.secret_hash, hash_secret(secret)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown device")
    if device.status != "active":
        raise HTTPException(status.HTTP_410_GONE, "Device has been retired")
    return device


# ---------- audit ----------
def client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def audit(db: Session, request: Request, actor: str, action: str, target_type: str = "", target_id="", **details):
    db.add(AuditLog(actor=actor, action=action, target_type=target_type, target_id=str(target_id),
                    details=details, ip=client_ip(request)))


# ---------- enrollment brute-force guard ----------
# Enrollment keys are short enough to type by hand, so the one thing that must not be
# possible is guessing them at machine speed. Failed /api/agent/enroll attempts are
# counted per client IP in a sliding window; successes don't count. In-memory and
# per-worker, which is all this needs -- it turns 10^12 guesses into geological time.
ENROLL_FAIL_LIMIT = 10
ENROLL_FAIL_WINDOW = timedelta(minutes=10)
_enroll_fails: dict[str, list] = {}


def enroll_attempts_exceeded(ip: str) -> bool:
    cutoff = utcnow() - ENROLL_FAIL_WINDOW
    recent = [t for t in _enroll_fails.get(ip, []) if t > cutoff]
    if recent:
        _enroll_fails[ip] = recent
    else:
        _enroll_fails.pop(ip, None)
    return len(recent) >= ENROLL_FAIL_LIMIT


def record_enroll_failure(ip: str) -> None:
    if len(_enroll_fails) > 10_000:  # bound memory against spoofed-IP floods
        _enroll_fails.clear()
    _enroll_fails.setdefault(ip, []).append(utcnow())


def clear_enroll_failures(ip: str) -> None:
    _enroll_fails.pop(ip, None)
