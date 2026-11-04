# TSS MDM

In-house Mobile Device Management for The Souled Store. Phase 1 ships the admin console, Super Admin & RBAC, device enrollment, and inventory. See **[docs/PLAN.md](docs/PLAN.md)** for the roadmap.

```
server/            FastAPI backend + web console (server/app/static)
  app/routers/     auth, users, devices+groups, enrollment, agent, dashboard+audit
  tests/           pytest suite (SQLite or PostgreSQL)
agents/windows/    tss-agent.ps1    Windows 10/11 agent (scheduled task, every 5 min)
agents/python/     tss_agent.py     macOS / Linux agent (launchd / systemd)
scripts/manage.py  create-superadmin / reset-password CLI
scripts/simulate_devices.py   enroll fake Android/iOS/macOS/Windows devices
```

## Run locally (2 minutes)

```bash
cd server
pip install -r requirements.txt
MDM_SUPERADMIN_EMAIL=haris.khalifa@thesouledstore.com uvicorn app.main:app --port 8000
# temporary super admin password -> server/data/initial_superadmin.txt
```
Open http://localhost:8000, sign in, then set your own password.

## Deploy (production)

```bash
cp .env.example .env        # set domain, secrets, super admin email
docker compose up -d --build
docker compose exec mdm cat /data/initial_superadmin.txt
```
Caddy obtains a Let's Encrypt certificate for `MDM_DOMAIN` automatically (open ports 80/443, point DNS at the server).

## Enroll a device

Console → **Enrollment** → *New enrollment token* → **QR & install**.
* **Windows** (PowerShell as admin): copy the one-liner shown. It enrolls and installs a SYSTEM scheduled task.
* **macOS/Linux**: `curl -fsSLO https://<server>/downloads/tss_agent.py && sudo python3 tss_agent.py --server https://<server> --token <TOKEN> --install`
* **Demo data**: `python scripts/simulate_devices.py --server http://localhost:8000 --token <TOKEN> --count 20`

## Roles

| Role | Can |
|---|---|
| Viewer | dashboard, devices, groups (read-only) |
| Technician | + create enrollment tokens, edit devices, send commands |
| Admin | + groups, retire/delete devices, revoke tokens, audit log |
| Super Admin | + manage admin users and roles |

Lost the super admin password? `python scripts/manage.py reset-password --email you@thesouledstore.com`

## Tests

```bash
cd server && python -m pytest -q                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest -q   # PostgreSQL
```
API docs: `/api/docs`.

## Security notes
bcrypt passwords (12+ chars, mixed classes), forced change at first login, lockout after 5 failures, sessions revoked on password/role change, per-device secrets (SHA-256 hashed), expiring/limited/revocable enrollment tokens, strict CSP and security headers, full audit log. Keep `MDM_SECRET_KEY` secret and always run behind HTTPS.
