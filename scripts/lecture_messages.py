"""Lecture Messenger de la Page, avec cache, pour l'écran Messages de l'app.

Principe (même esprit que lecture_cache.py) : un seul appel liste les conversations récentes
avec leur date de dernière activité (updated_time). Seules les conversations dont cette date a
changé sont relues (un appel chacune) ; les autres reprennent la version gardée en mémoire.
Hors activité, un cycle coûte donc un seul appel Facebook.

Rien ici ne doit jamais gêner la veille des commentaires : les erreurs sont remontées à
l'appelant (glm_boucle.MessagesApp) qui décide de la pause.

Confidentialité : les journaux GitHub Actions d'un dépôt public sont lisibles par tous.
Ce module n'affiche jamais un nom ni un texte de message.
"""
import copy
import urllib.parse

try:
    from scripts import comment_store
except ImportError:  # exécution directe : python3 scripts/glm_boucle.py
    import comment_store

CHAMPS_CONVERSATIONS = "id,updated_time,unread_count,message_count,participants"
CHAMPS_MINIMUM = "id,updated_time,participants"
CHAMPS_MESSAGES = "id,message,created_time,from"
MAX_CONVERSATIONS = 25
MAX_MESSAGES = 15
MAX_LECTURES_PAR_PASSE = 10   # au-delà, le reste est lu à la passe suivante (premier démarrage)
MAX_TEXTE = 2000


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return 0


class LecteurMessages:
    def __init__(self, page_id, token, graph, get_json, nb_conversations=MAX_CONVERSATIONS,
                 nb_messages=MAX_MESSAGES, max_lectures=MAX_LECTURES_PAR_PASSE, journal=print):
        self.page_id, self.token, self.graph, self.get_json = str(page_id), token, graph, get_json
        self.nb_conversations, self.nb_messages, self.max_lectures = nb_conversations, nb_messages, max_lectures
        self.journal = journal
        self.champs = CHAMPS_CONVERSATIONS
        self.cache = {}   # id de conversation -> {updated_time, conv}
        self.stats = {"relues": 0, "reutilisees": 0}

    def _lister(self):
        url = f"{self.graph}/{self.page_id}/conversations?" + urllib.parse.urlencode(
            {"fields": self.champs, "limit": self.nb_conversations, "access_token": self.token})
        try:
            return comment_store.fetch_all(url, self.get_json, max_pages=1)
        except comment_store.FacebookError as exc:
            if self.champs != CHAMPS_MINIMUM and exc.code == 100:
                # Un champ facultatif est refusé : on garde l'essentiel.
                self.champs = CHAMPS_MINIMUM
                self.journal("Messenger : Facebook refuse certains champs, lecture réduite.")
                return self._lister()
            raise

    def _messages(self, conv_id):
        url = f"{self.graph}/{conv_id}/messages?" + urllib.parse.urlencode(
            {"fields": CHAMPS_MESSAGES, "limit": self.nb_messages, "access_token": self.token})
        return comment_store.fetch_all(url, self.get_json, max_pages=1)

    def _construire(self, brut, messages):
        """Format lu par l'app : messages du plus ancien au plus récent."""
        page = self.page_id
        ordonnes = sorted((m for m in messages if isinstance(m, dict) and m.get("id")),
                          key=lambda m: str(m.get("created_time") or ""))
        sortie = []
        for m in ordonnes[-self.nb_messages:]:
            auteur = m.get("from") or {}
            sortie.append({
                "id": str(m.get("id")),
                "message": str(m.get("message") or "")[:MAX_TEXTE],
                "created_time": m.get("created_time"),
                "from_page": str(auteur.get("id") or "") == page,
                "from_name": auteur.get("name") or "",
            })
        contact = None
        for p in (brut.get("participants") or {}).get("data") or []:
            if str(p.get("id") or "") != page:
                contact = p
                break
        if contact is None:
            for m in ordonnes:
                auteur = m.get("from") or {}
                if str(auteur.get("id") or "") not in ("", page):
                    contact = auteur
                    break
        contact = contact or {}
        derniere_du_contact = ""
        for m in sortie:
            if not m["from_page"]:
                derniere_du_contact = m["created_time"] or ""
        return {
            "id": str(brut.get("id")),
            "updated_time": brut.get("updated_time"),
            "unread_count": _entier(brut.get("unread_count")),
            "message_count": _entier(brut.get("message_count")),
            "with": {"id": str(contact.get("id") or ""), "name": contact.get("name") or ""},
            "last_user_time": derniere_du_contact,
            "repondu": bool(sortie) and sortie[-1]["from_page"],
            "messages": sortie,
        }

    def lire(self):
        """Renvoie les conversations récentes (les plus récentes d'abord)."""
        self.stats = {"relues": 0, "reutilisees": 0}
        brutes = self._lister()
        vus, sortie = set(), []
        for brut in brutes[:self.nb_conversations]:
            cid = str(brut.get("id") or "")
            if not cid:
                continue
            vus.add(cid)
            maj = str(brut.get("updated_time") or "")
            entree = self.cache.get(cid)
            if entree and maj and entree["updated_time"] == maj:
                sortie.append(copy.deepcopy(entree["conv"]))
                self.stats["reutilisees"] += 1
                continue
            if self.stats["relues"] >= self.max_lectures:
                if entree:   # pas le temps de la relire : on garde l'ancienne version
                    sortie.append(copy.deepcopy(entree["conv"]))
                continue
            try:
                messages = self._messages(cid)
            except comment_store.FacebookError as exc:
                if exc.code == 100 and entree:   # conversation illisible : on garde l'ancienne version
                    sortie.append(copy.deepcopy(entree["conv"]))
                    continue
                if exc.code == 100:
                    continue
                raise
            conv = self._construire(brut, messages)
            self.cache[cid] = {"updated_time": maj, "conv": conv}
            sortie.append(copy.deepcopy(conv))
            self.stats["relues"] += 1
        for cid in [c for c in self.cache if c not in vus]:
            del self.cache[cid]
        sortie.sort(key=lambda c: str(c.get("updated_time") or ""), reverse=True)
        return sortie


def construire_cache_messages(conversations, page_id, quand):
    """Contenu de inbox/messages.json (lu par l'app)."""
    return {"updated": quand, "page": {"id": str(page_id)}, "conversations": conversations}


def sans_horodatage(donnees):
    """Pour comparer deux versions du cache sans tenir compte de l'heure de mise à jour."""
    if not isinstance(donnees, dict):
        return donnees
    return {k: v for k, v in donnees.items() if k != "updated"}
