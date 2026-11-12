import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MDM_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _secret() -> str:
    """Use MDM_SECRET_KEY, otherwise persist a random key so tokens survive restarts."""
    env = os.getenv("MDM_SECRET_KEY")
    if env:
        return env
    f = DATA_DIR / ".secret_key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip()


def _db_url() -> str:
    """MDM_DATABASE_URL, or a platform-provided DATABASE_URL (Railway, Render, Heroku)."""
    url = os.getenv("MDM_DATABASE_URL") or os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'mdm.db'}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Settings:
    APP_NAME = os.getenv("MDM_APP_NAME", "TSS MDM")
    DATABASE_URL = _db_url()
    SECRET_KEY = _secret()
    PUBLIC_URL = os.getenv("MDM_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    ACCESS_TOKEN_MINUTES = int(os.getenv("MDM_ACCESS_TOKEN_MINUTES", "480"))
    OFFLINE_AFTER_MINUTES = int(os.getenv("MDM_OFFLINE_AFTER_MINUTES", "15"))
    CHECKIN_INTERVAL_SECONDS = int(os.getenv("MDM_CHECKIN_INTERVAL_SECONDS", "10"))
    MAX_FAILED_LOGINS = int(os.getenv("MDM_MAX_FAILED_LOGINS", "0"))  # 0 = lockout disabled
    LOCKOUT_MINUTES = int(os.getenv("MDM_LOCKOUT_MINUTES", "15"))
    SUPERADMIN_EMAIL = os.getenv("MDM_SUPERADMIN_EMAIL", "admin@example.com")
    SUPERADMIN_NAME = os.getenv("MDM_SUPERADMIN_NAME", "Super Admin")
    SUPERADMIN_PASSWORD = os.getenv("MDM_SUPERADMIN_PASSWORD")  # random if unset
    AGENTS_DIR = Path(os.getenv("MDM_AGENTS_DIR", BASE_DIR.parent / "agents"))
    # Latest agent version the server ships. Bump when agents/ changes so devices self-update.
    AGENT_VERSION = os.getenv("MDM_AGENT_VERSION", "0.4.0")


settings = Settings()
