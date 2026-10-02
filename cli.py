"""CLI simple sans FastAPI.

Exemples:
  OD_COOKIES="FedAuth=...; rtFa=..." python cli.py list "/Documents"
  OD_COOKIES="..." python cli.py mkdir "/personal/prenom_nom_domaine_fr/Documents/Test"
  OD_COOKIES="..." python cli.py fav
  OD_COOKIES="..." python cli.py health
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from client import OneDriveInternalClient


def _is_session_error(ex: Exception) -> bool:
    msg = f"{type(ex).__name__} {str(ex)}".lower()
    return any(k in msg for k in ("401", "403", "fedauth", "rtfa", "unauthorized", "forbidden", "access denied"))


def cmd_health(c: OneDriveInternalClient) -> int:
    """Appelle digest + list racine + favorites, affiche OK/KO par endpoint."""
    checks = [
        ("digest", lambda: c.get_digest() and {"ok": True}),
        ("list_racine", lambda: c.list_documents("/Documents")),
        ("favorites", lambda: c.favorites()),
    ]
    failed = 0
    session_dead = False
    for name, fn in checks:
        try:
            fn()
            print(f"{name}: OK")
        except Exception as ex:
            failed += 1
            print(f"{name}: KO ({type(ex).__name__})")
            if _is_session_error(ex):
                session_dead = True
    if session_dead:
        print("session expirée, recopie FedAuth/rtFa (DevTools > Application > Cookies).")
    elif failed:
        print("un endpoint interne a peut-être changé (voir har_parse.py).")
    return 1 if failed else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    c = OneDriveInternalClient.from_env()
    if cmd == "list":
        path = sys.argv[2] if len(sys.argv) > 2 else "/Documents"
        print(json.dumps(c.list_documents(path), indent=2)[:8000])
    elif cmd == "mkdir":
        print(json.dumps(c.create_folder(sys.argv[2]), indent=2)[:4000])
    elif cmd == "fav":
        print(json.dumps(c.favorites(), indent=2)[:4000])
    elif cmd == "qa":
        print(json.dumps(c.quick_access(), indent=2)[:4000])
    elif cmd == "digest":
        print(c.get_digest())
    elif cmd == "health":
        raise SystemExit(cmd_health(c))
    else:
        print("cmd inconnue:", cmd)

if __name__ == "__main__":
    main()
