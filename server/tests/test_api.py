import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.update(MDM_DATA_DIR=_tmp, MDM_DATABASE_URL=os.getenv("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"), MDM_SECRET_KEY="test-secret-key-for-pytest-only-0123456789",
                  MDM_SUPERADMIN_EMAIL="root@tss.test", MDM_SUPERADMIN_PASSWORD="Initial-Pass-123!",
                  MDM_MAX_FAILED_LOGINS="3")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

NEW_PW = "Brand-New-Pass-456!"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(c, email, pw):
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def su(client):
    """Super admin headers, after the forced first-login password change."""
    h = login(client, "root@tss.test", "Initial-Pass-123!")
    assert client.get("/api/devices", headers=h).status_code == 403  # blocked until password change
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "Initial-Pass-123!", "new_password": NEW_PW})
    assert r.status_code == 200, r.text
    assert client.get("/api/devices", headers=h).status_code == 401  # old session revoked
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health_and_ui(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "TSS MDM" in r.text
    assert "Content-Security-Policy" in r.headers
    assert client.get("/downloads/tss-agent.ps1").status_code == 200
    assert client.get("/downloads/../../etc/passwd").status_code == 404


def test_unauthenticated_rejected(client):
    for path in ("/api/devices", "/api/users", "/api/dashboard", "/api/audit"):
        assert client.get(path).status_code == 401


def test_weak_password_rejected(client, su):
    r = client.post("/api/auth/change-password", headers=su, json={"current_password": NEW_PW, "new_password": "short"})
    assert r.status_code == 400 and "12 characters" in r.json()["detail"]


def test_lockout_after_failed_logins(client, su):
    r = client.post("/api/users", headers=su, json={"email": "lock@tss.test", "name": "Lock", "role": "viewer"})
    assert r.status_code == 201
    temp = r.json()["temporary_password"]
    for _ in range(3):
        assert client.post("/api/auth/login", json={"email": "lock@tss.test", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "lock@tss.test", "password": temp}).status_code == 423


def test_rbac(client, su):
    r = client.post("/api/users", headers=su, json={"email": "view@tss.test", "name": "V", "role": "viewer"})
    temp = r.json()["temporary_password"]
    h = login(client, "view@tss.test", temp)
    r = client.post("/api/auth/change-password", headers=h, json={"current_password": temp, "new_password": NEW_PW})
    vh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/devices", headers=vh).status_code == 200
    assert client.get("/api/users", headers=vh).status_code == 403
    assert client.post("/api/enrollment/tokens", headers=vh, json={"name": "x"}).status_code == 403
    assert client.post("/api/groups", headers=vh, json={"name": "x"}).status_code == 403
    assert client.get("/api/audit", headers=vh).status_code == 403
    # role change revokes the old session
    uid = r.json()["user"]["id"]
    client.patch(f"/api/users/{uid}", headers=su, json={"role": "technician"})
    assert client.get("/api/devices", headers=vh).status_code == 401


def test_cannot_remove_last_super_admin(client, su):
    me = client.get("/api/auth/me", headers=su).json()
    assert client.patch(f"/api/users/{me['id']}", headers=su, json={"role": "admin"}).status_code == 400
    assert client.delete(f"/api/users/{me['id']}", headers=su).status_code == 400


def test_enrollment_inventory_and_commands(client, su):
    g = client.post("/api/groups", headers=su, json={"name": "Store Bandra"}).json()
    t = client.post("/api/enrollment/tokens", headers=su, json={
        "name": "Bandra tablets", "platform": "android", "group_id": g["id"], "max_uses": 2,
        "assigned_user": "store.bandra@tss.test"}).json()
    assert t["usable"] and t["enroll_url"].endswith(t["token"])
    assert client.get(f"/api/enrollment/tokens/{t['id']}/qr", headers=su).headers["content-type"] == "image/png"
    assert client.get(f"/enroll/{t['token']}").status_code == 200
    assert client.get("/enroll/bogus").status_code == 404

    inv = {"name": "Tab-01", "model": "Galaxy Tab A9", "manufacturer": "Samsung", "os_version": "14",
           "serial_number": "R9XYZ", "hardware_id": "hw-1", "battery_level": 15, "storage_total_gb": 64,
           "storage_free_gb": 3, "is_encrypted": False, "custom_field": "kept",
           "installed_apps": [{"name": "Chrome", "id": "com.android.chrome"}]}
    # wrong platform for this token
    assert client.post("/api/agent/enroll", json={"token": t["token"], "platform": "windows", "inventory": inv}).status_code == 403
    r = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "android", "inventory": inv})
    assert r.status_code == 201, r.text
    dev = r.json()
    auth = {"Authorization": f"Device {dev['device_id']}:{dev['device_secret']}"}

    d = client.get(f"/api/devices/{dev['device_id']}", headers=su).json()
    assert d["group_name"] == "Store Bandra" and d["assigned_user"] == "store.bandra@tss.test"
    assert d["online"] and d["extra"]["custom_field"] == "kept" and d["installed_apps"][0]["name"] == "Chrome"

    dash = client.get("/api/dashboard", headers=su).json()
    assert dash["total"] == 1 and dash["by_platform"]["android"] == 1
    assert dash["alerts"] == {"low_battery": 1, "low_storage": 1, "unencrypted": 1}

    # command round-trip
    c = client.post(f"/api/devices/{dev['device_id']}/commands", headers=su, json={"command": "refresh_inventory"}).json()
    r = client.post("/api/agent/checkin", headers=auth, json={"inventory": {"battery_level": 80}}).json()
    assert r["commands"][0]["id"] == c["id"]
    client.post("/api/agent/checkin", headers=auth, json={"command_results": [{"id": c["id"], "status": "succeeded", "result": "ok"}]})
    d = client.get(f"/api/devices/{dev['device_id']}", headers=su).json()
    assert d["battery_level"] == 80 and d["commands"][0]["status"] == "succeeded"
    assert client.post(f"/api/devices/{dev['device_id']}/commands", headers=su, json={"command": "rm -rf"}).status_code == 400

    # bad credentials
    bad = {"Authorization": f"Device {dev['device_id']}:wrong"}
    assert client.post("/api/agent/checkin", headers=bad, json={}).status_code == 401

    # re-enroll same hardware => same record, new secret; max_uses then exhausted
    r2 = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "android", "inventory": inv}).json()
    assert r2["device_id"] == dev["device_id"]
    assert client.post("/api/agent/checkin", headers=auth, json={}).status_code == 401
    assert client.post("/api/agent/enroll", json={"token": t["token"], "platform": "android",
                                                  "inventory": {**inv, "hardware_id": "hw-2"}}).status_code == 403

    # search, filter, CSV
    assert client.get("/api/devices?q=r9xyz", headers=su).json()["total"] == 1
    assert client.get("/api/devices?platform=ios", headers=su).json()["total"] == 0
    assert "Tab-01" in client.get("/api/devices/export.csv", headers=su).text

    # retire => agent locked out
    auth2 = {"Authorization": f"Device {r2['device_id']}:{r2['device_secret']}"}
    client.post(f"/api/devices/{dev['device_id']}/retire", headers=su)
    assert client.post("/api/agent/checkin", headers=auth2, json={}).status_code == 410

    # revoke token
    t2 = client.post("/api/enrollment/tokens", headers=su, json={"name": "temp"}).json()
    client.post(f"/api/enrollment/tokens/{t2['id']}/revoke", headers=su)
    assert client.post("/api/agent/enroll", json={"token": t2["token"], "platform": "macos", "inventory": {}}).status_code == 403


def test_inventory_validation(client, su):
    t = client.post("/api/enrollment/tokens", headers=su, json={"name": "v"}).json()
    r = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "windows", "inventory": {"battery_level": 500}})
    assert r.status_code == 422
    r = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "symbian", "inventory": {}})
    assert r.status_code == 400


def test_remote_actions(client, su):
    t = client.post("/api/enrollment/tokens", headers=su, json={"name": "ra"}).json()
    dev = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "windows",
                                                 "inventory": {"name": "PC1", "hardware_id": "ra-1"}}).json()
    did = dev["device_id"]
    # technician can lock, but not reboot/shutdown (sensitive -> admin)
    tech = client.post("/api/users", headers=su, json={"email": "tech@tss.test", "name": "T", "role": "technician"}).json()
    th = login(client, "tech@tss.test", tech["temporary_password"])
    r = client.post("/api/auth/change-password", headers=th, json={"current_password": tech["temporary_password"], "new_password": NEW_PW})
    th = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.post(f"/api/devices/{did}/commands", headers=th, json={"command": "lock"}).status_code == 201
    assert client.post(f"/api/devices/{did}/commands", headers=th, json={"command": "reboot"}).status_code == 403
    assert client.post(f"/api/devices/{did}/commands", headers=su, json={"command": "reboot"}).status_code == 201
    assert client.post(f"/api/devices/{did}/commands", headers=su, json={"command": "shutdown"}).status_code == 201
    # agent receives them
    auth = {"Authorization": f"Device {did}:{dev['device_secret']}"}
    cmds = client.post("/api/agent/checkin", headers=auth, json={}).json()["commands"]
    assert {c["command"] for c in cmds} == {"lock", "reboot", "shutdown"}


def test_run_script(client, su):
    t = client.post("/api/enrollment/tokens", headers=su, json={"name": "rs"}).json()
    dev = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "windows",
                                                 "inventory": {"name": "PC2", "hardware_id": "rs-1"}}).json()
    did = dev["device_id"]
    # empty script rejected; technician can't run scripts
    assert client.post(f"/api/devices/{did}/commands", headers=su, json={"command": "run_script", "payload": {"script": "  "}}).status_code == 400
    tech = client.post("/api/users", headers=su, json={"email": "t2@tss.test", "name": "T2", "role": "technician"}).json()
    th = login(client, "t2@tss.test", tech["temporary_password"])
    r = client.post("/api/auth/change-password", headers=th, json={"current_password": tech["temporary_password"], "new_password": NEW_PW})
    th = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.post(f"/api/devices/{did}/commands", headers=th, json={"command": "run_script", "payload": {"script": "echo hi"}}).status_code == 403
    # admin queues it; agent receives it with payload; result round-trips
    c = client.post(f"/api/devices/{did}/commands", headers=su, json={"command": "run_script", "payload": {"script": "Get-Date", "shell": "powershell"}}).json()
    auth = {"Authorization": f"Device {did}:{dev['device_secret']}"}
    got = client.post("/api/agent/checkin", headers=auth, json={}).json()["commands"][0]
    assert got["command"] == "run_script" and got["payload"]["script"] == "Get-Date"
    client.post("/api/agent/checkin", headers=auth, json={"command_results": [{"id": c["id"], "status": "succeeded", "result": "2026-01-01"}]})
    d = client.get(f"/api/devices/{did}", headers=su).json()
    assert d["commands"][0]["status"] == "succeeded" and "2026" in d["commands"][0]["result"]


def test_checkin_advertises_agent_version(client, su):
    t = client.post("/api/enrollment/tokens", headers=su, json={"name": "av"}).json()
    dev = client.post("/api/agent/enroll", json={"token": t["token"], "platform": "linux",
                                                 "inventory": {"name": "L", "hardware_id": "av-1"}}).json()
    auth = {"Authorization": f"Device {dev['device_id']}:{dev['device_secret']}"}
    r = client.post("/api/agent/checkin", headers=auth, json={}).json()
    assert r["latest_agent_version"] and r["checkin_interval_seconds"] == 10


def test_installer_download(client, su):
    t = client.post("/api/enrollment/tokens", headers=su, json={"name": "Head Office"}).json()
    r = client.get(f"/api/enrollment/tokens/{t['id']}/installer?os=windows", headers=su)
    assert r.status_code == 200
    assert "TSS-MDM-Setup-Head-Office.bat" in r.headers["content-disposition"]
    assert t["token"] in r.text and "RunAs" in r.text and "\r\n" in r.text  # token baked in, self-elevating, CRLF
    assert "downloads/tss-agent.ps1" in r.text
    mac = client.get(f"/api/enrollment/tokens/{t['id']}/installer?os=macos", headers=su)
    assert mac.status_code == 200 and t["token"] in mac.text and ".command" in mac.headers["content-disposition"]
    # viewer can't download; bad os rejected; revoked token rejected
    assert client.get(f"/api/enrollment/tokens/{t['id']}/installer?os=beos", headers=su).status_code == 400
    client.post(f"/api/enrollment/tokens/{t['id']}/revoke", headers=su)
    assert client.get(f"/api/enrollment/tokens/{t['id']}/installer", headers=su).status_code == 400


def test_audit_log_records_actions(client, su):
    actions = {a["action"] for a in client.get("/api/audit?page_size=500", headers=su).json()["items"]}
    for a in ("auth.login", "auth.password_changed", "user.created", "device.enrolled", "device.retired",
              "enrollment.token_revoked", "auth.locked"):
        assert a in actions, a
