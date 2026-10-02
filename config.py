"""Config stricte, sans valeur personnelle par défaut (repo public).

Requis via env (voir .env.example) :
  OD_TENANT         ex: votre-tenant-my.sharepoint.com
  OD_PERSONAL_PATH  ex: /personal/prenom_nom_domaine_fr
  OD_DRIVE_ID       ex: <DRIVE_ID> (voir _api/v2.0/drives ou view.delta du HAR)
Optionnel :
  OD_LIST_ID_DOCS   ex: <LIST_ID_DOCS>
"""
import os


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(
            f"{name} manquant. Copie .env.example vers .env et renseigne-le "
            f"(ex: {name}=...). Aucune valeur réelle n'est fournie par défaut."
        )
    return v


TENANT = _req("OD_TENANT")
PERSONAL_PATH = _req("OD_PERSONAL_PATH")
DRIVE_ID = _req("OD_DRIVE_ID")
LIST_ID_DOCS = os.getenv("OD_LIST_ID_DOCS", "")


def personal_base() -> str:
    return f"https://{TENANT}{PERSONAL_PATH}"


def api_base() -> str:
    return personal_base() + "/_api"
