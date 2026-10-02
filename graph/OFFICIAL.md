# API officielle Graph (OneDrive) — recodage propre

Implémentation : `graph/official.py:1` (`GraphDriveClient`, base `https://graph.microsoft.com/v1.0`).
Auth : `Authorization: Bearer {token}` via `OD_GRAPH_TOKEN`. Doc source : <https://learn.microsoft.com/en-us/graph/api/resources/onedrive>.

> Module optionnel (plan B durable). Le cœur du projet reste l'API interne (`client.py`).

## 12 fonctions essentielles

| # | Fonction (`official.py`) | HTTP officiel | Rôle / notes doc |
|---|--------------------------|---------------|------------------|
| 1 | `get_root()` / `get_item(id)` / `get_path(path)` | `GET /me/drive/root`, `/items/{id}`, `/root:/{path}` | Métadonnées DriveItem. `$expand=children`, `$select`. 200 OK |
| 2 | `list_children(id)` / `list_children_path(path)` | `GET .../root/children`, `.../items/{id}/children`, `.../root:/{p}:/children` | Enfants d'un dossier (folder/package facet). `200 {value, @odata.nextLink}`, page ~200, `$top/$orderby` |
| 3 | `create_folder(name, parent)` | `POST .../items/{parent}/children` `{"name","folder":{}}` | 201 + DriveItem. `@microsoft.graph.conflictBehavior: fail\|replace\|rename` |
| 4 | `upload_small(data, file)` / `update_content(id, data)` | `PUT .../items/{parent}:/{file}:/content`, `PUT .../items/{id}/content` | 1 appel, **≤250 Mo**. 200/201 + DriveItem |
| 5 | `create_upload_session()` + `upload_chunk()` | `POST .../createUploadSession` → `PUT uploadUrl` `Content-Range` | Gros fichiers. Fragments **multiples de 320 Kio, <60 Mio**, séquentiels. 202 `{nextExpectedRanges}` puis 200/201. `DELETE uploadUrl` = cancel. Pas de `Authorization` sur l'uploadUrl |
| 6 | `download(id)` / `download_url(id)` | `GET .../items/{id}/content` → **302** vers `downloadUrl` pré-authentifiée | Binaire. `Range: bytes=` sur la downloadUrl finale (206). `?select=@microsoft.graph.downloadUrl` pour JS/CORS |
| 7 | `delete(id)` | `DELETE .../items/{id}` | Corbeille, **204 No Content**. `If-Match` (etag) opt |
| 8 | `rename(id, name)` / `move(id, parent)` | `PATCH .../items/{id}` `{"name"}`, `{"parentReference":{"id"}}` | 200 + DriveItem |
| 9 | `copy(id, drive, folder)` | `POST .../items/{id}/copy` `{"parentReference","name"}` | **Async 202 + header `Location` (monitor)** jusqu'à `completed/failed`. `?@microsoft.graph.conflictBehavior=` |
| 10 | `search(q)` | `GET .../root/search(q='...')` | Nom/métadonnées/contenu. 200 `{value, @odata.nextLink}` |
| 11 | `delta(token)` | `GET .../root/delta(?token=)` | Sync : suivre `@odata.nextLink` puis garder `@odata.deltaLink`. `token=latest` = point de reprise vide. `deleted` facet = supprimer en local |
| 12 | `create_link(id, type, scope)` | `POST .../items/{id}/createLink` `{"type","scope"}` | 200/201 + Permission `{link:{webUrl}}`. type `view\|edit\|embed`, scope `anonymous\|organization\|users` |

## Mapping avec notre API interne (HAR)

| Officiel (Graph, durable) | Interne (`client.py`, session 5j) |
|---|---|
| `list_children_path` | `list_documents()` (`RenderListDataAsStream`) — racine seule, pas de vrai paging |
| `create_folder` | `create_folder()` (`folders/AddUsingPath`) — observé en masse dans le HAR |
| `upload_small` | `upload_small()` (`Files/AddUsingPath`) |
| `upload_large` session | `upload_large_single_shot()` (`AddStubUsingPath/Upload`) — 1 appel vu, pas de ranges |
| `download` | `download_file()` (`GetFileByServerRelativePath/$value`) |
| `delta` | `delta()` (`view.delta?token=`) |
| `delete/move/copy/search/createLink` | non observés dans le HAR → à faire en Graph |

## Usage

```bash
export OD_GRAPH_TOKEN="..."   # OAuth2, scopes Files.ReadWrite
python -c "from graph.official import GraphDriveClient; c=GraphDriveClient.from_env(); print(c.get_root()['name'])"
python tests/test_official.py  # mocks, sans réseau
```

Permissions minimales : `Files.Read` (lecture), `Files.ReadWrite` (écriture). Perso MS : mêmes noms. App-only : `Files.Read(All)` / `Files.ReadWrite.All`.
