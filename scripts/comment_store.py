"""État partagé et lecture paginée des commentaires Facebook."""
import json
import os
import tempfile
import urllib.parse

# Les scripts sont dans le dépôt public ; les données (commentaires, état, rapports, prompts)
# vivent dans un dépôt PRIVÉ cloné dans data/ (ou dans le dossier donné par DATA_DIR).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR") or os.path.join(REPO_ROOT, "data"))


def data_path(*parts):
    return os.path.join(DATA_DIR, *parts)


def apercu(texte, limite=60):
    """Les journaux GitHub Actions d'un dépôt public sont lisibles par tous : jamais de contenu
    de commentaire en clair, sauf si LOG_CONTENT=1 (tests locaux)."""
    texte = str(texte or "")
    if os.environ.get("LOG_CONTENT") == "1":
        return texte[:limite]
    return f"[{len(texte)} car. masqués]"


LEDGER_PATH = data_path("inbox", "commentaires-traites.json")


def load_ledger(path=LEDGER_PATH, legacy_paths=()):
    entries = []
    seen = set()
    for source in (path, *legacy_paths):
        try:
            with open(source, encoding="utf-8") as stream:
                data = json.load(stream)
            if isinstance(data, dict):
                migrated = []
                for item in data.get("reponses", []):
                    migrated.append({**item, "status": "replied"})
                for item in data.get("a_traiter", []):
                    migrated.append({**item, "status": "human"})
                data = migrated
            if not isinstance(data, list):
                continue
        except (OSError, json.JSONDecodeError):
            continue
        for entry in data:
            if not isinstance(entry, dict) or not entry.get("comment_id"):
                continue
            cid = str(entry["comment_id"])
            if cid not in seen:
                entries.append(entry)
                seen.add(cid)
    return entries


def save_ledger(entries, path=LEDGER_PATH, keep=5000):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".comment-ledger-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entries[-keep:], stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def record(entries, comment_id, status, **details):
    cid = str(comment_id)
    for entry in entries:
        if str(entry.get("comment_id")) == cid:
            entry.update(status=status, **details)
            return
    entries.append({"comment_id": cid, "status": status, **details})


def is_handled(entries, comment_id, source=None):
    cid = str(comment_id)
    for entry in entries:
        if str(entry.get("comment_id")) != cid:
            continue
        status = entry.get("status")
        if status in {"replied", "repondu", "human"}:
            return True
        if status in {"ignore", "ignored"} and source and entry.get("source") == source:
            return True
    return False


def fetch_all(first_url, get_json):
    """Suit tous les liens paging.next. Ne jamais journaliser les URL (elles contiennent le jeton)."""
    result = []
    url = first_url
    visited = set()
    while url:
        if url in visited:
            raise RuntimeError("Pagination Facebook cyclique détectée")
        visited.add(url)
        code, payload = get_json(url)
        if code != 200 or not isinstance(payload, dict) or payload.get("error"):
            error = (payload or {}).get("error") or {}
            if not isinstance(error, dict):
                error = {"message": str(error)}
            detail = error.get("message") or (payload or {}).get("erreur") or "réponse invalide"
            kind = error.get("type")
            subcode = error.get("error_subcode")
            # Ne jamais inclure l'URL : elle contient le jeton d'accès.
            raise RuntimeError(
                f"Lecture Facebook impossible à la page {len(visited)} "
                f"(HTTP {code}, code {error.get('code')}, sous-code {subcode}, type {kind}) : {str(detail)[:300]}"
            )
        result.extend(payload.get("data") or [])
        url = (payload.get("paging") or {}).get("next")
    return result


def fetch_page_posts(page_id, token, graph, get_json):
    """Charge les publications puis tous les niveaux de commentaires/réponses."""
    posts_query = urllib.parse.urlencode({
        "fields": "id,message,created_time",
        "limit": 100,
        "access_token": token,
    })
    posts = fetch_all(f"{graph}/{page_id}/posts?{posts_query}", get_json)

    for post in posts:
        comment_query = urllib.parse.urlencode({
            "fields": "id,message,created_time,from{id,name},comment_count",
            "limit": 100,
            "access_token": token,
        })
        roots = fetch_all(f"{graph}/{post['id']}/comments?{comment_query}", get_json)
        flattened = []
        queue = [(item, None, None) for item in roots]
        visited = set()

        while queue:
            comment, parent_id, root_id = queue.pop(0)
            cid = str(comment.get("id") or "")
            if not cid or cid in visited:
                continue
            visited.add(cid)
            comment["parent_id"] = parent_id
            # Facebook n'a que deux niveaux (commentaire > réponse). Les réponses doivent
            # toujours être publiées sur le commentaire racine, sinon Facebook Lite les masque.
            root_id = root_id or cid
            comment["root_id"] = root_id
            comment["post_id"] = post["id"]
            comment["page_replied"] = False
            flattened.append(comment)

            # Ne pas se fier à comment_count : selon les réponses de l'API Graph,
            # ce compteur peut être absent ou incohérent alors que des réponses existent.
            # Interroger chaque edge /comments permet de découvrir toute la profondeur.
            child_query = urllib.parse.urlencode({
                "fields": "id,message,created_time,from{id,name},comment_count",
                "limit": 100,
                "access_token": token,
            })
            children = fetch_all(f"{graph}/{cid}/comments?{child_query}", get_json)
            comment["page_replied"] = any(
                str((child.get("from") or {}).get("id") or "") == str(page_id)
                for child in children
            )
            queue.extend((child, cid, root_id) for child in children)

        post["comments"] = {"data": flattened, "summary": {"total_count": len(flattened)}}
    return posts


def reply_target(comment):
    """Identifiant sur lequel publier la réponse : toujours le commentaire de premier niveau."""
    return str(comment.get("root_id") or comment.get("id") or "")


def is_nested(comment):
    """Vrai si le commentaire est lui-même une réponse (niveau 2 ou plus)."""
    root = comment.get("root_id")
    return bool(root) and str(root) != str(comment.get("id"))


def with_name(message, first_name, max_len=300):
    """Préfixe le prénom quand la réponse est publiée sous le commentaire racine."""
    name = (first_name or "").strip()
    if not name or message.lower().startswith(name.lower()):
        return message
    body = message[0].lower() + message[1:] if len(message) > 1 and message[1].islower() else message
    candidate = f"{name}, {body}"
    return candidate if len(candidate) <= max_len else message
