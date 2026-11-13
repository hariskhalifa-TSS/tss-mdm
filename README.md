# TSS MDM

In-house Mobile Device Management for The Souled Store. Phase 1 ships the admin console, Super Admin & RBAC, device enrollment, and inventory. See **[docs/PLAN.md](docs/PLAN.md)** for the roadmap.

```
server/            FastAPI backend + web console (server/app/static)
  app/routers/     auth, users, devices+groups, enrollment, agent, dashboard+audit
  tests/           pytest suite (SQLite or PostgreSQL)
agents/rust/       tss-agent.exe    the one agent: enrollment window + background service
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

Enrollment is a key and an .exe. Nothing else — no QR code, no link, no commands to copy.

1. Console → **Enrollment** → **+ New enrollment key**. You get a key like `TSSM-4K7P-9XQ2`.
2. On the PC, run **TSS-MDM-Agent.exe** (download it from the same screen, or from
   `https://<server>/downloads/TSS-MDM-Agent.exe`) and approve the Windows admin prompt.
3. Type the key, click **Enroll**. The device shows up in **Devices** within a few seconds.

The key is forgiving: case, spaces, dashes and the usual `0`/`O` and `1`/`I`/`L` mix-ups
all work. The server address is baked into the .exe, which is why the key is all anyone needs.

**Mass deployment** (GPO, Intune, a login script) — same .exe, no window:

```bat
TSS-MDM-Agent.exe --key TSSM-4K7P-9XQ2
```

**Removing the agent**: `TSS-MDM-Agent.exe --uninstall`

**Demo data**: `python scripts/simulate_devices.py --server http://localhost:8000 --token <KEY> --count 20`

## Building the agent

GitHub Actions (**Build Windows agent**) builds `agents/rust/dist/tss-agent.exe` and commits it
so the server can serve it. The MDM address is baked in at build time:

```bash
MDM_SERVER_URL=https://mdm.thesouledstore.com cargo build --release --manifest-path agents/rust/Cargo.toml
```

Change `MDM_SERVER_URL` in `.github/workflows/build-windows-agent.yml` (or pass it when running
the workflow manually) if the server moves. The build fails if the exe doesn't point where you asked.

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
