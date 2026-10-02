"""Démarre l'API perso (lit cookies depuis le HAR, jamais affichés)."""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import uvicorn

HAR = os.getenv("OD_HAR") or max(
    glob.glob(os.path.expanduser("~/storage/downloads/Reqable/*sharepoint.com_*.har")),
    key=os.path.getmtime,
)

with open(HAR) as fh:
    j = json.load(fh)
parts = []
tenant = None
personal = None
for e in j["log"]["entries"]:
    u = e["request"]["url"]
    if "/_api/" in u:
        if "-my.sharepoint.com" in u:
            if tenant is None:
                tenant = u.split("://", 1)[1].split("/", 1)[0]
            if personal is None:
                idx = u.find("/personal/")
                if idx != -1:
                    personal = u[idx:].split("/_api")[0]
        for h in e["request"]["headers"]:
            if h["name"].lower() == "cookie" and len(parts) < 20:
                parts.append(h["value"])
        if parts and tenant and personal:
            break
if not parts:
    raise SystemExit("cookies introuvables dans le HAR")

os.environ.setdefault("OD_COOKIES", "; ".join(parts))
os.environ.setdefault("OD_TENANT", tenant)
os.environ.setdefault("OD_PERSONAL_PATH", personal)
os.environ.setdefault("OD_DRIVE_ID", "x")
os.environ.setdefault("OD_MIN_DELAY", "0.2")

key_file = "/data/data/com.termux/files/usr/tmp/opencode/od_api_key.txt"
with open(key_file) as fh:
    os.environ.setdefault("OD_SERVER_KEY", fh.read().strip())

# import après env (config.py exige les vars)
import server  # noqa: E402

uvicorn.run(server.app, host="127.0.0.1", port=8000, log_level="warning")
