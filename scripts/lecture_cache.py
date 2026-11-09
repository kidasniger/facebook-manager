"""Lecture Facebook avec cache.

Avant : à chaque cycle (60 s) la boucle relisait TOUTES les publications, tous leurs
commentaires et, pour chaque commentaire, un appel de plus pour ses réponses. Sur une Page
qui grandit, cela dépasse vite le quota d'appels de Facebook (4800 x personnes engagées / 24 h).

Maintenant : un seul appel (par tranche de 100 publications) donne la liste des posts avec
leur nombre total de commentaires. Seules les publications dont ce nombre a changé sont
relues en profondeur ; les autres reprennent l'arbre gardé en mémoire. Un balayage complet
a lieu toutes les BALAYAGE_MINUTES minutes comme filet de sécurité.

La relecture en profondeur elle-même (comment_store.fetch_post_comments) n'a pas changé.
"""
import copy
import datetime as dt
import time
import urllib.parse

try:
    from scripts import comment_store
except ImportError:  # exécution directe : python3 scripts/glm_boucle.py
    import comment_store

CHAMPS_POSTS = "id,message,story,created_time,permalink_url"
RESUME = "comments.filter(stream).summary(true).limit(0)"


def _total(post):
    resume = (post.get("comments") or {}).get("summary") or {}
    total = resume.get("total_count")
    return total if isinstance(total, int) else None


def _est_recent(post, maintenant, jours):
    brut = str(post.get("created_time") or "")
    try:
        quand = dt.datetime.strptime(brut, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return True  # date illisible : par prudence, on traite comme récent
    return (maintenant - quand.timestamp()) < jours * 86400


class LecteurCache:
    def __init__(self, page_id, token, graph, get_json, balayage_minutes=20,
                 jours_surs=7, horloge=time.time, journal=print):
        self.page_id, self.token, self.graph, self.get_json = page_id, token, graph, get_json
        self.balayage_s = max(1, balayage_minutes) * 60
        self.jours_surs = jours_surs
        self.horloge = horloge
        self.journal = journal
        self.cache = {}            # id du post -> {meta, compte, arbre, verifie}
        self.dernier_balayage = 0.0
        self.resume_ok = True      # passe à False si Facebook refuse le champ de comptage
        self.stats = {"relus": 0, "reutilises": 0, "balayage": False}

    def _lister(self, max_pages):
        champs = CHAMPS_POSTS + ("," + RESUME if self.resume_ok else "")
        url = f"{self.graph}/{self.page_id}/posts?" + urllib.parse.urlencode(
            {"fields": champs, "limit": 100, "access_token": self.token})
        try:
            return comment_store.fetch_all(url, self.get_json, max_pages=max_pages)
        except comment_store.FacebookError as exc:
            if self.resume_ok and exc.code == 100:
                # Champ de comptage refusé : retour à la lecture complète à chaque cycle.
                self.resume_ok = False
                self.journal("Cache : Facebook refuse le comptage des commentaires, "
                             "retour à la lecture complète (le quota reste surveillé).")
                return self._lister(max_pages)
            raise

    def lire(self, forcer=False):
        """Renvoie les publications au même format que comment_store.fetch_page_posts."""
        maintenant = self.horloge()
        balayage = forcer or not self.cache or (maintenant - self.dernier_balayage) >= self.balayage_s
        self.stats = {"relus": 0, "reutilises": 0, "balayage": balayage}
        posts = self._lister(None if balayage else 1)
        vus = set()
        for post in posts:
            pid = str(post["id"])
            vus.add(pid)
            total = _total(post)
            entree = self.cache.get(pid)
            relire = (
                balayage or entree is None or total is None or not self.resume_ok
                or total != entree["compte"]
                or (not entree["verifie"] and _est_recent(post, maintenant, self.jours_surs))
            )
            meta = {k: v for k, v in post.items() if k != "comments"}
            if relire:
                comment_store.fetch_post_comments(post, self.page_id, self.token, self.graph, self.get_json)
                plat = len((post.get("comments") or {}).get("data") or [])
                # « vérifié » : le comptage de Facebook égale ce qu'on a réellement trouvé,
                # donc on peut s'y fier pour détecter les nouveaux commentaires ET réponses.
                self.cache[pid] = {"meta": meta, "compte": total,
                                   "arbre": copy.deepcopy(post["comments"]),
                                   "verifie": total is not None and total == plat}
                self.stats["relus"] += 1
            else:
                entree["meta"] = meta
                post["comments"] = copy.deepcopy(entree["arbre"])
                self.stats["reutilises"] += 1
        if balayage:
            self.dernier_balayage = maintenant
            for pid in [p for p in self.cache if p not in vus]:
                del self.cache[pid]
        else:
            # Cycle partiel : les publications plus anciennes ne sont pas relistées, mais on
            # les redonne depuis le cache pour que les réponses en échec soient bien retentées.
            for pid, entree in self.cache.items():
                if pid not in vus:
                    ancien = dict(entree["meta"])
                    ancien["comments"] = copy.deepcopy(entree["arbre"])
                    posts.append(ancien)
        return posts


def lire_page(get_json, graph, page_id, token):
    """Nom et nombre d'abonnés de la Page (affichés par l'app). None si la lecture échoue."""
    url = f"{graph}/{page_id}?" + urllib.parse.urlencode(
        {"fields": "id,name,fan_count,followers_count", "access_token": token})
    code, payload = get_json(url)
    if code != 200 or not isinstance(payload, dict) or payload.get("error"):
        return None
    return payload


def _court(c):
    return {"id": c.get("id"), "message": c.get("message", ""),
            "created_time": c.get("created_time"), "from": c.get("from")}


def construire_cache_commentaires(posts, page, page_id, quand, nb_posts=15):
    """Même structure que l'ancien workflow « Surveiller la Page » (lue par l'app)."""
    recents = sorted(posts, key=lambda p: p.get("created_time") or "", reverse=True)[:nb_posts]
    sortie = []
    for p in recents:
        data = (p.get("comments") or {}).get("data") or []
        enfants = {}
        for c in data:
            if c.get("parent_id"):
                enfants.setdefault(str(c.get("root_id") or c["parent_id"]), []).append(c)
        racines = []
        for c in data:
            if c.get("parent_id"):
                continue
            sous = enfants.get(str(c.get("id")), [])
            entree = _court(c)
            entree["comment_count"] = len(sous)
            entree["comments"] = {"data": [_court(s) for s in sous]}
            entree["page_a_repondu"] = any(
                str((s.get("from") or {}).get("id") or "") == str(page_id) for s in sous)
            racines.append(entree)
        sortie.append({"id": p.get("id"), "message": p.get("message"), "story": p.get("story"),
                       "created_time": p.get("created_time"), "permalink_url": p.get("permalink_url"),
                       "comments": {"data": racines}})
    return {"updated": quand, "page": page or {}, "posts": sortie}


def sans_horodatage(donnees):
    """Pour comparer deux versions du cache sans tenir compte de l'heure de mise à jour."""
    if not isinstance(donnees, dict):
        return donnees
    return {k: v for k, v in donnees.items() if k != "updated"}
