"""Pont Jellyfin <-> API perso OneDrive via fichiers .strm.

Jellyfin ne sait lire que des fichiers locaux : pour chaque vidéo OneDrive on
écrit un `.strm` (texte, 1 ligne = URL http) pointant vers /api/file du serveur
perso (streaming + Range). Jellyfin lit le .strm comme une vidéo.

Écriture directe dans le FS du conteneur proot (pas besoin d'y entrer) :
  <rootfs-ubuntu>/mnt/onedrive-strm/
"""
import os
import urllib.parse

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg", ".ts")


def strm_url(base: str, server_path: str, api_key: str) -> str:
    """URL proxy /api/file pour un chemin serveur OneDrive."""
    q = urllib.parse.quote(server_path, safe="")
    return f"{base.rstrip('/')}/api/file?path={q}&api_key={api_key}"


def safe_name(name: str) -> str:
    """Nom de fichier .strm sûr (garde l'extension d'origine + .strm)."""
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, "_")
    return name + ".strm"


def build_library(listing_rows: list[dict], base: str, api_key: str, out_dir: str,
                  exts: tuple = VIDEO_EXTS) -> list[str]:
    """Depuis des lignes RenderListDataAsStream (FileLeafRef/FileRef), écrit les .strm.

    Retourne les chemins écrits. Ne télécharge rien (Jellyfin streamera).
    """
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for row in listing_rows:
        leaf = str(row.get("FileLeafRef", ""))
        ref = str(row.get("FileRef", ""))
        if not leaf or row.get("FSObjType") == "1":
            continue
        if not leaf.lower().endswith(exts):
            continue
        if not ref:
            continue
        dest = os.path.join(out_dir, safe_name(leaf))
        with open(dest, "w") as fh:
            fh.write(strm_url(base, ref, api_key) + "\n")
        written.append(dest)
    return written
