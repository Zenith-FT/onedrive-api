"""Génère les .strm Jellyfin depuis un dossier OneDrive (via client interne).

Usage:
  OD_COOKIES="..." OD_SERVER_KEY="..." OD_API_BASE="http://127.0.0.1:8000" \\
    python jellyfin/make_strm.py "/Documents" /.../containers/ubuntu/mnt/onedrive-strm
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from client import OneDriveInternalClient
from jellyfin.strm import build_library


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    folder, out = sys.argv[1], sys.argv[2]
    base = os.getenv("OD_API_BASE", "http://127.0.0.1:8000")
    key = os.getenv("OD_SERVER_KEY", "")
    if not key:
        print("OD_SERVER_KEY manquante")
        raise SystemExit(1)
    c = OneDriveInternalClient.from_env()
    resp = c.list_documents(folder, all_pages=True)
    rows = resp.get("ListData", {}).get("Row", [])
    written = build_library(rows, base, key, out)
    print(f"{len(written)} .strm écrits dans {out}")
    for w in written[:20]:
        print(" ", w)


if __name__ == "__main__":
    main()
