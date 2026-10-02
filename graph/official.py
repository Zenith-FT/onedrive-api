"""Client officiel Microsoft Graph (OneDrive) — recodage propre des fonctions essentielles.

Source doc: https://learn.microsoft.com/en-us/graph/api/resources/onedrive (v1.0)
Base: https://graph.microsoft.com/v1.0
Auth: Authorization: Bearer {token} (OAuth2 délégué ou app). Token via env OD_GRAPH_TOKEN.
  Permissions minimales: Files.Read (lecture), Files.ReadWrite (écriture).

Fonctions couvertes (12):
  1. get_item / get_root ................ GET /me/drive/root, /me/drive/items/{id}, /me/drive/root:/{path}
  2. list_children ...................... GET /me/drive/root/children, /items/{id}/children, /root:/{path}:/children
  3. create_folder ...................... POST /me/drive/root/children | /items/{parent}/children {"name","folder":{}}
  4. upload_small (<=250Mo) ............. PUT /me/drive/items/{parent}:/{file}:/content | /items/{id}/content
  5. upload_large (session) ............. POST .../createUploadSession + PUT uploadUrl (ranges multiples 320Kio, <60Mio/req)
  6. download (302 + Range) ............. GET /me/drive/items/{id}/content -> suit 302; Range sur downloadUrl
  7. delete (corbeille, 204) ............ DELETE /me/drive/items/{id}
  8. rename / move (PATCH) .............. PATCH /me/drive/items/{id} {"name", "parentReference":{...}}
  9. copy (async 202 + monitor) ......... POST /me/drive/items/{id}/copy {"parentReference","name"} -> Location monitor
 10. search ............................. GET /me/drive/root/search(q='...') | /drives/{id}/root/search(q='...')
 11. delta (sync) ....................... GET /me/drive/root/delta (?token=...), suit nextLink -> deltaLink
 12. create_link (partage) .............. POST /me/drive/items/{id}/createLink {"type","scope",...} -> Permission

Usage:
  export OD_GRAPH_TOKEN="EwD0..."
  from official import GraphDriveClient
  c = GraphDriveClient.from_env()
  c.list_children_path("Documents/SchoolMouv")
"""
from __future__ import annotations
import os
import time
import urllib.parse

try:
    from curl_cffi.requests import Session as CurlSession
    _HAS_CURL = True
except ImportError:
    CurlSession = None  # type: ignore
    _HAS_CURL = False
import requests

GRAPH = "https://graph.microsoft.com/v1.0"


def _qs_path(p: str) -> str:
    # Graph: /me/drive/root:/chemin:/... — encode chaque segment, garde les "/"
    return "/".join(urllib.parse.quote(s, safe="") for s in p.strip("/").split("/"))


class GraphDriveClient:
    """Wrapper fin et documenté autour de Graph v1.0 (Drive/DriveItem)."""

    def __init__(self, token: str | None = None, session=None, drive: str = "me"):
        self.token = token or os.getenv("OD_GRAPH_TOKEN", "")
        if not self.token:
            raise ValueError("OD_GRAPH_TOKEN manquant (Bearer OAuth2 requis pour l'API officielle)")
        self.drive_prefix = "/me/drive" if drive == "me" else f"/drives/{drive}"
        if session is not None:
            self.s = session
        elif _HAS_CURL:
            self.s = CurlSession(impersonate=os.getenv("OD_IMPERSONATE", "chrome124"))  # type: ignore
        else:
            self.s = requests.Session()

    @classmethod
    def from_env(cls) -> "GraphDriveClient":
        return cls()

    def _h(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _url(self, suffix: str) -> str:
        return f"{GRAPH}{suffix}"

    @staticmethod
    def _retry_after(resp) -> float | None:
        try:
            h = resp.headers or {}
            v = h.get("Retry-After") or h.get("retry-after")
            return float(str(v).split(",")[0].strip()) if v is not None else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _do(self, method: str, url: str, headers: dict | None = None, **kw):
        """Requête avec retry 429/5xx (backoff exponentiel, respecte Retry-After)."""
        last = None
        for attempt in range(4):
            fn = getattr(self.s, method)
            r = fn(url, headers=headers, **kw)
            if r.status_code in (429,) + tuple(range(500, 600)):
                last = r
                ra = self._retry_after(r)
                time.sleep(ra if ra is not None else min(0.5 * (2 ** attempt), 8.0))
                continue
            return r
        return last

    # ---------- 1. get ----------
    def get_root(self) -> dict:
        """GET {drive}/root — métadonnées racine. 200 + DriveItem."""
        r = self.s.get(self._url(f"{self.drive_prefix}/root"), headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def get_item(self, item_id: str, expand: str | None = None, select: str | None = None) -> dict:
        """GET {drive}/items/{id} — métadonnées par ID. Support $expand, $select."""
        q = ""
        ps = []
        if expand:
            ps.append(f"$expand={urllib.parse.quote(expand)}")
        if select:
            ps.append(f"$select={urllib.parse.quote(select)}")
        if ps:
            q = "?" + "&".join(ps)
        r = self.s.get(self._url(f"{self.drive_prefix}/items/{item_id}{q}"), headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def get_path(self, path: str) -> dict:
        """GET {drive}/root:/{path} — métadonnées par chemin relatif à la racine."""
        r = self.s.get(self._url(f"{self.drive_prefix}/root:/{_qs_path(path)}"), headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    # ---------- 2. list children ----------
    def list_children(self, item_id: str = "root", top: int | None = None, orderby: str | None = None) -> dict:
        """GET {drive}/items/{id}/children — enfants d'un dossier. 200 {value, @odata.nextLink}."""
        q = ""
        ps = []
        if top:
            ps.append(f"$top={int(top)}")
        if orderby:
            ps.append(f"$orderby={urllib.parse.quote(orderby)}")
        if ps:
            q = "?" + "&".join(ps)
        base = f"{self.drive_prefix}/root/children" if item_id == "root" else f"{self.drive_prefix}/items/{item_id}/children"
        r = self.s.get(self._url(base + q), headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def list_children_path(self, path: str, top: int | None = None) -> dict:
        """GET {drive}/root:/{path}:/children — enfants par chemin."""
        q = f"?$top={int(top)}" if top else ""
        r = self.s.get(self._url(f"{self.drive_prefix}/root:/{_qs_path(path)}:/children{q}"), headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    # ---------- 3. create folder ----------
    def create_folder(self, name: str, parent_id: str = "root", conflict: str = "rename") -> dict:
        """POST {drive}/items/{parent}/children {"name","folder":{}} — 201 + DriveItem."""
        base = f"{self.drive_prefix}/root/children" if parent_id == "root" else f"{self.drive_prefix}/items/{parent_id}/children"
        body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": conflict}
        r = self.s.post(self._url(base), headers=self._h({"Content-Type": "application/json"}), json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---------- 4. upload small (<=250Mo, 1 appel) ----------
    def upload_small(self, data: bytes, filename: str, parent_id: str = "root", content_type: str = "application/octet-stream") -> dict:
        """PUT {drive}/items/{parent}:/{file}:/content — 200/201 + DriveItem."""
        if len(data) > 250 * 1024 * 1024:
            raise ValueError("upload_small limité à 250Mo, utilise upload_large()")
        fn = urllib.parse.quote(filename, safe="")
        base = f"{self.drive_prefix}/root:/{fn}:/content" if parent_id == "root" else f"{self.drive_prefix}/items/{parent_id}:/{fn}:/content"
        r = self._put_bytes(self._url(base), data, headers=self._h({"Content-Type": content_type}), timeout=120)
        if r is None:
            raise RuntimeError("upload_small a renvoyé une réponse vide (échec inattendu).")
        r.raise_for_status()
        return r.json()

    def update_content(self, item_id: str, data: bytes, content_type: str = "application/octet-stream") -> dict:
        """PUT {drive}/items/{id}/content — remplace le contenu d'un fichier existant."""
        r = self._put_bytes(
            self._url(f"{self.drive_prefix}/items/{item_id}/content"),
            data,
            headers=self._h({"Content-Type": content_type}),
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    # ---------- 5. upload large (session, ranges) ----------
    def create_upload_session(self, filename: str, parent_id: str = "root", conflict: str = "rename", defer_commit: bool = False) -> dict:
        """POST .../createUploadSession — 200 {uploadUrl, expirationDateTime}."""
        fn = urllib.parse.quote(filename, safe="")
        base = (
            f"{self.drive_prefix}/root:/{fn}:/createUploadSession"
            if parent_id == "root"
            else f"{self.drive_prefix}/items/{parent_id}:/{fn}:/createUploadSession"
        )
        body: dict = {"item": {"@microsoft.graph.conflictBehavior": conflict, "name": filename}}
        if defer_commit:
            body["deferCommit"] = True
        r = self.s.post(self._url(base), headers=self._h({"Content-Type": "application/json"}), json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put_bytes(self, url: str, data: bytes, headers: dict, timeout: int = 120):
        if _HAS_CURL and isinstance(self.s, CurlSession):  # type: ignore
            return self.s.put(url, headers=headers, content=data, timeout=timeout)
        return self.s.put(url, headers=headers, data=data, timeout=timeout)

    @staticmethod
    def check_range_size(n: int) -> None:
        """Chaque fragment doit être multiple de 320Kio et <60Mio (doc officielle)."""
        if n > 60 * 1024 * 1024:
            raise ValueError("fragment >60Mio interdit")
        if n % (320 * 1024) != 0:
            raise ValueError("fragment doit être multiple de 320Kio (327680 o)")

    def upload_chunk(self, upload_url: str, data: bytes, offset: int, total: int) -> dict:
        """PUT uploadUrl avec Content-Range: bytes {off}-{off+len-1}/{total}.

        Retour: 202 {nextExpectedRanges} si incomplet, 200/201 + DriveItem si fini.
        Note doc: PAS de header Authorization sur l'uploadUrl pré-authentifié.
        """
        end = offset + len(data) - 1
        h = {"Content-Length": str(len(data)), "Content-Range": f"bytes {offset}-{end}/{total}"}
        # uploadUrl pré-signée: pas de Bearer
        if _HAS_CURL and isinstance(self.s, CurlSession):  # type: ignore
            r = self.s.put(upload_url, headers=h, content=data, timeout=300)
        else:
            r = self.s.put(upload_url, headers=h, data=data, timeout=300)
        if r.status_code in (200, 201, 202):
            return {"status": r.status_code, "body": r.json() if r.content else {}}
        r.raise_for_status()
        return {}

    def cancel_upload_session(self, upload_url: str) -> bool:
        """DELETE uploadUrl — annule une session d'upload (nettoie le fichier temporaire)."""
        r = self.s.delete(upload_url, timeout=30)
        if r.status_code in (200, 204):
            return True
        r.raise_for_status()
        return True

    @staticmethod
    def _next_offset(next_ranges: list | None, fallback: int) -> int:
        """Reprend via nextExpectedRanges (ex: ["26-"] -> 26)."""
        try:
            first = (next_ranges or [""])[0]
            return int(str(first).split("-")[0] or fallback)
        except (ValueError, TypeError, IndexError):
            return fallback

    def upload_large(
        self,
        data: bytes,
        filename: str,
        parent_id: str = "root",
        chunk_size: int = 10 * 1024 * 1024,
        conflict: str = "rename",
    ) -> dict:
        """Enchaîne createUploadSession + PUT ranges (320 Kio multiples) avec reprise.

        Reprise via nextExpectedRanges après chaque 202 ; annulation (DELETE
        uploadUrl) en cas d'erreur. Retourne le DriveItem final.
        """
        if chunk_size % (320 * 1024) != 0:
            raise ValueError("chunk_size doit être multiple de 320 Kio")
        self.check_range_size(chunk_size)
        sess = self.create_upload_session(filename, parent_id, conflict)
        upload_url = sess.get("uploadUrl", "")
        if not upload_url:
            raise KeyError("uploadUrl absente de createUploadSession")
        total = len(data)
        offset = 0
        try:
            while offset < total:
                chunk = data[offset:offset + chunk_size]
                # dernier fragment peut ne pas être multiple de 320K (doc officielle)
                res = self.upload_chunk(upload_url, chunk, offset, total)
                if res.get("status") in (200, 201):
                    return res["body"]
                nxt = self._next_offset(res.get("body", {}).get("nextExpectedRanges"), offset + len(chunk))
                if nxt <= offset:
                    raise RuntimeError("upload_large: nextExpectedRanges n'a pas progressé")
                offset = nxt
            raise RuntimeError("upload_large: boucle terminée sans DriveItem final")
        except Exception:
            try:
                self.cancel_upload_session(upload_url)
            except Exception:
                pass
            raise

    # ---------- 6. download ----------
    def download(self, item_id: str, range_bytes: tuple[int, int] | None = None, follow: bool = True) -> bytes:
        """GET {drive}/items/{id}/content — suit la 302 vers downloadUrl pré-authentifiée.

        Range: à appliquer sur la downloadUrl finale (doc officielle), ici simplifié
        via header Range sur l'appel initial si follow=True et backend le supporte.
        """
        h = self._h()
        if range_bytes and follow:
            h["Range"] = f"bytes={range_bytes[0]}-{range_bytes[1]}"
        r = self.s.get(self._url(f"{self.drive_prefix}/items/{item_id}/content"), headers=h, timeout=120)
        r.raise_for_status()
        return r.content or b""

    def download_url(self, item_id: str) -> str:
        """GET {drive}/items/{id}?select=id,@microsoft.graph.downloadUrl — URL pré-signée (JS/CORS)."""
        r = self.s.get(
            self._url(f"{self.drive_prefix}/items/{item_id}?$select=id,@microsoft.graph.downloadUrl"),
            headers=self._h(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("@microsoft.graph.downloadUrl", "")

    # ---------- 7. delete ----------
    def delete(self, item_id: str) -> bool:
        """DELETE {drive}/items/{id} — envoie à la corbeille. 204 si OK."""
        r = self.s.delete(self._url(f"{self.drive_prefix}/items/{item_id}"), headers=self._h(), timeout=30)
        if r.status_code == 204:
            return True
        r.raise_for_status()
        return True

    # ---------- 8. rename / move ----------
    def rename(self, item_id: str, new_name: str) -> dict:
        """PATCH {drive}/items/{id} {"name": ...} — renomme. 200 + DriveItem."""
        r = self.s.patch(
            self._url(f"{self.drive_prefix}/items/{item_id}"),
            headers=self._h({"Content-Type": "application/json"}),
            json={"name": new_name},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def move(self, item_id: str, new_parent_id: str, new_name: str | None = None) -> dict:
        """PATCH {drive}/items/{id} {"parentReference":{"id":...}} — déplace (+renomme opt)."""
        body: dict = {"parentReference": {"id": new_parent_id}}
        if new_name:
            body["name"] = new_name
        r = self.s.patch(
            self._url(f"{self.drive_prefix}/items/{item_id}"),
            headers=self._h({"Content-Type": "application/json"}),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ---------- 9. copy (async) ----------
    def copy(self, item_id: str, dest_drive_id: str, dest_folder_id: str, name: str | None = None, conflict: str = "fail") -> str:
        """POST {drive}/items/{id}/copy — 202 Accepted + header Location (monitor).

        Retourne l'URL de monitoring à GET jusqu'à status completed/failed.
        """
        params = f"?@microsoft.graph.conflictBehavior={conflict}" if conflict != "fail" else ""
        body: dict = {"parentReference": {"driveId": dest_drive_id, "id": dest_folder_id}}
        if name:
            body["name"] = name
        r = self.s.post(
            self._url(f"{self.drive_prefix}/items/{item_id}/copy{params}"),
            headers=self._h({"Content-Type": "application/json"}),
            json=body,
            timeout=30,
        )
        if r.status_code != 202:
            r.raise_for_status()
        loc = r.headers.get("Location", "") if hasattr(r, "headers") else ""
        return loc

    # ---------- 10. search ----------
    def search(self, query: str, top: int | None = None) -> dict:
        """GET {drive}/root/search(q='...') — recherche nom/contenu. 200 {value, @odata.nextLink}."""
        q = urllib.parse.quote(query, safe="")
        url = self._url(f"{self.drive_prefix}/root/search(q='{q}')")
        if top:
            url += f"?$top={int(top)}"
        r = self._do("get", url, headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json()

    def _collect_nextlink(self, first: dict) -> dict:
        """Suit @odata.nextLink (avec retry 429/5xx) et concatène value[]."""
        items = list(first.get("value", []) or [])
        url = first.get("@odata.nextLink")
        pages = 1
        while url and pages < 50:
            r = self._do("get", url, headers=self._h(), timeout=30)
            r.raise_for_status()
            page = r.json()
            items.extend(page.get("value", []) or [])
            url = page.get("@odata.nextLink")
            pages += 1
        out = dict(first)
        out["value"] = items
        out.pop("@odata.nextLink", None)
        return out

    def list_children_all(self, item_id: str = "root") -> dict:
        """Liste TOUTES les pages children (suit @odata.nextLink)."""
        return self._collect_nextlink(self.list_children(item_id))

    def search_all(self, query: str) -> dict:
        """Recherche TOUTES les pages (suit @odata.nextLink)."""
        return self._collect_nextlink(self.search(query))

    # ---------- 11. delta ----------
    def delta(self, token: str | None = None) -> dict:
        """GET {drive}/root/delta(?token=...) — suivi changements.

        Sans token: énumération complète (suivre @odata.nextLink).
        Retourne @odata.nextLink (page suivante) ou @odata.deltaLink (point de reprise).
        token='latest': deltaLink vide sans énumérer (doc officielle).
        """
        url = self._url(f"{self.drive_prefix}/root/delta")
        if token:
            url += f"?token={urllib.parse.quote(token, safe='')}"
        r = self._do("get", url, headers=self._h(), timeout=60)
        r.raise_for_status()
        return r.json()

    def delta_all(self, token: str | None = None) -> dict:
        """Énumère TOUTES les pages delta jusqu'au @odata.deltaLink."""
        first = self.delta(token)
        items = list(first.get("value", []) or [])
        url = first.get("@odata.nextLink")
        delta_link = first.get("@odata.deltaLink")
        while url and not delta_link:
            r = self._do("get", url, headers=self._h(), timeout=60)
            r.raise_for_status()
            page = r.json()
            items.extend(page.get("value", []) or [])
            url = page.get("@odata.nextLink")
            delta_link = page.get("@odata.deltaLink")
        return {"value": items, "@odata.deltaLink": delta_link}

    # ---------- 12. createLink ----------
    def create_link(
        self,
        item_id: str,
        type: str = "view",
        scope: str = "anonymous",
        password: str | None = None,
        expiration: str | None = None,
    ) -> dict:
        """POST {drive}/items/{id}/createLink — 200/201 + Permission {link:{webUrl}}.

        type: view|edit|embed (embed=OneDrive perso uniquement).
        scope: anonymous|organization|users (défaut org).
        """
        if type not in ("view", "edit", "embed"):
            raise ValueError("type doit être view|edit|embed")
        body: dict = {"type": type, "scope": scope}
        if password:
            body["password"] = password
        if expiration:
            body["expirationDateTime"] = expiration
        r = self.s.post(
            self._url(f"{self.drive_prefix}/items/{item_id}/createLink"),
            headers=self._h({"Content-Type": "application/json"}),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
