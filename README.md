# OneDrive Personal API (endpoints internes, via HAR Reqable)

Basé sur un HAR Reqable du client web OneDrive (`votre-tenant-my.sharepoint.com_AAAA_MM_JJ_HH_MM_SS.har`,
402 entries, 141 endpoints uniques dans la capture de référence).

> Ce projet ne contient **aucune valeur personnelle** : tenant, chemin `/personal/...`,
> drive ID et noms de fichiers sont des placeholders à remplir via `.env`
> (voir `.env.example`). Ne committe jamais ton HAR ni tes cookies.

## Ce qui a été observé (trafic réel OneDrive web)

| # | Méthode | Endpoint interne | Usage |
|---|---------|------------------|-------|
| 194x | POST | `/personal/.../_api/web/folders/AddUsingPath(DecodedUrl=@a1,overwrite=@a2)` body `{}` | création arborescence `Documents/...` |
| 5x | POST | `/personal/.../_api/web/GetListUsingPath(DecodedUrl=@a1)/RenderListDataAsStream` body `SP.RenderListDataParameters` | lister `Documents` |
| 1x | POST | `/personal/.../_api/web/GetFolderByServerRelativePath(...)/Files/AddUsingPath(...,XorHash=@a4)` body binaire | upload petit fichier (ex: doc 187 Ko) |
| 3x | POST | `/personal/.../_api/web/GetFolderByServerRelativePath(...)/Files/AddStubUsingPath(...)/Upload(uploadId=@a3)` body binaire | upload gros fichier (ex: vidéo ~50 Mo, 1 appel) |
| 3x | POST | `/personal/.../_api/web/GetFileByServerRelativePath(...)/CancelUpload(uploadId=@a2)` | annuler upload |
| 2x | GET | `/_api/v2.0/drives/<DRIVE_ID>/root/view.delta?token=...` | sync delta |
| 4x | GET | `/personal/.../_api/v2.0/sites/.../lists/<LIST_ID>/subscriptions/socketIo` | notifs temps réel |
| 1x | GET | `/personal/.../_api/sp.FavoriteItemCollection.GetFavoriteItems` | favoris |
| 1x | GET | `/personal/.../_api/sp.QuickAccessItemCollection.GetQuickAccessItems` | accès rapide |
| 1x | GET | `/personal/.../_api/web/lists/GetByTitle('microsoft.ListSync.Endpoints')/items` | endpoints sync |

Auth observée : cookies `FedAuth` + `rtFa` (+ `SPA_RT`, `odbn`...), header `X-RequestDigest` réutilisé (~30 min),
`X-ClientService-ClientTag: ODB Web`, `Accept/Content-Type: application/json;odata=verbose`
(ou `application/octet-stream` pour upload).

## Sécurité — IMPORTANT

Le HAR contient **tes secrets** (`FedAuth`, `rtFa`, `Bearer`, `X-RequestDigest`, tokens `view.delta`).
- Ne le partage jamais, ne le commit jamais (voir `.gitignore` : `*.har`, `.env`, `cookies*.txt`).
- Les tokens expirent vite (digest ~30 min, FedAuth quelques heures/jours selon politique).
- Ce projet **n'embarque aucun secret** : tout passe par env `OD_COOKIES` / `OD_DIGEST`.
- `har_parse.py` ne print que méthode+path+clés de query, jamais les valeurs.

## Install

```bash
pip install -r requirements.txt
# serveur local optionnel :
pip install -r requirements-server.txt
cp .env.example .env  # puis remplit OD_TENANT, OD_PERSONAL_PATH, OD_DRIVE_ID, OD_COOKIES, OD_SERVER_KEY
```

Récupérer `OD_COOKIES` : ouvre OneDrive dans le navigateur > F12 > Application > Cookies > `votre-tenant-my.sharepoint.com` > copie `FedAuth` et `rtFa` :
```
OD_COOKIES="FedAuth=...; rtFa=...;"
```
`OD_DIGEST` optionnel : si vide, le client fait `POST /_api/contextinfo` automatiquement.

## Usage

```bash
# 1. re-lister les endpoints depuis ton HAR (sans secrets)
python har_parse.py /chemin/vers/votre-tenant-my.sharepoint.com_AAAA_MM_JJ_HH_MM_SS.har --top 20

# 2. tests (mock, sans réseau)
python -m tests.test_client
python -m tests.test_official
python -m tests.test_server
# ou
python tests/test_client.py

# 3. CLI directe
OD_COOKIES="FedAuth=...; rtFa=..." python cli.py list "/Documents"
OD_COOKIES="..." python cli.py mkdir "/personal/prenom_nom_domaine_fr/Documents/Test"
OD_COOKIES="..." python cli.py health

# 4. serveur perso FastAPI (localhost + clé API)
OD_COOKIES="..." OD_SERVER_KEY="..." uvicorn server:app --host 127.0.0.1 --port 8000
# GET  http://127.0.0.1:8000/health
# GET  http://127.0.0.1:8000/api/list?path=/Documents (header X-API-Key)
```

Fichiers : `config.py:1` `client.py:1` `server.py:1` `har_parse.py:1` `cli.py:1`.
Uploads : `upload_small` (1 appel), `upload_large_single_shot` (stub, observé :
~48 Mo, 100 Mo, tentative 200 Mo en 1 appel),
`upload_large_chunked` (protocole web observé : stub/Upload 24 Mio +
`FinishUploadWithChecksum`, checksum explicite requis) et
`upload_auto` (bascule au seuil `OD_LARGE_THRESHOLD_MB`, défaut 100).
Alternative optionnelle (non requise) : `graph/` (API officielle Microsoft Graph).

## Vérifié avec des HAR de 100 et 200 Mo

Capture n°2 (18 entries, upload `100MB.bin` = 104 857 600 o) + capture n°3
(37 entries, tentative `200mb.svg` = 209 765 320 o) :

**Protocole web réel pour les gros fichiers :**
1. `POST .../Files/AddStubUsingPath(DecodedUrl=@a2)/Upload(uploadId=@a3)`
   avec le 1er slice (24 Mio = 25 165 824 o observés pour le fichier 100 Mio) ;
2. (reprise) `GET .../GetFileByServerRelativePath(...)/GetUploadStatus(uploadId=@a2)`
   → 200 `{"d": {"ExpectedContentRange": "25165824-104857599",
   "ExpirationDateTime": ..., "UploadId": ...}}` ;
3. `POST .../GetFileByServerRelativePath(...)/FinishUploadWithChecksum(`
   `uploadId=@a2,fileOffset=@a3,checksum=@a4)` avec le reste
   (offset + corps = taille totale exacte : 25 165 824 + 79 691 776 = 104 857 600),
   headers `Content-Type: application/octet-stream` + `X-RequestDigest` +
   `Prefer: RetrieveArchiveFileErrorCode`, `CancelUpload` → 200 `{"d":{"CancelUpload":null}}`
   en cas d'abandon.
4. Le fichier 200 Mio a été tenté en **1 seul appel** `AddStubUsingPath/Upload`
   (×3 essais, puis `CancelUpload`) : **aucun `StartUpload` / `ContinueUpload` /
   `FinishUpload` n'a jamais été observé** dans les 3 HAR (cette implémentation
   a donc été supprimée du code).

**Checksum inconnu** : `@a4` = base64 de 20 octets, mais ce n'est ni le SHA-1
ni le MD5 du contenu (vérifié sur un PDF local de 186 998 o). `checksum_b64`
est donc un paramètre explicite obligatoire — ne jamais inventer de valeur.
Expérience restante : uploader un fichier connu et comparer les candidats
(SHA-1 tronqué, CRC64, XorHash propriétaire...).

## Limites honnêtes

- API **interne non documentée** : Microsoft peut changer les paths à tout moment.
  `cli.py health` détecte vite un endpoint qui a changé ou une session expirée.
- Session courte : `FedAuth/rtFa` à recopier quand expirés (message clair du client).
- Upload gros fichier chunké non validé contre le client web réel (voir section ci-dessus).
- Pour du durable, préfère l'API officielle Microsoft Graph (`graph/`, optionnel).
- `view.delta` nécessite un `token` frais (récupéré via sync précédent ou page web).
