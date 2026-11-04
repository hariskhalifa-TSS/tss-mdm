#!/usr/bin/env python3
"""Enroll realistic fake devices (Android, iOS, macOS, Windows) for demos and load tests.

  python scripts/simulate_devices.py --server http://localhost:8000 --token <TOKEN> --count 12
"""
import argparse
import json
import random
import urllib.request
import uuid

CATALOG = {
    "android": [("Samsung", "Galaxy Tab A9", "14"), ("Samsung", "Galaxy A55", "14"), ("Google", "Pixel 8", "15"),
                ("Lenovo", "Tab M10", "13"), ("Zebra", "TC52 Scanner", "11")],
    "ios": [("Apple", "iPhone 15", "18.1"), ("Apple", "iPhone 13", "17.6")],
    "ipados": [("Apple", "iPad (10th gen)", "18.1")],
    "macos": [("Apple", "MacBook Air M2", "15.1"), ("Apple", "MacBook Pro M3", "15.0")],
    "windows": [("Dell", "Latitude 5440", "Windows 11 Pro 10.0.22631"), ("HP", "ProBook 450 G10", "Windows 11 Pro 10.0.22631"),
                ("Lenovo", "ThinkCentre M70q (POS)", "Windows 10 Pro 10.0.19045")],
}
APPS = {
    "android": [("TSS Store App", "com.thesouledstore.app"), ("Chrome", "com.android.chrome"), ("WhatsApp", "com.whatsapp"), ("Pine Labs POS", "com.pinelabs.pos")],
    "ios": [("TSS Store App", "com.thesouledstore.ios"), ("Safari", "com.apple.mobilesafari"), ("Slack", "com.tinyspeck.chatlyio")],
    "ipados": [("TSS Store App", "com.thesouledstore.ios"), ("Safari", "com.apple.mobilesafari")],
    "macos": [("Google Chrome", "com.google.Chrome"), ("Slack", "com.tinyspeck.slackmacgap"), ("Figma", "com.figma.Desktop")],
    "windows": [("Google Chrome", "Google LLC"), ("Microsoft Office 365", "Microsoft Corporation"), ("Tally Prime", "Tally Solutions")],
}
STORES = [("Mumbai", 19.076, 72.8777), ("Bengaluru", 12.9716, 77.5946), ("Delhi", 28.6139, 77.209), ("Pune", 18.5204, 73.8567)]


def post(url, body, auth=None):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
    if auth:
        req.add_header("Authorization", auth)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def fake(platform: str, i: int) -> dict:
    make, model, osv = random.choice(CATALOG[platform])
    city, lat, lon = random.choice(STORES)
    total = random.choice([64, 128, 256, 512])
    inv = {
        "name": f"TSS-{city[:3].upper()}-{platform[:3].upper()}-{i:03d}", "os_version": osv, "manufacturer": make,
        "model": model, "serial_number": uuid.uuid4().hex[:12].upper(), "hardware_id": str(uuid.uuid4()),
        "agent_version": "0.1.0-sim", "battery_level": random.randint(8, 100), "is_charging": random.random() < .3,
        "storage_total_gb": total, "storage_free_gb": round(random.uniform(2, total * .7), 1),
        "ram_total_gb": random.choice([4, 6, 8, 16]), "ip_address": f"10.{random.randint(1, 20)}.{random.randint(0, 255)}.{random.randint(2, 250)}",
        "mac_address": ":".join(f"{random.randint(0, 255):02x}" for _ in range(6)), "wifi_ssid": f"TSS-{city}-Staff",
        "latitude": lat + random.uniform(-.03, .03), "longitude": lon + random.uniform(-.03, .03),
        "is_encrypted": random.random() > .1, "passcode_set": random.random() > .15, "is_rooted": random.random() < .03,
        "installed_apps": [{"name": n, "id": a, "version": f"{random.randint(1, 9)}.{random.randint(0, 20)}"} for n, a in APPS[platform]],
    }
    if platform in ("android", "ios"):
        inv["imei"] = "35" + "".join(str(random.randint(0, 9)) for _ in range(13))
    return inv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--count", type=int, default=12)
    a = ap.parse_args()
    plats = list(CATALOG)
    for i in range(a.count):
        p = plats[i % len(plats)]
        r = post(f"{a.server}/api/agent/enroll", {"token": a.token, "platform": p, "inventory": fake(p, i + 1)})
        print(f"enrolled {p:8s} {r['device_id']}")


if __name__ == "__main__":
    main()
