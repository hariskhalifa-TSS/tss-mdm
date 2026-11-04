# TSS MDM: Plan & Roadmap

An in-house MDM/UEM for The Souled Store, modelled on Hexnode UEM and ManageEngine MDM Plus.
Scope: internal use only (one organisation). Platforms: Android, Windows, iOS/iPadOS, macOS.

## 1. What Hexnode and ManageEngine offer (what we're matching)

| Module | Hexnode / ManageEngine | TSS MDM status |
|---|---|---|
| Admin console, roles (Super Admin / Admin / Technician / Viewer) | Technician roles with scoped access | **Done (Phase 1)** |
| Audit log of admin and device actions | Yes | **Done (Phase 1)** |
| Device enrollment (token, QR, email invite, bulk CSV) | Many methods per OS | **Token + QR + link done**; invites/CSV in Phase 2 |
| Device inventory (hardware, OS, battery, storage, network, apps, location) | Yes | **Done** (agent-reported) |
| Device groups | Yes | **Done** |
| Remote actions (lock, wipe, reboot, message, locate) | Yes | Queue + message/reboot (Windows) done; full set in Phase 2 |
| Policies (passcode, Wi-Fi, VPN, restrictions, compliance) | Yes | Phase 2 |
| App management (push/remove apps, catalog, managed Play / VPP) | Yes | Phase 3 |
| Kiosk mode (single / multi-app, web kiosk) for store tablets & POS | Yes | Phase 3 |
| Geofencing, location history | Yes | Phase 4 |
| Remote view/control, file transfer | Yes | Phase 4 |
| Patch / OS update management | Yes | Phase 4 |
| Directory integration (Google Workspace / Entra ID / Okta), SSO | Yes | Phase 2 (SSO) / Phase 4 (directory sync) |
| Reports, alerts (email/Slack) | Yes | Phase 2 |

## 2. How real MDM works per platform (and why it's phased)

| Platform | Proper management channel | What we need from TSS |
|---|---|---|
| **Windows 10/11** | (a) Our own agent (PowerShell/.NET service). This is what Hexnode calls the "Hexnode Installer": scripts, MSI deployment, live hardware info. (b) Native OMA-DM/MDM enrollment (MS-MDE2) for lock/wipe/BitLocker/CSP policies, optionally via Entra ID / Autopilot. | A domain + TLS cert; optionally a Microsoft Entra ID tenant for Autopilot |
| **Android** | Android Enterprise via Google's **Android Management API** (managed Google Play, kiosk, policies) *or* our own Device Policy Controller app (device-owner). Enrollment by QR at setup, zero-touch, Samsung KME. | A Google Cloud project, an Android Enterprise binding (free) with a TSS Google account, and optionally a zero-touch reseller account |
| **iOS / iPadOS** | Apple MDM protocol: device installs an enrollment profile, server talks to it through **APNs**. Supervision through **Apple Business Manager (ABM)** for silent apps, kiosk and full restrictions. | Apple Push (APNs) certificate (free, needs an Apple ID + CSR signed by us), **Apple Business Manager** account (D-U-N-S number needed), devices bought through ABM-linked reseller or added with Apple Configurator |
| **macOS** | Same Apple MDM protocol + our agent for extra inventory/scripts | Same as iOS |

Phase 1 covers inventory and enrollment on every OS through our agent, and on Apple/Android through the simulator. The native channels are Phases 2–3.

## 3. Architecture

```
 Admin browser ──HTTPS──► Caddy (TLS) ──► TSS MDM API (FastAPI, Python)
                                            │  ├─ Admin REST API + web console
 Windows / macOS / Linux agent ──HTTPS──────┤  ├─ Agent API (enroll, check-in, commands)
 Android (AMAPI / DPC app)   ──── Phase 2 ──┤  ├─ Apple MDM endpoints + APNs  ─ Phase 2
 iPhone / iPad / Mac (MDM)   ──── Phase 2 ──┘  └─ Background worker (alerts, push) ─ Phase 2
                                            ▼
                                     PostgreSQL (SQLite for dev)
```

* Devices **pull** commands at check-in (every 5 min); Phase 2 adds push wake-ups (APNs for Apple, FCM/AMAPI for Android, WNS or a websocket for Windows) so actions apply in seconds.
* Each device gets its **own secret**. Enrollment tokens are one-time-use / expiring / revocable.
* Admin auth: bcrypt passwords, 12+ char policy, forced change on first login, lockout after 5 failures, JWT sessions revoked on role/password change, full audit trail.

## 4. Phases

**Phase 1: Foundation (built and tested)**
Admin console, Super Admin + 4-role RBAC, audit log, enrollment tokens with QR/link, Windows & macOS/Linux agents, device inventory/search/filters/CSV export, groups, dashboard, command queue, Docker deploy.

**Phase 2: Control (≈ 4–6 weeks)**
* Apple MDM: APNs cert upload, enrollment profile (.mobileconfig) signing, check-in/command endpoints, ABM/ADE sync. Lock, wipe, lost mode, restrictions.
* Android: Android Enterprise signup, AMAPI enrollment QR, policies (passcode, camera, USB, Wi-Fi), lock/wipe/reboot.
* Windows: native MDM enrollment (lock/wipe/BitLocker), plus agent scripts.
* Policies engine (platform-agnostic profiles assigned to groups), compliance rules + alerts (email/Slack), Google Workspace SSO for admins + MFA, email invites & CSV bulk enrollment.

**Phase 3: Apps & Kiosk (≈ 4 weeks)**
Managed Google Play, Apple VPP/Apps & Books, Windows MSI/winget deployment, app catalog, single/multi-app kiosk for store tablets & POS terminals, web kiosk.

**Phase 4: Advanced (≈ 4–6 weeks)**
Geofencing & location history, remote view/control (WebRTC), OS patch scheduling, directory sync (Google/Entra), reports scheduler, multi-region HA.

## 5. What I need from you (decisions & accounts)

1. **Domain** for the server, e.g. `mdm.thesouledstore.com`, and where to host it (any Linux VM with Docker: AWS Mumbai `ap-south-1` recommended; ~2 vCPU / 4 GB RAM covers a few thousand devices).
2. **Apple**: Apple Business Manager account for TSS (needs D-U-N-S), and an Apple ID for the APNs certificate.
3. **Google**: a TSS Google account for the Android Enterprise binding, plus a Google Cloud project.
4. **Microsoft** (optional): Entra ID tenant if you want Autopilot for new laptops.
5. **Device count per platform** and the main use cases (store POS/tablets in kiosk? staff phones BYOD?). This decides what Phase 2 builds first.
6. Should admins sign in with **Google Workspace SSO**?

## 6. Known limitations of Phase 1

* No push channel yet, so commands land at the next check-in (≤ 5 min).
* Android / iOS devices can't enroll for real until Phase 2 (they're covered by the simulator and the API is ready).
* Windows agent supports `refresh_inventory`, `send_message` and `reboot`. Lock/wipe need native Windows MDM (Phase 2).
* Single server instance; HA and backups are configured at deploy time (Postgres backups: daily `pg_dump` cron or managed RDS).
