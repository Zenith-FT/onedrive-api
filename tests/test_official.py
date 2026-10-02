"""Tests mock (sans réseau) du client officiel Graph (module optionnel graph/)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import MagicMock
try:
    from graph.official import GraphDriveClient
except ImportError:
    from official import GraphDriveClient  # ancien emplacement

def _c():
    c = GraphDriveClient(token="T0KEN", session=MagicMock())
    return c

def test_paths():
    c = _c()
    m = c.s
    mk = MagicMock(); mk.json.return_value = {"ok": 1}; mk.content = b"{}"; mk.status_code = 200; mk.headers = {}
    m.get.return_value = mk; m.post.return_value = mk; m.put.return_value = mk
    m.patch.return_value = mk; m.delete.return_value = MagicMock(status_code=204)
    c.get_root(); assert "/me/drive/root" in m.get.call_args[0][0]
    c.get_item("ID123"); assert "/me/drive/items/ID123" in m.get.call_args[0][0]
    c.get_path("A/B f.txt"); assert "/me/drive/root:/A/B%20f.txt" in m.get.call_args[0][0]
    c.list_children(); assert "children" in m.get.call_args[0][0]
    c.list_children_path("Docs"); assert ":/children" in m.get.call_args[0][0]
    c.create_folder("NF"); assert m.post.call_args[1]["json"]["folder"] == {}
    c.search("contoso"); assert "search(q='contoso')" in m.get.call_args[0][0]
    c.delta(); assert "/root/delta" in m.get.call_args[0][0]
    c.rename("ID", "n.docx"); assert m.patch.call_args[1]["json"] == {"name": "n.docx"}
    c.delete("ID"); assert m.delete.called
    c.create_link("ID", "view", "anonymous")
    body = m.post.call_args[1]["json"]; assert body["type"] == "view"
    print("paths OK")

def test_upload_sizes():
    try:
        from graph.official import GraphDriveClient as G
    except ImportError:
        from official import GraphDriveClient as G
    G.check_range_size(320 * 1024)  # ok
    G.check_range_size(10 * 1024 * 1024)  # 10Mio non multiple? 10*1024*1024 % 327680 = 0? 10485760/327680=32 -> ok
    for bad in (100, 60 * 1024 * 1024 + 1):
        try:
            G.check_range_size(bad); raise AssertionError(f"aurait dû rejeter {bad}")
        except ValueError:
            pass
    print("sizes OK")

def test_upload_large_resume_and_cancel():
    c = _c()
    m = c.s
    sess = MagicMock()
    sess.json.return_value = {"uploadUrl": "https://up.example/123", "expirationDateTime": "x"}
    m.post.return_value = sess
    put1 = MagicMock(status_code=202, content=b'{"nextExpectedRanges":["5-"]}')
    put1.json.return_value = {"nextExpectedRanges": ["5-"]}
    put2 = MagicMock(status_code=201, content=b'{"id":"F1"}')
    put2.json.return_value = {"id": "F1"}
    m.put.side_effect = [put1, put2]
    out = c.upload_large(b"0123456789", "f.bin", chunk_size=320 * 1024)
    assert out == {"id": "F1"}, out
    assert m.put.call_count == 2
    # erreur -> DELETE cancel
    m2 = MagicMock()
    c2 = _c(); c2.s = m2
    s2 = MagicMock(); s2.json.return_value = {"uploadUrl": "https://up.example/9"}
    m2.post.return_value = s2
    m2.put.side_effect = RuntimeError("coupé")
    m2.delete.return_value = MagicMock(status_code=204)
    try:
        c2.upload_large(b"0123456789", "f.bin", chunk_size=320 * 1024)
        raise AssertionError("aurait dû lever")
    except RuntimeError:
        pass
    assert m2.delete.called
    print("upload_large OK")

def test_nextlink_pagination_and_retry():
    import time
    c = _c()
    m = c.s
    r429 = MagicMock(status_code=429, content=b"busy", headers={"Retry-After": "0"})
    p1 = MagicMock(status_code=200, content=b"{}")
    p1.json.return_value = {"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next1"}
    p1.headers = {}
    p2 = MagicMock(status_code=200, content=b"{}")
    p2.json.return_value = {"value": [{"id": "b"}]}
    p2.headers = {}
    m.get.side_effect = [r429, p1, p2]
    orig = time.sleep
    time.sleep = lambda s: None
    try:
        out = c.search_all("q")
    finally:
        time.sleep = orig
    assert [x["id"] for x in out["value"]] == ["a", "b"], out
    print("pagination+retry OK")

if __name__ == "__main__":
    test_paths(); test_upload_sizes(); test_upload_large_resume_and_cancel(); test_nextlink_pagination_and_retry(); print("ALL GREEN")
