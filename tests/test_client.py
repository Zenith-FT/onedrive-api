"""Tests sans réseau: vérifient la construction des URLs/headers/body identiques au HAR."""
import urllib.parse
from unittest.mock import MagicMock
import sys, os
os.environ.setdefault("OD_TENANT", "votre-tenant-my.sharepoint.com")
os.environ.setdefault("OD_PERSONAL_PATH", "/personal/prenom_nom_domaine_fr")
os.environ.setdefault("OD_DRIVE_ID", "DRIVE_ID_TEST")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from client import OneDriveInternalClient

def _mock_client():
    c = OneDriveInternalClient(cookies="FedAuth=x; rtFa=y", digest="0xDIGEST", min_delay=0)
    m = MagicMock()
    c.s = m
    return c, m

def test_create_folder_url():
    c, m = _mock_client()
    resp = MagicMock(); resp.content = b""; resp.json.return_value = {}
    m.post.return_value = resp
    c.create_folder("/personal/prenom_nom_domaine_fr/Documents/Exemple")
    url = m.post.call_args[0][0]
    assert "/_api/web/folders/AddUsingPath(DecodedUrl=@a1,overwrite=@a2)" in url, url
    assert "@a2=false" in url
    kwargs = m.post.call_args[1]
    assert kwargs["headers"]["X-RequestDigest"] == "0xDIGEST"
    assert kwargs["json"] == {}

def test_list_documents_body():
    c, m = _mock_client()
    resp = MagicMock(); resp.json.return_value = {"ListData": {}}
    m.post.return_value = resp
    c.list_documents("/Documents")
    url = m.post.call_args[0][0]
    assert "RenderListDataAsStream" in url and "TryNewExperienceSingle=TRUE" in url, url
    body = m.post.call_args[1]["json"]
    assert body["parameters"]["__metadata"]["type"] == "SP.RenderListDataParameters"
    assert body["parameters"]["RenderOptions"] == 1496839

def test_upload_small_headers():
    c, m = _mock_client()
    resp = MagicMock(); resp.content = b""; resp.json.return_value = {}
    m.post.return_value = resp
    c.upload_small("/personal/prenom_nom_domaine_fr/Documents", "f.pdf", b"%PDF")
    url = m.post.call_args[0][0]
    assert "Files/AddUsingPath" in url, url
    h = m.post.call_args[1]["headers"]
    assert h["Content-Type"] == "application/octet-stream"

def test_upload_large_url():
    c, m = _mock_client()
    resp = MagicMock(); resp.content = b""; resp.json.return_value = {}
    m.post.return_value = resp
    out = c.upload_large_single_shot("/personal/x/Documents", "v.mp4", b"xxx", upload_id="11111111-2222-3333-4444-555555555555")
    url = m.post.call_args[0][0]
    assert "AddStubUsingPath" in url and "/Upload(uploadId=@a3)" in url, url
    assert "guid'11111111-2222-3333-4444-555555555555'" in url
    assert out["_uploadId"].startswith("1111")

def test_cancel_url():
    c, m = _mock_client()
    m.post.return_value = MagicMock()
    c.cancel_upload("/personal/x/f.mp4", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    url = m.post.call_args[0][0]
    assert "CancelUpload" in url, url

def test_delta_url():
    c, m = _mock_client()
    m.get.return_value = MagicMock(content=b"", json=lambda: {})
    # delta avec content vide -> {}
    r = c.delta("mytoken123")
    url = m.get.call_args[0][0]
    assert "/_api/v2.0/drives/" in url and "/root/view.delta" in url, url
    r = c.delta()
    url = m.get.call_args[0][0]
    assert "token=" not in url, url

def test_delta_all_pages():
    c, m = _mock_client()
    p1 = MagicMock()
    p1.json.return_value = {"value": [{"name": "a"}], "@odata.nextLink": "https://x/delta?p=2"}
    p2 = MagicMock()
    p2.json.return_value = {"value": [{"name": "b"}], "@odata.deltaLink": "https://x/delta?token=L"}
    m.get.side_effect = [p1, p2]
    out = c.delta_all()
    assert [v["name"] for v in out["value"]] == ["a", "b"]
    assert out["@odata.deltaLink"].endswith("token=L")
    assert out["pages"] == 2

def test_retry_429_then_ok():
    import time
    c, m = _mock_client()
    r429 = MagicMock(status_code=429, content=b"busy", headers={"Retry-After": "0"})
    r429.json.return_value = {}
    r200 = MagicMock(status_code=200, content=b'{"ListData": {}}')
    r200.json.return_value = {"ListData": {}}
    r200.headers = {}
    m.post.side_effect = [r429, r200]
    orig_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        out = c.list_documents("/Documents")
    finally:
        time.sleep = orig_sleep
    assert out == {"ListData": {}}
    assert m.post.call_count == 2

def test_list_pagination_all_pages():
    c, m = _mock_client()
    p1 = MagicMock()
    p1.json.return_value = {"ListData": {"Row": [{"FileLeafRef": "a"}], "NextHref": "Paged=TRUE&p_ID=1"}}
    p2 = MagicMock()
    p2.json.return_value = {"ListData": {"Row": [{"FileLeafRef": "b"}]}}
    m.post.side_effect = [p1, p2]
    out = c.list_documents("/Documents", all_pages=True)
    assert [r["FileLeafRef"] for r in out["ListData"]["Row"]] == ["a", "b"]
    assert out["pages"] == 2
    assert m.post.call_count == 2

def test_chunked_offsets_order():
    c, m = _mock_client()
    calls = []
    c.upload_large_single_shot = lambda folder, fn, data, upload_id=None: calls.append(("Upload", len(data))) or {"_uploadId": upload_id}
    c.finish_upload_with_checksum = lambda fp, uid, off, chk, data: calls.append(("Finish", off, len(data), chk)) or {}
    c.cancel_upload = MagicMock()
    data = b"x" * (30 * 1024 * 1024)  # 30Mo -> 24 + 6 avec first slice 24Mo
    out = c.upload_large_chunked("/personal/x/Documents", "g.bin", data, first_chunk_mb=24, checksum_b64="Q2hlY2tzdW0=", upload_id="u1")
    assert calls[0] == ("Upload", 24 * 1024 * 1024), calls
    assert calls[1] == ("Finish", 24 * 1024 * 1024, 6 * 1024 * 1024, "Q2hlY2tzdW0="), calls
    assert out["_uploadId"] == "u1" and out["Length"] == len(data)
    assert not c.cancel_upload.called

def test_chunked_cancel_on_error():
    c, m = _mock_client()
    c.upload_large_single_shot = MagicMock(return_value={})
    c.finish_upload_with_checksum = MagicMock(side_effect=RuntimeError("réseau coupé"))
    c.cancel_upload = MagicMock(return_value=True)
    try:
        c.upload_large_chunked("/personal/x/Documents", "g.bin", b"y" * (30 * 1024 * 1024),
                               first_chunk_mb=24, checksum_b64="eA==", upload_id="u2")
        raise AssertionError("aurait dû lever")
    except RuntimeError:
        pass
    assert c.cancel_upload.called
    args = c.cancel_upload.call_args[0]
    assert args[1] == "u2"

def test_chunked_requires_checksum():
    c, m = _mock_client()
    c.upload_large_single_shot = MagicMock(return_value={})
    c.cancel_upload = MagicMock(return_value=True)
    try:
        c.upload_large_chunked("/personal/x/Documents", "g.bin", b"z" * (30 * 1024 * 1024), first_chunk_mb=24, upload_id="u3")
        raise AssertionError("aurait dû lever (checksum manquant)")
    except ValueError as ex:
        assert "checksum_b64" in str(ex)

def test_parse_expected_range():
    c, m = _mock_client()
    off, total = c.parse_expected_range({"d": {"ExpectedContentRange": "25165824-104857599"}})
    assert (off, total) == (25165824, 104857600)
    assert c.parse_expected_range({}) == (None, None)

def test_list_folder_url():
    c, m = _mock_client()
    resp = MagicMock()
    resp.json.return_value = {"d": {"results": [{"Name": "a.mp4", "Length": 10}] }}
    m.get.return_value = resp
    out = c.list_folder("/personal/x/Documents/Jellyfin")
    url = m.get.call_args[0][0]
    assert "GetFolderByServerRelativePath" in url and "/Files" in url, url
    assert out == [{"Name": "a.mp4", "Length": 10}]

if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); print("OK", k)
    print("ALL GREEN")
