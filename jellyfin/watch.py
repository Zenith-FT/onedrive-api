"""Détecte les nouvelles vidéos OneDrive et génère leurs .strm (auto-detect).

Principe : scrute les dossiers configurés via list_folder() (SharePoint REST,
vérifié live), compare avec l'état précédent (fichier JSON local) et écrit les
.strm manquants via jellyfin/strm.py. À lancer en cron ou en boucle.

Usage (une passe) :
  OD_COOKIES="..." OD_SERVER_KEY="..." \\
  python jellyfin/watch.py "/personal/prenom_nom_domaine_fr/Documents/Jellyfin" /mnt/onedrive-strm

Boucle toutes les 5 min :
  ... python jellyfin/watch.py --loop 300 <dossier> <out_dir>
État : <out_dir>/.watch-state.json (jamais de secrets dedans).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from client import OneDriveInternalClient
from jellyfin.strm import VIDEO_EXTS, build_library

STATE_NAME = ".watch-state.json"


def snapshot(client: OneDriveInternalClient, folder: str) -> dict:
    """{nom -> taille} des vidéos du dossier."""
    out = {}
    for f in client.list_folder(folder):
        name = str(f.get("Name", ""))
        if name.lower().endswith(VIDEO_EXTS):
            out[name] = {"Length": f.get("Length"), "ServerRelativeUrl": f.get("ServerRelativeUrl", folder.rstrip("/") + "/" + name)}
    return out


def load_state(out_dir: str) -> dict:
    p = os.path.join(out_dir, STATE_NAME)
    if os.path.exists(p):
        with open(p) as fh:
            return json.load(fh)
    return {}


def save_state(out_dir: str, state: dict) -> None:
    with open(os.path.join(out_dir, STATE_NAME), "w") as fh:
        json.dump(state, fh, indent=2)


def run_once(folder: str, out_dir: str, base: str, api_key: str) -> list[str]:
    """Une passe : retourne les .strm créés pour les NOUVELLES vidéos."""
    c = OneDriveInternalClient.from_env()
    current = snapshot(c, folder)
    state = load_state(out_dir)
    new_rows = []
    for name, info in current.items():
        prev = state.get(name)
        if prev is None or prev.get("Length") != info["Length"]:
            new_rows.append({"FileLeafRef": name, "FileRef": info["ServerRelativeUrl"], "FSObjType": "0"})
    written = build_library(new_rows, base, api_key, out_dir) if new_rows else []
    state.update(current)
    save_state(out_dir, state)
    return written


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    loop = 0
    for a in sys.argv[1:]:
        if a.startswith("--loop"):
            loop = int(a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1])
    if len(args) < 2:
        print(__doc__)
        raise SystemExit(1)
    folder, out_dir = args[0], args[1]
    base = os.getenv("OD_API_BASE", "http://127.0.0.1:8000")
    key = os.getenv("OD_SERVER_KEY", "")
    if not key:
        print("OD_SERVER_KEY manquante")
        raise SystemExit(1)
    while True:
        try:
            w = run_once(folder, out_dir, base, key)
            print(f"{time.strftime('%H:%M:%S')} : {len(w)} nouveau(x) .strm" + (f" : {w}" if w else ""))
        except Exception as ex:
            print(f"{time.strftime('%H:%M:%S')} : KO {type(ex).__name__} (session expirée ? cli.py health)")
        if not loop:
            break
        time.sleep(loop)


if __name__ == "__main__":
    main()
