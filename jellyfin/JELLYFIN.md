# Jellyfin <-> API perso OneDrive (via .strm)

Jellyfin (serveur `nasterpiece`, user `root`) ne lit que des fichiers locaux.
Le pont : des `.strm` (1 ligne = URL http) vers `GET /api/file` du serveur perso,
qui stream depuis OneDrive avec support `Range` (seek).

## Chaîne

```
OneDrive --(cookies FedAuth)--> API perso :8000/api/file --(http .strm)--> Jellyfin :8096
```

- `/api/file?path=<chemin-serveur>&api_key=<OD_SERVER_KEY>` : 200 full ou 206
  partial si header `Range`. Auth = `X-API-Key` **ou** `?api_key=` (les .strm ne
  portent pas de headers). Serveur lié à 127.0.0.1 uniquement.
- `.strm` écrits dans `<rootfs-ubuntu>/mnt/onedrive-strm/` (= `/mnt/onedrive-strm`
  vu par Jellyfin), directement depuis Termux, sans entrer dans le proot :
  `.../containers/ubuntu/mnt/onedrive-strm/`.
- Dans Jellyfin : Tableau de bord > Bibliothèques > Ajouter `/mnt/onedrive-strm`
  (type Films/Séries) > Scanner.

## Générer la bibliothèque

```bash
export OD_COOKIES="FedAuth=...; rtFa=..." OD_SERVER_KEY="..."
python jellyfin/make_strm.py "/Documents" /chemin/rootfs-ubuntu/mnt/onedrive-strm
```

Ne régénère que les vidéos (`.mp4 .mkv .avi .mov .webm ...`). Relance après
chaque ajout côté OneDrive (ou via `view.delta`).

## Limites honnêtes

- Le flux dépend de la session OneDrive (`FedAuth/rtFa`, ~jours) : si expirée,
  les lectures Jellyfin échouent jusqu'au renouvellement (`cli.py health`).
- Débit mesuré via l'API interne ~8 Mb/s : OK 720p/1080p léger, pas 4K.
- `?api_key=` en clair dans les .strm : acceptable en localhost uniquement.
