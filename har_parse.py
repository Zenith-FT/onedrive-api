"""Parse le HAR en local et liste les endpoints internes, SANS afficher de secrets.

Usage:
  python har_parse.py /chemin/vers/capture.har [--top 50] [--focus sharepoint.com]

Sortie: méthode + host + path + CLÉS de query uniquement (valeurs jamais affichées).
Ne jamais imprimer: valeurs de query, cookies, headers Authorization/digest/tokens.
"""
import argparse
import collections
import json
import urllib.parse

# Clés sensibles: leurs VALEURS ne doivent jamais être affichées.
SECRET_KEYS = ("token", "@a1", "@a2", "@a3", "@a4", "code", "session", "auth")


def is_secret_key(name: str) -> bool:
    """True si la clé est sensible (comparaison insensible à la casse)."""
    n = (name or "").lower()
    return any(s.lower() == n or s.lower() in n for s in SECRET_KEYS)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Liste les endpoints d'un HAR sans exposer de secrets.")
    p.add_argument("har", help="Chemin du fichier .har")
    p.add_argument("--top", type=int, default=50, help="Nombre d'endpoints à afficher (défaut 50)")
    p.add_argument("--focus", default="sharepoint.com", help="Filtre host pour la 2e section (défaut sharepoint.com)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    with open(args.har) as fh:
        j = json.load(fh)
    entries = j["log"]["entries"]
    print(f"entries: {len(entries)}")
    uniq: collections.Counter = collections.Counter()
    for e in entries:
        m = e["request"]["method"]
        u = urllib.parse.urlparse(e["request"]["url"])
        # On ne garde QUE les noms de clés; les valeurs sont jetées immédiatement.
        # is_secret_key() documente quelles clés sont sensibles (aucune valeur printée).
        keys = tuple(sorted({k for k, _v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)}))
        assert all(isinstance(k, str) for k in keys)
        uniq[(m, u.netloc, u.path, keys)] += 1
    print(f"unique endpoints: {len(uniq)}\n")
    for (m, host, p, keys), c in uniq.most_common(args.top):
        print(f"{c:4d}x {m:6s} https://{host}{p}  ?{','.join(keys)[:160]}")
    print(f"\n-- focus {args.focus} _api --")
    for (m, host, p, keys), c in uniq.most_common(args.top * 3):
        if args.focus in host and "_api" in p:
            print(f"{c:4d}x {m:6s} {p}  ?{','.join(keys)[:160]}")


if __name__ == "__main__":
    main()
