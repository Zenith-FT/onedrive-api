"""Client minimal reproduisant les endpoints internes OneDrive/SharePoint observés dans le HAR.

Endpoints couverts (tous same-origin, auth par cookies FedAuth+rtFa + X-RequestDigest):
 1. POST {personal}/_api/web/folders/AddUsingPath(DecodedUrl=@a1,overwrite=@a2)
 2. POST {personal}/_api/web/GetListUsingPath(DecodedUrl=@a1)/RenderListDataAsStream
 3. POST {personal}/_api/web/GetFolderByServerRelativePath(DecodedUrl=@a1)/Files/AddUsingPath(...)
 4. POST {personal}/_api/web/GetFolderByServerRelativePath(DecodedUrl=@a1)/Files/AddStubUsingPath(...)/Upload(...)
 5. POST {personal}/_api/web/GetFileByServerRelativePath(DecodedUrl=@a1)/CancelUpload(uploadId=@a2)
 6. GET  /_api/v2.0/drives/{driveId}/root/view.delta
 7. GET  {personal}/_api/sp.FavoriteItemCollection.GetFavoriteItems
 8. GET  {personal}/_api/sp.QuickAccessItemCollection.GetQuickAccessItems
 9. GET  {personal}/_api/web/lists/GetByTitle('microsoft.ListSync.Endpoints')/items
10. POST {personal}/_api/contextinfo (standard SharePoint, pour obtenir le digest)

Auth: FedAuth/rtFa via env (jamais en dur, jamais loggés):
  OD_COOKIES="FedAuth=...; rtFa=...; ..." ou OD_COOKIES_FILE=/path/cookies.txt
  OD_DIGEST="0x..." (optionnel, sinon auto-fetch via contextinfo)
  OD_TENANT, OD_PERSONAL_PATH, OD_DRIVE_ID (requis, voir config.py)
  OD_MIN_DELAY=0.2 (délai min entre appels, anti-rafale)

Usage:
  from client import OneDriveInternalClient
  c = OneDriveInternalClient.from_env()
  print(c.list_documents("/Documents"))
  c.create_folder("/personal/prenom_nom_domaine_fr/Documents/Exemple")
"""
from __future__ import annotations
import os
import time
import uuid
import urllib.parse

try:
    from curl_cffi.requests import Session as CurlSession
    _HAS_CURL_CFFI = True
except ImportError:
    CurlSession = None  # type: ignore
    _HAS_CURL_CFFI = False
import requests

try:
    from . import config
except ImportError:
    import config


def _make_session(impersonate: str | None = None):
    """Session par défaut: curl_cffi (impersonate chrome) si dispo, sinon requests."""
    if _HAS_CURL_CFFI:
        return CurlSession(impersonate=impersonate or os.getenv("OD_IMPERSONATE", "chrome124"))  # type: ignore
    return requests.Session()


def _q(s: str) -> str:
    """Quote SharePoint: @a1="'<path>'" URL-encodé (apostrophes incluses)."""
    return urllib.parse.quote(f"'{s}'", safe="")


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """Backoff exponentiel (0.5s, 1s, 2s... plafonné 8s), respecte Retry-After si présent."""
    if retry_after:
        try:
            return max(0.0, float(str(retry_after).split(",")[0].strip()))
        except ValueError:
            pass
    return min(0.5 * (2 ** attempt), 8.0)


class OneDriveInternalClient:
    """Client interne SharePoint (cookies + digest). Aucun secret loggé."""

    def __init__(
        self,
        cookies: str | dict | None = None,
        digest: str | None = None,
        session=None,
        impersonate: str | None = None,
        min_delay: float | None = None,
    ) -> None:
        self.tenant: str = config.TENANT
        self.personal_path: str = config.PERSONAL_PATH
        self.drive_id: str = config.DRIVE_ID
        self.base: str = config.personal_base()
        self.api: str = config.api_base()
        self.impersonate = impersonate or os.getenv("OD_IMPERSONATE", "chrome124")
        self.backend = "curl_cffi" if _HAS_CURL_CFFI and session is None else "requests"
        if session is not None:
            self.backend = "mock" if "Mock" in type(session).__name__ else self.backend
        self.s = session or _make_session(self.impersonate)
        self.min_delay = float(os.getenv("OD_MIN_DELAY", "0.2")) if min_delay is None else float(min_delay)
        self._last_call = 0.0
        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "application/json;odata=verbose",
                "Origin": f"https://{self.tenant}",
                "Referer": f"https://{self.tenant}/my",
            }
        )
        if isinstance(cookies, str):
            if cookies:
                self.s.headers["Cookie"] = cookies
        elif isinstance(cookies, dict):
            self.s.cookies.update(cookies)
        self._digest = digest or os.getenv("OD_DIGEST")

    @classmethod
    def from_env(cls) -> "OneDriveInternalClient":
        """Construit depuis OD_COOKIES ou OD_COOKIES_FILE (jamais loggés)."""
        cookies = os.getenv("OD_COOKIES", "")
        cf = os.getenv("OD_COOKIES_FILE")
        if cf and os.path.exists(cf):
            with open(cf) as fh:
                cookies = fh.read().strip()
        return cls(cookies=cookies)

    # ---------- helpers ----------
    def _throttle(self) -> None:
        if self.min_delay <= 0:
            return
        now = time.monotonic()
        wait = self.min_delay - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    @staticmethod
    def _retry_after(resp) -> str | None:
        try:
            h = resp.headers or {}
            v = h.get("Retry-After") or h.get("retry-after")
            return str(v) if v is not None else None
        except Exception:
            return None

    # ---------- digest ----------
    def get_digest(self) -> str:
        """POST {personal}/_api/contextinfo -> FormDigestValue (avec retry 429/5xx)."""
        url = f"{self.api}/contextinfo"
        headers = {
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/json;odata=verbose",
        }
        last: Exception | None = None
        for attempt in range(4):
            self._throttle()
            try:
                r = self.s.post(url, headers=headers, data="{}", timeout=30)
            except Exception as ex:  # réseau: backoff puis retry
                last = ex
                time.sleep(_retry_delay(attempt, None))
                continue
            if r.status_code in (429,) + tuple(range(500, 600)):
                time.sleep(_retry_delay(attempt, self._retry_after(r)))
                last = RuntimeError(f"contextinfo HTTP {r.status_code}")
                continue
            r.raise_for_status()
            try:
                j = r.json()
                dv = j["d"]["GetContextWebInformation"]["FormDigestValue"]
            except KeyError as ex:
                raise KeyError(f"FormDigestValue introuvable dans contextinfo: {ex}") from ex
            self._digest = dv
            return dv
        raise RuntimeError(f"contextinfo a échoué après retries: {last}")

    def _ensure_digest(self) -> str:
        if not self._digest:
            return self.get_digest()
        return self._digest

    def _post_headers(self, extra: dict | None = None) -> dict:
        h = {
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/json;odata=verbose",
            "X-RequestDigest": self._ensure_digest(),
            "X-ClientService-ClientTag": "ODB Web",
            "X-ms-cc": "t",
        }
        if extra:
            h.update(extra)
        return h

    def _post(self, url: str, headers: dict | None = None, **kw):
        """POST avec refresh digest sur 403 + retry 429/5xx (backoff, respecte Retry-After)."""
        last = None
        for attempt in range(4):
            self._throttle()
            r = self.s.post(url, headers=headers, **kw)
            body = (r.content or b"")[:500].lower()
            if r.status_code == 403 and (
                b"security validation" in body or b"request digest" in body or b"expired" in body
            ):
                self._digest = None  # force refresh via contextinfo
                h2 = dict(headers or {})
                h2["X-RequestDigest"] = self._ensure_digest()
                self._throttle()
                r = self.s.post(url, headers=h2, **kw)
                return r
            if r.status_code in (429,) + tuple(range(500, 600)):
                last = r
                time.sleep(_retry_delay(attempt, self._retry_after(r)))
                continue
            return r
        return last

    def _get(self, url: str, headers: dict | None = None, **kw):
        """GET avec retry 429/5xx (backoff, respecte Retry-After)."""
        last = None
        for attempt in range(4):
            self._throttle()
            r = self.s.get(url, headers=headers, **kw)
            if r.status_code in (429,) + tuple(range(500, 600)):
                last = r
                time.sleep(_retry_delay(attempt, self._retry_after(r)))
                continue
            return r
        return last

    # ---------- 1. create folder ----------
    def create_folder(self, server_relative_path: str, overwrite: bool = False) -> dict:
        """POST .../folders/AddUsingPath. Body observé: {}."""
        url = (
            f"{self.api}/web/folders/AddUsingPath(DecodedUrl=@a1,overwrite=@a2)"
            f"?@a1={_q(server_relative_path)}"
            f"&@a2={'true' if overwrite else 'false'}"
            f"&$Expand=ListItemAllFields/PermMask"
        )
        r = self._post(url, headers=self._post_headers(), json={}, timeout=60)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ---------- 2. list / RenderListDataAsStream ----------
    @staticmethod
    def _extract_next_token(resp: dict) -> str | None:
        """Cherche un token de page suivante (NextHref/NextPageToken/nextLink)."""
        if not isinstance(resp, dict):
            return None
        for k in ("NextHref", "nextHref", "NextPageToken", "nextPageToken", "@odata.nextLink"):
            v = resp.get(k)
            if v:
                return str(v)
        for container in ("ListData", "d"):
            sub = resp.get(container)
            if isinstance(sub, dict):
                for k in ("NextHref", "nextHref", "NextPageToken", "@odata.nextLink"):
                    v = sub.get(k)
                    if v:
                        return str(v)
                inner = sub.get("ListData")
                if isinstance(inner, dict):
                    for k in ("NextHref", "nextHref", "NextPageToken"):
                        v = inner.get(k)
                        if v:
                            return str(v)
        return None

    @staticmethod
    def _extract_rows(resp: dict) -> list:
        if not isinstance(resp, dict):
            return []
        for path in (("ListData", "Row"), ("d", "ListData", "Row"), ("value",), ("d", "results")):
            node: object = resp
            ok = True
            for k in path:
                if isinstance(node, dict) and k in node:
                    node = node[k]
                else:
                    ok = False
                    break
            if ok and isinstance(node, list):
                return node
        return []

    def list_documents(
        self,
        library_relative_path: str = "/Documents",
        view_id: str | None = None,
        body: dict | None = None,
        all_pages: bool = False,
    ) -> dict:
        """POST .../GetListUsingPath(...)/RenderListDataAsStream. Body observé dans HAR.

        all_pages=True suit le token de pagination (NextHref/NextPageToken) et
        concatène les lignes dans ListData.Row.
        """
        if library_relative_path.startswith("/personal/"):
            lib = library_relative_path
        else:
            lib = (
                f"{self.personal_path}{library_relative_path}"
                if library_relative_path.startswith("/")
                else f"{self.personal_path}/Documents/{library_relative_path}"
            )
        base = f"{self.api}/web/GetListUsingPath(DecodedUrl=@a1)/RenderListDataAsStream?@a1={_q(lib)}"
        if view_id:
            base += f"&View={view_id}"
        base += "&TryNewExperienceSingle=TRUE"
        payload = body or {
            "parameters": {
                "__metadata": {"type": "SP.RenderListDataParameters"},
                "RenderOptions": 1496839,
                "AllowMultipleValueFilterForTaxonomyFields": True,
                "AddRequiredFields": True,
                "RequireFolderColoringFields": True,
            }
        }
        if not all_pages:
            r = self._post(base, headers=self._post_headers(), json=payload, timeout=60)
            r.raise_for_status()
            return r.json()
        rows: list = []
        pages = 0
        url = base
        current_payload: dict | None = dict(payload)
        while True:
            r = self._post(url, headers=self._post_headers(), json=current_payload, timeout=60)
            r.raise_for_status()
            resp = r.json()
            rows.extend(self._extract_rows(resp))
            pages += 1
            nxt = self._extract_next_token(resp)
            if not nxt or pages >= 50:
                break
            if nxt.startswith("http"):
                url = nxt
                current_payload = None
            else:
                url = base + ("&" if "?" in base else "?") + nxt.lstrip("&")
        return {"ListData": {"Row": rows}, "pages": pages}

    # ---------- 2b. list folder (SharePoint REST standard, vérifié live) ----------
    def list_folder(self, folder_relative_path: str, top: int = 100) -> list[dict]:
        """GET .../GetFolderByServerRelativePath(...)/Files -> [{Name, Length, ...}].

        Contrairement à RenderListDataAsStream (racine seule), ceci liste un
        SOUS-dossier précis. Vérifié live (dossier Jellyfin).
        """
        url = (
            f"{self.api}/web/GetFolderByServerRelativePath(DecodedUrl=@a1)/Files"
            f"?@a1={_q(folder_relative_path)}&$top={int(top)}"
        )
        r = self._get(url, headers={"Accept": "application/json;odata=verbose"}, timeout=30)
        r.raise_for_status()
        d = r.json().get("d", {})
        return d.get("results", []) or []

    # ---------- 3. upload petit fichier ----------
    def upload_small(
        self,
        folder_relative_path: str,
        filename: str,
        data: bytes,
        xor_hash: str | None = None,
    ) -> dict:
        """POST .../Files/AddUsingPath. Content-Type: application/octet-stream.

        XorHash ajouté seulement s'il est fourni.
        """
        if xor_hash:
            sig = "Files/AddUsingPath(DecodedUrl=@a2,AutoCheckoutOnInvalidData=@a3,XorHash=@a4)"
            suffix = (
                f"?@a1={_q(folder_relative_path)}"
                f"&@a2={_q(filename)}"
                f"&@a3=true"
                f"&@a4={urllib.parse.quote(f'{xor_hash}', safe='')}"
            )
        else:
            sig = "Files/AddUsingPath(DecodedUrl=@a2,AutoCheckoutOnInvalidData=@a3)"
            suffix = f"?@a1={_q(folder_relative_path)}&@a2={_q(filename)}&@a3=true"
        url = (
            f"{self.api}/web/GetFolderByServerRelativePath(DecodedUrl=@a1)/{sig}"
            + suffix
            + "&$Select=ServerRelativeUrl,UniqueId,Name&$Expand=ListItemAllFields"
        )
        r = self._post(
            url,
            headers={
                **self._post_headers(),
                "Content-Type": "application/octet-stream",
                "Prefer": "RetrieveArchiveFileErrorCode",
            },
            data=data,
            timeout=120,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    # ---------- 4. upload gros fichier (stub + Upload en 1 appel, comme HAR) ----------
    def upload_large_single_shot(
        self, folder_relative_path: str, filename: str, data: bytes, upload_id: str | None = None
    ) -> dict:
        """POST .../Files/AddStubUsingPath(DecodedUrl=@a2)/Upload(uploadId=@a3)."""
        uid = upload_id or str(uuid.uuid4())
        url = (
            f"{self.api}/web/GetFolderByServerRelativePath(DecodedUrl=@a1)/Files/AddStubUsingPath(DecodedUrl=@a2)/Upload(uploadId=@a3)"
            f"?@a1={_q(folder_relative_path)}"
            f"&@a2={_q(filename)}"
            f"&@a3=guid'{uid}'"
            f"&$Select=ServerRelativeUrl,UniqueId,Name&$Expand=ListItemAllFields"
        )
        r = self._post(
            url,
            headers={
                **self._post_headers(),
                "Content-Type": "application/octet-stream",
                "Prefer": "RetrieveArchiveFileErrorCode",
            },
            data=data,
            timeout=300,
        )
        r.raise_for_status()
        out = r.json() if r.content else {}
        if isinstance(out, dict):
            out["_uploadId"] = uid
        return out

    # ---------- 5b. delete (SharePoint REST standard, vérifié live) ----------
    def delete_file(self, file_relative_path: str) -> bool:
        """POST .../GetFileByServerRelativePath(...)/recycle (corbeille). True si 2xx.

        Note : le Delete direct renvoie 404 sur ce tenant, recycle() fonctionne.
        Endpoint SharePoint REST standard (pas observé dans le HAR, vérifié live).
        """
        url = (
            f"{self.api}/web/GetFileByServerRelativePath(DecodedUrl=@a1)/recycle()"
            f"?@a1={_q(file_relative_path)}"
        )
        r = self._post(url, headers=self._post_headers(), json={}, timeout=30)
        r.raise_for_status()
        return True

    # ---------- 5. cancel upload ----------
    def cancel_upload(self, file_relative_path: str, upload_id: str) -> bool:
        """POST .../GetFileByServerRelativePath(...)/CancelUpload. True si 2xx."""
        url = (
            f"{self.api}/web/GetFileByServerRelativePath(DecodedUrl=@a1)/CancelUpload(uploadId=@a2)"
            f"?@a1={_q(file_relative_path)}"
            f"&@a2=guid'{upload_id}'"
        )
        r = self._post(url, headers=self._post_headers(), json={}, timeout=30)
        r.raise_for_status()
        return True

    # ---------- 4b. upload chunké (protocole web observé) ----------
    def get_upload_status(self, file_relative_path: str, upload_id: str) -> dict:
        """GET .../GetFileByServerRelativePath(...)/GetUploadStatus(uploadId=@a2).

        Observé (HAR 14:21) : 200 {"d": {"ExpectedContentRange": "<off>-<total-1>",
        "ExpirationDateTime": ..., "UploadId": ...}} — sert à la reprise.
        """
        url = (
            f"{self.api}/web/GetFileByServerRelativePath(DecodedUrl=@a1)/GetUploadStatus(uploadId=@a2)"
            f"?@a1={_q(file_relative_path)}&@a2=guid'{upload_id}'"
        )
        r = self._get(url, headers={"Accept": "application/json;odata=verbose"}, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    @staticmethod
    def parse_expected_range(status: dict) -> tuple[int | None, int | None]:
        """'25165824-104857599' -> (offset, total). None si absent/illisible."""
        try:
            raw = status["d"]["ExpectedContentRange"]
            off, last = str(raw).split("-")
            return int(off), int(last) + 1
        except (KeyError, ValueError, TypeError, AttributeError):
            return None, None

    def finish_upload_with_checksum(
        self,
        file_relative_path: str,
        upload_id: str,
        offset: int,
        checksum_b64: str,
        data: bytes,
    ) -> dict:
        """POST .../FinishUploadWithChecksum(uploadId=@a2,fileOffset=@a3,checksum=@a4).

        Observé (HAR 14:21, fichier 100 Mio) : offset + corps = taille totale exacte.
        checksum_b64 : base64 28 car. EXPLICITE — algorithme non identifié
        (ni SHA-1 ni MD5 du contenu, voir README). Ne pas inventer de valeur.
        """
        url = (
            f"{self.api}/web/GetFileByServerRelativePath(DecodedUrl=@a1)"
            f"/FinishUploadWithChecksum(uploadId=@a2,fileOffset=@a3,checksum=@a4)"
            f"?@a1={_q(file_relative_path)}"
            f"&@a2=guid'{upload_id}'"
            f"&@a3={int(offset)}"
            f"&@a4={urllib.parse.quote(f'{checksum_b64}', safe='')}"
            f"&$Select=ServerRelativeUrl,UniqueId,Name&$Expand=ListItemAllFields"
        )
        r = self._post(
            url,
            headers={
                **self._post_headers(),
                "Content-Type": "application/octet-stream",
                "Prefer": "RetrieveArchiveFileErrorCode",
            },
            data=data,
            timeout=300,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def upload_large_chunked(
        self,
        folder_relative_path: str,
        filename: str,
        data: bytes,
        first_chunk_mb: int | None = None,
        checksum_b64: str | None = None,
        upload_id: str | None = None,
    ) -> dict:
        """Upload en 2 temps, comme le client web (HAR 14:21) :
        1. AddStubUsingPath/Upload avec le 1er slice (24 Mio observés) ;
        2. FinishUploadWithChecksum avec le reste (offset + corps = total).

        checksum_b64 OBLIGATOIRE si reste non vide (algorithme inconnu, voir README).
        CancelUpload en cas d'erreur.
        """
        import os as _os

        first_size = int(first_chunk_mb or _os.getenv("OD_FIRST_CHUNK_MB", "24")) * 1024 * 1024
        uid = upload_id or str(uuid.uuid4())
        file_path = folder_relative_path.rstrip("/") + "/" + filename
        total = len(data)
        if total == 0:
            raise ValueError("upload_large_chunked: données vides")
        try:
            first = data[:first_size]
            self.upload_large_single_shot(folder_relative_path, filename, first, upload_id=uid)
            rest = data[len(first):]
            if rest:
                if not checksum_b64:
                    raise ValueError(
                        "checksum_b64 requis pour le slice final "
                        "(algorithme non identifié, voir README 'Checksum inconnu')."
                    )
                self.finish_upload_with_checksum(file_path, uid, len(first), checksum_b64, rest)
        except Exception:
            try:
                self.cancel_upload(file_path, uid)
            except Exception:
                pass
            raise
        return {"ServerRelativeUrl": file_path, "Length": total, "_uploadId": uid}

    def upload_auto(
        self,
        folder_relative_path: str,
        filename: str,
        data: bytes,
        threshold_mb: int | None = None,
    ) -> dict:
        """Choisit single-shot vs chunké selon la taille.

        Défaut 100 Mo (env OD_LARGE_THRESHOLD_MB) : le HAR de 100 Mio prouve
        qu'un fichier de 100 Mio passe en 1 seul appel AddStubUsingPath/Upload.
        Au-delà, le comportement web réel est inconnu (chunké = fallback théorique).
        """
        import os as _os

        thr = int(threshold_mb if threshold_mb is not None else _os.getenv("OD_LARGE_THRESHOLD_MB", "100"))
        if len(data) <= thr * 1024 * 1024:
            return self.upload_large_single_shot(folder_relative_path, filename, data)
        return self.upload_large_chunked(folder_relative_path, filename, data)

    # ---------- 6. delta (sync) ----------
    def delta(self, token: str | None = None) -> dict:
        """GET /_api/v2.0/drives/{driveId}/root/view.delta(?token=...).

        Sans token : énumération complète (page 1). Suivre @odata.nextLink,
        garder @odata.deltaLink comme point de reprise.
        """
        url = f"https://{self.tenant}/_api/v2.0/drives/{self.drive_id}/root/view.delta"
        if token:
            url += f"?token={urllib.parse.quote(token, safe='')}"
        r = self._get(url, headers={"Accept": "*/*"}, timeout=60)
        r.raise_for_status()
        return r.json() if r.content else {}

    def delta_all(self, token: str | None = None, max_pages: int = 50) -> dict:
        """Énumère TOUT (suit @odata.nextLink) jusqu'au @odata.deltaLink."""
        first = self.delta(token)
        items = list(first.get("value", []) or [])
        url = first.get("@odata.nextLink")
        delta_link = first.get("@odata.deltaLink")
        pages = 1
        while url and not delta_link and pages < max_pages:
            r = self._get(url, headers={"Accept": "*/*"}, timeout=60)
            r.raise_for_status()
            page = r.json()
            items.extend(page.get("value", []) or [])
            url = page.get("@odata.nextLink")
            delta_link = page.get("@odata.deltaLink")
            pages += 1
        return {"value": items, "@odata.deltaLink": delta_link, "pages": pages}

    # ---------- 6b. download ----------
    def download_file(self, file_relative_path: str) -> bytes:
        """GET .../GetFileByServerRelativePath(...)/$value -> bytes."""
        url = (
            f"{self.api}/web/GetFileByServerRelativePath(DecodedUrl=@a1)/$value"
            f"?@a1={_q(file_relative_path)}"
        )
        r = self._get(url, headers={"Accept": "*/*"}, timeout=120)
        r.raise_for_status()
        return r.content or b""

    def download_range(self, file_relative_path: str, start: int, end: int | None = None) -> tuple[int, bytes, int | None]:
        """GET $value avec Range: bytes=start-end.

        Retourne (status, bytes, total). Si le serveur ignore le Range (200),
        slice local. total = taille complète si déterminable, sinon None.
        """
        url = (
            f"{self.api}/web/GetFileByServerRelativePath(DecodedUrl=@a1)/$value"
            f"?@a1={_q(file_relative_path)}"
        )
        h = {"Accept": "*/*", "Range": f"bytes={start}-{'' if end is None else end}"}
        r = self._get(url, headers=h, timeout=120)
        r.raise_for_status()
        data = r.content or b""
        total: int | None = None
        if r.status_code == 206:
            cr = ""
            try:
                cr = r.headers.get("Content-Range", "")
            except Exception:
                cr = ""
            if "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    total = None
            return 206, data, total
        # 200 : Range ignoré -> slice locale
        total = len(data)
        if end is None:
            return 206, data[start:], total
        return 206, data[start:end + 1], total

    # ---------- 7/8/9. divers GET observés ----------
    def favorites(self, top: int = 500) -> dict:
        """GET .../sp.FavoriteItemCollection.GetFavoriteItems."""
        r = self._get(f"{self.api}/sp.FavoriteItemCollection.GetFavoriteItems?top={top}", timeout=30)
        r.raise_for_status()
        return r.json()

    def quick_access(self) -> dict:
        """GET .../sp.QuickAccessItemCollection.GetQuickAccessItems."""
        r = self._get(f"{self.api}/sp.QuickAccessItemCollection.GetQuickAccessItems", timeout=30)
        r.raise_for_status()
        return r.json()

    def listsync_endpoints(self) -> dict:
        """GET .../web/lists/GetByTitle('microsoft.ListSync.Endpoints')/items."""
        r = self._get(f"{self.api}/web/lists/GetByTitle('microsoft.ListSync.Endpoints')/items", timeout=30)
        r.raise_for_status()
        return r.json()
