"""Tests mock du pont Jellyfin (sans réseau)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("OD_TENANT", "votre-tenant-my.sharepoint.com")
os.environ.setdefault("OD_PERSONAL_PATH", "/personal/prenom_nom_domaine_fr")
os.environ.setdefault("OD_DRIVE_ID", "DRIVE_ID_TEST")
os.environ.setdefault("OD_SERVER_KEY", "KEY_TEST")
os.environ.setdefault("OD_MIN_DELAY", "0")
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import server
from jellyfin.strm import build_library, strm_url, safe_name


def test_strm_build(tmp_path=None):
    import tempfile
    out = tempfile.mkdtemp()
    rows = [
        {"FileLeafRef": "film.mp4", "FileRef": "/personal/x/Documents/film.mp4", "FSObjType": "0"},
        {"FileLeafRef": "doc.pdf", "FileRef": "/personal/x/Documents/doc.pdf", "FSObjType": "0"},
        {"FileLeafRef": "Sous-dossier", "FileRef": "/personal/x/Documents/SD", "FSObjType": "1"},
    ]
    w = build_library(rows, "http://127.0.0.1:8000", "K", out)
    assert len(w) == 1 and w[0].endswith("film.mp4.strm")
    with open(w[0]) as fh:
        body = fh.read().strip()
    assert body == "http://127.0.0.1:8000/api/file?path=%2Fpersonal%2Fx%2FDocuments%2Ffilm.mp4&api_key=K", body
    print("OK test_strm_build")


def test_file_endpoint_auth_and_range():
    import tempfile
    os.environ["OD_CACHE_DIR"] = tempfile.mkdtemp()
    server._CLIENT = None
    os.environ["OD_SERVER_KEY"] = "KEY_TEST"
    c = TestClient(server.app, raise_server_exceptions=False)
    assert c.get("/api/file?path=/x").status_code == 401
    assert c.get("/api/file?path=/x&api_key=BAD").status_code == 401
    # mock client derrière le singleton
    m = MagicMock()
    m.download_range.return_value = (206, b"0123456789"[2:5], 10)
    m.download_file.return_value = b"HELLO"
    server._CLIENT = m
    try:
        r = c.get("/api/file?path=/a.mp4&api_key=KEY_TEST", headers={"Range": "bytes=2-4"})
        assert r.status_code == 206, r.status_code
        assert r.content == b"234", r.content
        assert r.headers.get("Content-Range") == "bytes 2-4/10", r.headers.get("Content-Range")
        r2 = c.get("/api/file?path=/a.mp4&api_key=KEY_TEST")
        assert r2.status_code == 200 and r2.content == b"HELLO"
        r3 = c.get("/api/file?path=/a.mp4", headers={"X-API-Key": "KEY_TEST"})
        assert r3.status_code == 200
    finally:
        server._CLIENT = None
    print("OK test_file_endpoint_auth_and_range")


if __name__ == "__main__":
    test_strm_build()
    test_file_endpoint_auth_and_range()
    print("ALL GREEN")
