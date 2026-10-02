"""Petite API HTTP personnelle exposant les endpoints internes OneDrive.

Lance:  uvicorn server:app --host 127.0.0.1 --port 8000
Auth:   header X-API-Key (valeur OD_SERVER_KEY, démarrage refusé si absente)
        + cookies OneDrive via OD_COOKIES env (jamais renvoyés ni loggés).

Routes protégées (X-API-Key requise):
  GET  /api/list?path=/Documents&view=
  POST /api/folders {"path": "/personal/.../Documents/..."}
  POST /api/upload-small (form: folder, filename, file)
  POST /api/upload-large (form: folder, filename, file)
  POST /api/upload-auto (form: folder, filename, file)
  POST /api/cancel-upload {"file": "...", "uploadId": "..."}
  GET  /api/delta?token=...
  GET  /api/favorites
  GET  /api/quick-access
"""
import os
import re
import shutil
import tempfile

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel

try:
    from .client import OneDriveInternalClient
except ImportError:
    from client import OneDriveInternalClient

app = FastAPI(title="OneDrive Personal API (interne)")

_CLIENT = None


def _server_key() -> str:
    k = os.getenv("OD_SERVER_KEY", "")
    if not k:
        raise RuntimeError("OD_SERVER_KEY manquante. Refus de démarrer sans clé API (voir .env.example).")
    return k


@app.on_event("startup")
def _require_key() -> None:
    _server_key()


def require_key(req: Request) -> None:
    if req.headers.get("X-API-Key", "") != os.getenv("OD_SERVER_KEY", "") or not os.getenv("OD_SERVER_KEY"):
        raise HTTPException(401, "X-API-Key invalide ou absente")


def require_key_or_query(req: Request) -> None:
    """Variante pour /api/file : les .strm Jellyfin ne peuvent pas envoyer de headers,
    la clé passe en ?api_key= (serveur lié à 127.0.0.1 uniquement)."""
    key = os.getenv("OD_SERVER_KEY", "")
    if not key:
        raise HTTPException(500, "OD_SERVER_KEY manquante")
    if req.headers.get("X-API-Key", "") == key:
        return
    if req.query_params.get("api_key", "") == key:
        return
    raise HTTPException(401, "X-API-Key invalide ou absente")


def get_client() -> OneDriveInternalClient:
    """Singleton: réutilise session + digest (pas un client par requête)."""
    global _CLIENT
    if _CLIENT is None:
        if not os.getenv("OD_COOKIES") and not os.getenv("OD_COOKIES_FILE"):
            raise HTTPException(500, "OD_COOKIES (ou OD_COOKIES_FILE) non configuré. Voir .env.example")
        _CLIENT = OneDriveInternalClient.from_env()
    return _CLIENT


def _public_error(ex: Exception) -> str:
    """Message d'erreur sans secrets (jamais cookies/digest)."""
    msg = f"{type(ex).__name__}: {str(ex)[:200]}"
    return re.sub(r"(?i)(fedauth|rtfa|cookie|digest|x-requestdigest|bearer|set-cookie)[^\s;]*", "[redacted]", msg)


class FolderIn(BaseModel):
    path: str
    overwrite: bool = False


class CancelIn(BaseModel):
    file: str
    uploadId: str


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/list", dependencies=[Depends(require_key)])
def api_list(path: str = "/Documents", view: str | None = None, all_pages: bool = False):
    try:
        return get_client().list_documents(path, view_id=view, all_pages=all_pages)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.post("/api/folders", dependencies=[Depends(require_key)])
def api_create_folder(body: FolderIn):
    try:
        return get_client().create_folder(body.path, body.overwrite)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


def _spool_upload(file: UploadFile) -> tuple[tempfile.SpooledTemporaryFile, int]:
    """Copie l'upload vers un SpooledTemporaryFile (RAM puis disque). Retourne (tmp, taille)."""
    tmp: tempfile.SpooledTemporaryFile = tempfile.SpooledTemporaryFile(max_size=5 * 1024 * 1024)
    shutil.copyfileobj(file.file, tmp)
    size = tmp.tell()
    tmp.seek(0)
    return tmp, size


@app.post("/api/upload-small", dependencies=[Depends(require_key)])
async def api_upload_small(folder: str = Form(...), filename: str = Form(...), file: UploadFile = File(...)):
    try:
        tmp, size = _spool_upload(file)
        try:
            data = tmp.read()
            return get_client().upload_small(folder, filename, data)
        finally:
            tmp.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.post("/api/upload-large", dependencies=[Depends(require_key)])
async def api_upload_large(folder: str = Form(...), filename: str = Form(...), file: UploadFile = File(...)):
    """Gros fichier comme le client web : 1 seul appel AddStubUsingPath/Upload (vu jusqu'à 200 Mo).

    Le corps transite par un SpooledTemporaryFile (disque au-delà de 5 Mo).
    """
    c = get_client()
    tmp, size = _spool_upload(file)
    try:
        tmp.seek(0)
        return c.upload_large_single_shot(folder, filename, tmp.read())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))
    finally:
        tmp.close()


@app.post("/api/upload-auto", dependencies=[Depends(require_key)])
async def api_upload_auto(folder: str = Form(...), filename: str = Form(...), file: UploadFile = File(...)):
    try:
        tmp, size = _spool_upload(file)
        try:
            import os as _os

            thr = int(_os.getenv("OD_LARGE_THRESHOLD_MB", "100")) * 1024 * 1024
            tmp.seek(0)
            if size <= thr:
                return get_client().upload_small(folder, filename, tmp.read())
            tmp.seek(0)
            data = tmp.read()
            return get_client().upload_auto(folder, filename, data)
        finally:
            tmp.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.post("/api/cancel-upload", dependencies=[Depends(require_key)])
def api_cancel(body: CancelIn):
    try:
        get_client().cancel_upload(body.file, body.uploadId)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.get("/api/delta", dependencies=[Depends(require_key)])
def api_delta(token: str):
    try:
        return get_client().delta(token)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.get("/api/favorites", dependencies=[Depends(require_key)])
def api_fav():
    try:
        return get_client().favorites()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.get("/api/quick-access", dependencies=[Depends(require_key)])
def api_qa():
    try:
        return get_client().quick_access()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


@app.delete("/api/file", dependencies=[Depends(require_key)])
def api_delete_file(path: str):
    """Supprime un fichier OneDrive (SharePoint REST standard, vérifié live)."""
    try:
        get_client().delete_file(path)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


def _cache_dir() -> str:
    d = os.getenv("OD_CACHE_DIR", "/data/data/com.termux/files/usr/tmp/opencode/od_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(server_path: str) -> str:
    import hashlib

    return os.path.join(_cache_dir(), hashlib.sha256(server_path.encode()).hexdigest() + ".bin")


@app.get("/api/file")
def api_file(req: Request, path: str, refresh: bool = False):
    """Proxy streaming d'un fichier OneDrive (pour .strm Jellyfin). Supporte Range (206).

    Auth : header X-API-Key OU ?api_key= (les .strm ne portent pas de headers).
    Cache disque : si SharePoint ignore le Range, le fichier complet est mis en
    cache et les seeks suivants sont servis depuis le disque (instantané).
    ?refresh=1 force le re-téléchargement.
    """
    require_key_or_query(req)
    try:
        c = get_client()
        range_h = req.headers.get("Range", "")
        m = re.match(r"bytes=(\d+)-(\d*)$", range_h.strip())
        cp = _cache_path(path)
        if m and os.path.exists(cp) and not refresh:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else None
            size = os.path.getsize(cp)
            last = (size - 1) if end is None else min(end, size - 1)
            with open(cp, "rb") as fh:
                fh.seek(start)
                data = fh.read(last - start + 1)
            return Response(content=data, status_code=206,
                            headers={"Accept-Ranges": "bytes",
                                     "Content-Range": f"bytes {start}-{last}/{size}"},
                            media_type="application/octet-stream")
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else None
            _status, data, total = c.download_range(path, start, end)
            headers = {"Accept-Ranges": "bytes"}
            if total is not None:
                last = (total - 1) if end is None else min(end, total - 1)
                headers["Content-Range"] = f"bytes {start}-{last}/{total}"
            return Response(content=data, status_code=206, headers=headers,
                            media_type="application/octet-stream")
        data = c.download_file(path)
        try:
            with open(cp, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
        return Response(content=data, status_code=200,
                        headers={"Accept-Ranges": "bytes"},
                        media_type="application/octet-stream")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, _public_error(e))


if __name__ == "__main__":
    import uvicorn

    _server_key()  # refuse si pas de clé
    uvicorn.run(app, host=os.getenv("OD_HOST", "127.0.0.1"), port=int(os.getenv("OD_PORT", "8000")))
