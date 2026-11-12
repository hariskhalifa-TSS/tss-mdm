#!/usr/bin/env python3
"""TSS MDM agent for macOS and Linux (stdlib only; also runs on Windows for testing).

  sudo python3 tss_agent.py --server https://mdm.example.com --token <TOKEN> --install
  python3 tss_agent.py --once          # one check-in using saved credentials
"""
import argparse
import json
import os
import platform
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.3.0"
SYSTEM = platform.system()  # Darwin | Linux | Windows
if SYSTEM == "Windows":
    CONF_DIR = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "TSSMDM"
elif os.geteuid() == 0:
    CONF_DIR = Path("/Library/Application Support/TSSMDM") if SYSTEM == "Darwin" else Path("/etc/tss-mdm")
else:
    CONF_DIR = Path.home() / ".tss-mdm"
CONF = CONF_DIR / "agent.json"


def sh(cmd: str, timeout: int = 15) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return ""


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def disk() -> tuple[float, float]:
    u = shutil.disk_usage("/" if SYSTEM != "Windows" else "C:\\")
    return round(u.total / 1e9, 1), round(u.free / 1e9, 1)


def inventory_macos() -> dict:
    inv = {"manufacturer": "Apple"}
    try:
        hw = plistlib.loads(subprocess.run(["system_profiler", "SPHardwareDataType", "-xml"], capture_output=True,
                                           timeout=30).stdout)[0]["_items"][0]
        inv.update(model=hw.get("machine_model", ""), serial_number=hw.get("serial_number", ""),
                   hardware_id=hw.get("platform_UUID", ""), cpu=hw.get("chip_type") or hw.get("cpu_type", ""))
        mem = hw.get("physical_memory", "")
        if mem.endswith("GB"):
            inv["ram_total_gb"] = float(mem.split()[0])
    except Exception:
        pass
    inv["os_version"] = platform.mac_ver()[0]
    batt = sh("pmset -g batt")
    if "%" in batt:
        try:
            inv["battery_level"] = float(batt.split("\t")[1].split("%")[0])
            inv["is_charging"] = "AC Power" in batt
        except Exception:
            pass
    inv["is_encrypted"] = "On" in sh("fdesetup status")
    inv["wifi_ssid"] = sh("networksetup -getairportnetwork en0 | sed 's/^.*: //'")
    inv["mac_address"] = sh("ifconfig en0 | awk '/ether/{print $2}'")
    apps = []
    for app in Path("/Applications").glob("*.app"):
        try:
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            apps.append({"name": info.get("CFBundleName") or app.stem, "version": info.get("CFBundleShortVersionString", ""),
                         "id": info.get("CFBundleIdentifier", "")})
        except Exception:
            apps.append({"name": app.stem})
    inv["installed_apps"] = apps
    return inv


def inventory_linux() -> dict:
    inv = {
        "manufacturer": read("/sys/class/dmi/id/sys_vendor"), "model": read("/sys/class/dmi/id/product_name"),
        "serial_number": read("/sys/class/dmi/id/product_serial"),
        "hardware_id": read("/sys/class/dmi/id/product_uuid") or read("/etc/machine-id"),
    }
    osr = {}
    for line in read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            osr[k] = v.strip('"')
    inv["os_version"] = osr.get("PRETTY_NAME", platform.release())
    for line in read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            inv["cpu"] = line.split(":", 1)[1].strip()
            break
    for line in read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal"):
            inv["ram_total_gb"] = round(int(line.split()[1]) / 1e6, 1)
    for b in Path("/sys/class/power_supply").glob("BAT*"):
        cap = read(str(b / "capacity"))
        if cap:
            inv["battery_level"] = float(cap)
            inv["is_charging"] = read(str(b / "status")) == "Charging"
    inv["is_encrypted"] = "crypt" in sh("lsblk -o TYPE 2>/dev/null")
    inv["wifi_ssid"] = sh("iwgetid -r 2>/dev/null")
    iface = sh("ip route show default 2>/dev/null | awk '{print $5; exit}'")
    if iface:
        inv["mac_address"] = read(f"/sys/class/net/{iface}/address")
    pkgs = sh("dpkg-query -W -f='${Package}\\t${Version}\\n' 2>/dev/null") or sh("rpm -qa --qf '%{NAME}\\t%{VERSION}\\n' 2>/dev/null")
    inv["installed_apps"] = [dict(zip(("name", "version"), l.split("\t", 1))) for l in pkgs.splitlines()[:1500] if l]
    return inv


def collect() -> dict:
    inv = inventory_macos() if SYSTEM == "Darwin" else inventory_linux() if SYSTEM == "Linux" else {
        "os_version": platform.version(), "hardware_id": platform.node()}
    total, free = disk()
    inv.update(name=socket.gethostname(), agent_version=VERSION, ip_address=local_ip(),
               storage_total_gb=total, storage_free_gb=free, architecture=platform.machine())
    return {k: v for k, v in inv.items() if v not in ("", None)}


def platform_name() -> str:
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[SYSTEM]


def call(server: str, path: str, body: dict, auth: str | None = None) -> dict:
    req = urllib.request.Request(server.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": f"tss-agent/{VERSION}"})
    if auth:
        req.add_header("Authorization", auth)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def save_conf(conf: dict):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(conf, indent=2))
    try:
        CONF.chmod(0o600)
    except Exception:
        pass


def enroll(server: str, token: str) -> dict:
    r = call(server, "/api/agent/enroll", {"token": token, "platform": platform_name(), "inventory": collect()})
    conf = {"server": server.rstrip("/"), "device_id": r["device_id"], "device_secret": r["device_secret"],
            "interval": r.get("checkin_interval_seconds", 300)}
    save_conf(conf)
    print(f"Enrolled. Device ID {conf['device_id']}")
    return conf


def run_command(cmd: dict) -> dict:
    name = cmd["command"]
    if name == "refresh_inventory":
        return {"id": cmd["id"], "status": "succeeded", "result": "inventory sent with next check-in"}
    if name == "send_message":
        p = cmd.get("payload", {})
        title, body = str(p.get("title", "IT"))[:100], str(p.get("body", ""))[:1000]
        if SYSTEM == "Darwin":
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(body)} with title {json.dumps(title)}"])
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body])
        print(f"[message] {title}: {body}")
        return {"id": cmd["id"], "status": "succeeded", "result": "displayed"}
    if name == "reboot":
        subprocess.Popen(["sh", "-c", "shutdown -r +2 'IT is restarting this device in 2 minutes.' 2>/dev/null || sudo shutdown -r +2"])
        return {"id": cmd["id"], "status": "succeeded", "result": "reboot scheduled in 2 min"}
    if name == "shutdown":
        subprocess.Popen(["sh", "-c", "shutdown -h +2 'IT is shutting down this device in 2 minutes.' 2>/dev/null || sudo shutdown -h +2"])
        return {"id": cmd["id"], "status": "succeeded", "result": "shutdown scheduled in 2 min"}
    if name == "lock":
        cmds = ["loginctl lock-sessions", "gnome-screensaver-command -l",
                "pmset displaysleepnow" if SYSTEM == "Darwin" else "xdg-screensaver lock"]
        for c in cmds:
            if subprocess.run(["sh", "-c", c], capture_output=True).returncode == 0:
                return {"id": cmd["id"], "status": "succeeded", "result": "locked"}
        return {"id": cmd["id"], "status": "failed", "result": "no lock method available"}
    if name == "run_script":
        code = str(cmd.get("payload", {}).get("script", ""))
        try:
            r = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=120)
            out = (r.stdout + r.stderr)[:3900] or f"(no output, exit {r.returncode})"
            return {"id": cmd["id"], "status": "succeeded" if r.returncode == 0 else "failed", "result": out}
        except Exception as e:
            return {"id": cmd["id"], "status": "failed", "result": f"ERROR: {e}"}
    return {"id": cmd["id"], "status": "failed", "result": f"'{name}' not supported by this agent yet"}


def _ver(s: str) -> tuple:
    try:
        return tuple(int(x) for x in str(s).split("."))
    except Exception:
        return (0,)


def maybe_self_update(conf: dict, latest: str | None):
    """Download the newer agent, replace the installed copy, and relaunch so it takes effect."""
    if not latest or _ver(latest) <= _ver(VERSION):
        return
    target = CONF_DIR / "tss_agent.py"
    if not target.exists():  # only self-update an installed agent, not an ad-hoc run
        return
    try:
        import urllib.request
        tmp = str(target) + ".new"
        urllib.request.urlretrieve(conf["server"].rstrip("/") + "/downloads/tss_agent.py", tmp)
        os.replace(tmp, target)
        print(f"Updated agent {VERSION} -> {latest}; relaunching")
        os.execv(sys.executable, [sys.executable, str(target)])  # service restarts with new code
    except Exception as e:
        print(f"self-update failed: {e}", file=sys.stderr)


def checkin(conf: dict, results: list) -> list:
    auth = f"Device {conf['device_id']}:{conf['device_secret']}"
    r = call(conf["server"], "/api/agent/checkin", {"inventory": collect(), "command_results": results}, auth)
    conf["interval"] = r.get("checkin_interval_seconds", conf.get("interval", 300))
    conf["_latest"] = r.get("latest_agent_version")
    return [run_command(c) for c in r.get("commands", [])]


def install_service():
    exe, script = sys.executable, str(Path(__file__).resolve())
    target = CONF_DIR / "tss_agent.py"
    shutil.copy(script, target)
    if SYSTEM == "Linux":
        Path("/etc/systemd/system/tss-mdm-agent.service").write_text(
            f"[Unit]\nDescription=TSS MDM agent\nAfter=network-online.target\n\n[Service]\nExecStart={exe} {target}\n"
            "Restart=always\nRestartSec=30\n\n[Install]\nWantedBy=multi-user.target\n")
        sh("systemctl daemon-reload && systemctl enable --now tss-mdm-agent")
        print("Installed systemd service tss-mdm-agent")
    elif SYSTEM == "Darwin":
        plist = Path("/Library/LaunchDaemons/com.thesouledstore.mdm-agent.plist")
        plist.write_bytes(plistlib.dumps({"Label": "com.thesouledstore.mdm-agent", "ProgramArguments": [exe, str(target)],
                                          "RunAtLoad": True, "KeepAlive": True,
                                          "StandardErrorPath": "/var/log/tss-mdm-agent.log"}))
        sh(f"launchctl load -w {plist}")
        print("Installed LaunchDaemon com.thesouledstore.mdm-agent")
    else:
        print("Use agents/windows/tss-agent.ps1 on Windows.")


def main():
    ap = argparse.ArgumentParser(description="TSS MDM agent")
    ap.add_argument("--server")
    ap.add_argument("--token")
    ap.add_argument("--install", action="store_true", help="install as a background service after enrolling")
    ap.add_argument("--once", action="store_true", help="single check-in then exit")
    a = ap.parse_args()

    if a.token:
        if not a.server:
            sys.exit("--server is required with --token")
        try:
            conf = enroll(a.server, a.token)
        except urllib.error.HTTPError as e:
            sys.exit(f"Enrollment failed: {e.code} {e.read().decode()}")
        if a.install:
            install_service()
            return
    elif CONF.exists():
        conf = json.loads(CONF.read_text())
    else:
        sys.exit("Not enrolled. Run with --server and --token.")

    results: list = []
    while True:
        try:
            results = checkin(conf, results)
            if results:  # report results immediately
                results = checkin(conf, results)
        except urllib.error.HTTPError as e:
            if e.code in (401, 410):
                sys.exit("Device was retired or removed from MDM. Stopping.")
            print(f"check-in failed: HTTP {e.code}", file=sys.stderr)
        except Exception as e:  # network down etc.
            print(f"check-in failed: {e}", file=sys.stderr)
        if a.once:
            return
        maybe_self_update(conf, conf.get("_latest"))  # relaunches if a newer agent is available
        time.sleep(conf.get("interval", 300))


if __name__ == "__main__":
    main()
