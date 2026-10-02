"""Tests mock du serveur (sans réseau): auth 401 + singleton client."""
import os, sys
os.environ.setdefault("OD_TENANT", "votre-tenant-my.sharepoint.com")
os.environ.setdefault("OD_PERSONAL_PATH", "/personal/prenom_nom_domaine_fr")
os.environ.setdefault("OD_DRIVE_ID", "DRIVE_ID_TEST")
os.environ.setdefault("OD_SERVER_KEY", "KEY_TEST")
os.environ.setdefault("OD_MIN_DELAY", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import server


def _client(key="KEY_TEST"):
    server._CLIENT = None
    os.environ["OD_SERVER_KEY"] = key
    return TestClient(server.app, raise_server_exceptions=False)


def test_no_key_401():
    c = _client()
    for url in ("/api/list", "/api/favorites", "/api/quick-access"):
        r = c.get(url)
        assert r.status_code == 401, (url, r.status_code)
    r = c.post("/api/folders", json={"path": "/x"})
    assert r.status_code == 401, r.status_code
    print("OK test_no_key_401")


def test_singleton_reuse():
    c = _client()
    os.environ["OD_COOKIES"] = "FedAuth=x; rtFa=y"
    os.environ["OD_DIGEST"] = "0xD"
    m = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"ListData": {}}
    m.post.return_value = resp
    import client as cli
    real_from_env = cli.OneDriveInternalClient.from_env
    made = []
    orig_init = cli.OneDriveInternalClient.__init__

    def counting_init(self, *a, **k):
        made.append(1)
        k.setdefault("min_delay", 0)
        orig_init(self, *a, **k)
        self.s = m

    cli.OneDriveInternalClient.__init__ = counting_init
    try:
        server._CLIENT = None
        r1 = c.get("/api/list", headers={"X-API-Key": "KEY_TEST"})
        r2 = c.get("/api/list", headers={"X-API-Key": "KEY_TEST"})
        assert r1.status_code == 200 and r2.status_code == 200, (r1.status_code, r2.status_code)
        assert len(made) == 1, f"client créé {len(made)}x, singleton attendu"
    finally:
        cli.OneDriveInternalClient.__init__ = orig_init
        server._CLIENT = None
        os.environ.pop("OD_COOKIES", None)
        os.environ.pop("OD_DIGEST", None)
    print("OK test_singleton_reuse")


if __name__ == "__main__":
    test_no_key_401()
    test_singleton_reuse()
    print("ALL GREEN")
