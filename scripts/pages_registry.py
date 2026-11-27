#!/usr/bin/env python3
"""Registre des Pages My Page : lecture de Firestore par REST (compte de service).

Schéma lu : users/{uid}/pages/{pageId}
  écrit par l'appli : nom, tokenEnc, demande (connecter | deconnecter), maj
  écrit par le bot  : status (active | erreur | deconnectee), message

Économie de lectures (offre gratuite) :
- lecture complète au démarrage, puis seulement les documents dont `maj` est
  supérieur ou égal au dernier vu (une requête sans résultat coûte 1 lecture) ;
- resynchronisation complète toutes les RESYNC_SECONDS pour voir les suppressions ;
- les écritures du bot ne modifient pas `maj` : pas de boucle.

Sécurité (dépôt public, logs visibles par tous) : ce module n'écrit jamais dans
les logs un identifiant, un nom, un jeton ou un contenu. Les erreurs ne portent
que le code HTTP et un message court sans lien.
Dépendance : cryptography (signature RS256 du jeton du compte de service).
"""
import base64
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://firestore.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/datastore"
TAILLE_PAGE = 300
LOT_MAX = 400  # limite Firestore : 500 écritures par commit
RESYNC_SECONDS = 1800
ETATS_SERVIS = ("attente", "active")


class FirestoreError(RuntimeError):
    """Erreur Firestore. Le message ne contient jamais de jeton ni de donnée utilisateur."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---------- compte de service ----------
def charger_compte_service(brut):
    """Lit le secret FIREBASE_SERVICE_ACCOUNT (JSON, ou JSON encodé en base64)."""
    texte = (brut or "").strip()
    if not texte:
        raise ValueError("FIREBASE_SERVICE_ACCOUNT absent")
    if not texte.startswith("{"):
        try:
            texte = base64.b64decode(texte).decode("utf-8")
        except Exception:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT illisible") from None
    try:
        data = json.loads(texte)
    except ValueError:
        raise ValueError("FIREBASE_SERVICE_ACCOUNT n'est pas un JSON valide") from None
    if not isinstance(data, dict):
        raise ValueError("FIREBASE_SERVICE_ACCOUNT n'est pas un objet JSON")
    for cle in ("client_email", "private_key", "project_id"):
        if not isinstance(data.get(cle), str) or not data[cle]:
            raise ValueError(f"FIREBASE_SERVICE_ACCOUNT : champ {cle} manquant")
    return data


def _b64url(octets):
    return base64.urlsafe_b64encode(octets).rstrip(b"=").decode("ascii")


def _signer_rs256(cle_pem, message):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    cle = serialization.load_pem_private_key(cle_pem.encode("utf-8"), password=None)
    return cle.sign(message, padding.PKCS1v15(), hashes.SHA256())


# ---------- transport ----------
def http_defaut(methode, url, en_tetes, corps, timeout=60):
    """Renvoie (statut, JSON décodé). Statut 0 : erreur réseau."""
    req = urllib.request.Request(url, data=corps, headers=en_tetes or {}, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rep:
            statut = rep.status
            brut = rep.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        statut = exc.code
        brut = exc.read().decode("utf-8", "replace") or "{}"
    except Exception as exc:
        return 0, {"erreur": type(exc).__name__}
    try:
        return statut, json.loads(brut)
    except ValueError:
        return statut, {"erreur": "réponse illisible"}


def _message_erreur(statut, data):
    err = None
    if isinstance(data, dict):
        err = data.get("error")
        if err is None and data.get("erreur"):
            return f"Firestore HTTP {statut} : {str(data['erreur'])[:40]}"
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        err = data[0].get("error")
    if isinstance(err, dict):
        etat = str(err.get("status") or "")
        detail = re.sub(r"https?://\S+", "[lien]", str(err.get("message") or ""))[:120]
        return f"Firestore HTTP {statut} {etat} : {detail}".strip()
    return f"Firestore HTTP {statut}"


class Firestore:
    """Client REST minimal : jeton OAuth du compte de service + requêtes."""

    def __init__(self, compte, http=None, maintenant=time.time, dormir=time.sleep):
        self._compte = compte
        self.projet = compte["project_id"]
        self._http = http or http_defaut
        self._now = maintenant
        self._dormir = dormir
        self._jeton = None
        self._expire = 0.0
        self.lectures = 0
        self.ecritures = 0

    def _jeton_acces(self):
        if self._jeton and self._now() < self._expire - 60:
            return self._jeton
        t = int(self._now())
        entete = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        charge = _b64url(json.dumps({
            "iss": self._compte["client_email"], "scope": SCOPE,
            "aud": TOKEN_URL, "iat": t, "exp": t + 3600,
        }, separators=(",", ":")).encode())
        signature = _b64url(_signer_rs256(self._compte["private_key"], f"{entete}.{charge}".encode()))
        corps = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": f"{entete}.{charge}.{signature}",
        }).encode()
        statut, data = self._http("POST", TOKEN_URL,
                                  {"Content-Type": "application/x-www-form-urlencoded"}, corps)
        if statut != 200 or not isinstance(data, dict) or not data.get("access_token"):
            motif = str(data.get("error") or "")[:40] if isinstance(data, dict) else ""
            raise FirestoreError(f"Jeton Google refusé (HTTP {statut}) {motif}".strip(), status=statut)
        self._jeton = data["access_token"]
        self._expire = self._now() + int(data.get("expires_in") or 3600)
        return self._jeton

    def _appeler(self, methode, url, corps=None):
        donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
        statut, data = 0, {}
        for tentative in range(3):
            en_tetes = {"Authorization": "Bearer " + self._jeton_acces(),
                        "Content-Type": "application/json"}
            statut, data = self._http(methode, url, en_tetes, donnees)
            if 200 <= statut < 300:
                return data
            if statut == 401:
                self._jeton = None
            elif statut not in (0, 429, 500, 502, 503, 504):
                break
            if tentative < 2:
                self._dormir(2 ** tentative)
        raise FirestoreError(_message_erreur(statut, data), status=statut)

    def requete(self, requete_structuree):
        """Exécute une requête structurée et renvoie la liste des documents bruts."""
        url = f"{API}/projects/{self.projet}/databases/(default)/documents:runQuery"
        data = self._appeler("POST", url, {"structuredQuery": requete_structuree})
        docs = []
        if isinstance(data, list):
            docs = [e["document"] for e in data if isinstance(e, dict) and e.get("document")]
        self.lectures += max(1, len(docs))
        return docs

    def lire_document(self, chemin):
        """Lit un document par son chemin. Renvoie ses champs décodés, ou None s'il n'existe pas."""
        url = f"{API}/{nom_document(self.projet, chemin)}"
        self.lectures += 1
        try:
            data = self._appeler("GET", url)
        except FirestoreError as exc:
            if exc.status == 404:
                return None
            raise
        return decoder_champs((data or {}).get("fields"))

    def commit(self, ecritures):
        """Applique des écritures (voir ecriture_maj / ecriture_suppression) par lots atomiques
        de LOT_MAX. Renvoie le nombre d'écritures envoyées. Un lot qui échoue lève FirestoreError."""
        url = f"{API}/projects/{self.projet}/databases/(default)/documents:commit"
        faits = 0
        for i in range(0, len(ecritures), LOT_MAX):
            lot = ecritures[i:i + LOT_MAX]
            self._appeler("POST", url, {"writes": lot})
            self.ecritures += len(lot)
            faits += len(lot)
        return faits


# ---------- écriture ----------
_NOM_CHAMP = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def nom_document(projet, chemin):
    return f"projects/{projet}/databases/(default)/documents/{str(chemin).strip('/')}"


def encoder_valeur(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, dt.datetime):
        u = v.astimezone(dt.timezone.utc) if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
        return {"timestampValue": u.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {str(k): encoder_valeur(x) for k, x in v.items()}}}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [encoder_valeur(x) for x in v]}}
    raise TypeError("type de valeur non pris en charge")


def ecriture_maj(projet, chemin, champs, supprimer=(), existe=None):
    """Écriture partielle : seuls les champs donnés sont modifiés, ceux de `supprimer` sont effacés.
    existe=True : échoue si le document n'existe pas ; False : échoue s'il existe ; None : sans condition."""
    noms = list(champs) + list(supprimer)
    if any(not _NOM_CHAMP.match(str(n)) for n in noms):
        raise ValueError("nom de champ invalide")
    if set(champs) & set(supprimer):
        raise ValueError("champ à la fois écrit et supprimé")
    ecriture = {
        "update": {"name": nom_document(projet, chemin),
                   "fields": {k: encoder_valeur(v) for k, v in champs.items()}},
        "updateMask": {"fieldPaths": noms},
    }
    if existe is not None:
        ecriture["currentDocument"] = {"exists": bool(existe)}
    return ecriture


def ecriture_suppression(projet, chemin, existe=None):
    ecriture = {"delete": nom_document(projet, chemin)}
    if existe is not None:
        ecriture["currentDocument"] = {"exists": bool(existe)}
    return ecriture


def depuis_env(env=None, http=None):
    env = os.environ if env is None else env
    return Firestore(charger_compte_service(env.get("FIREBASE_SERVICE_ACCOUNT", "")), http=http)


# ---------- décodage des documents ----------
def decoder_valeur(v):
    if not isinstance(v, dict):
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "booleanValue" in v:
        return bool(v["booleanValue"])
    if "timestampValue" in v:
        return v["timestampValue"]
    if "mapValue" in v:
        return decoder_champs((v["mapValue"] or {}).get("fields"))
    if "arrayValue" in v:
        return [decoder_valeur(x) for x in (v["arrayValue"] or {}).get("values") or []]
    return None


def decoder_champs(champs):
    return {k: decoder_valeur(v) for k, v in (champs or {}).items()}


_NOM = re.compile(r"/documents/users/([^/]+)/pages/([^/]+)$")


def decoder_page(doc):
    """Document brut -> dict avec uid et page_id, ou None si ce n'est pas users/*/pages/*."""
    m = _NOM.search(str((doc or {}).get("name") or ""))
    if not m:
        return None
    rec = decoder_champs(doc.get("fields"))
    rec["uid"], rec["page_id"] = m.group(1), m.group(2)
    if doc.get("updateTime"):
        # Version serveur : sert de précondition aux écritures du bot (voir pages_tokens.py).
        rec["_update_time"] = doc["updateTime"]
    return rec


def cle_page(rec):
    return (rec["uid"], rec["page_id"])


def etat_page(rec):
    """Même logique que l'appli (page_link.dart) : attente, active, erreur, deconnexion, deconnectee."""
    demande, status = rec.get("demande"), rec.get("status")
    if demande == "connecter":
        return "attente"
    if demande == "deconnecter":
        return "deconnexion"
    if status == "active":
        return "active"
    if status == "erreur":
        return "erreur"
    if status == "deconnectee":
        return "deconnectee"
    return "attente"


def _horodatage(texte):
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z$", str(texte or ""))
    if not m:
        return None
    micro = int((m.group(2) or ".0")[1:7].ljust(6, "0"))
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    return base.replace(microsecond=micro, tzinfo=dt.timezone.utc)


# ---------- registre ----------
class RegistrePages:
    def __init__(self, fs, resync_secondes=RESYNC_SECONDS, maintenant=time.time):
        self.fs = fs
        self.resync = resync_secondes
        self._now = maintenant
        self.pages = {}
        self.curseur = None
        self.dernier_complet = 0.0

    def _lire(self, depuis=None):
        """Documents pages dont maj >= depuis (tous si None), par lots de TAILLE_PAGE."""
        sortie = []
        curseur = depuis
        while True:
            requete = {
                "from": [{"collectionId": "pages", "allDescendants": True}],
                "orderBy": [{"field": {"fieldPath": "maj"}, "direction": "ASCENDING"}],
                "limit": TAILLE_PAGE,
            }
            if curseur:
                requete["where"] = {"fieldFilter": {
                    "field": {"fieldPath": "maj"}, "op": "GREATER_THAN_OR_EQUAL",
                    "value": {"timestampValue": curseur}}}
            docs = self.fs.requete(requete)
            lot = [r for r in (decoder_page(d) for d in docs) if r]
            sortie.extend(lot)
            if len(docs) < TAILLE_PAGE or not lot:
                break
            dernier = lot[-1].get("maj")
            if not dernier or dernier == curseur:
                break
            curseur = dernier
        return sortie

    def _avancer(self, maj):
        h = _horodatage(maj)
        if h is None:
            return
        if self.curseur is None or h >= _horodatage(self.curseur):
            self.curseur = maj

    def rafraichir(self, forcer_complet=False):
        """Met le registre à jour. Renvoie {nouvelles, modifiees, supprimees} (listes de clés)."""
        maintenant = self._now()
        complet = (forcer_complet or self.dernier_complet == 0.0
                   or maintenant - self.dernier_complet >= self.resync)
        lus = self._lire(None if complet else self.curseur)
        bilan = {"nouvelles": [], "modifiees": [], "supprimees": []}
        for rec in lus:
            k = cle_page(rec)
            ancien = self.pages.get(k)
            if ancien is None:
                bilan["nouvelles"].append(k)
            elif ancien != rec:
                bilan["modifiees"].append(k)
            self.pages[k] = rec
            self._avancer(rec.get("maj"))
        if complet:
            vus = {cle_page(r) for r in lus}
            for k in [k for k in self.pages if k not in vus]:
                del self.pages[k]
                bilan["supprimees"].append(k)
            self.dernier_complet = maintenant
        return bilan

    def appliquer_local(self, uid, page_id, champs):
        """Reporte en mémoire une écriture du bot (status, message...) qui ne change pas `maj`."""
        rec = self.pages.get((uid, page_id))
        if rec is not None:
            rec.update(champs)
            for cle in [c for c, v in champs.items() if v is None]:
                rec.pop(cle, None)

    def _filtrer(self, predicat):
        return [r for _, r in sorted(self.pages.items()) if predicat(r)]

    def a_traiter(self):
        """Pages avec une demande de l'appli (connecter ou deconnecter)."""
        return self._filtrer(lambda r: r.get("demande") in ("connecter", "deconnecter"))

    def actives(self):
        return self._filtrer(lambda r: etat_page(r) == "active")

    def a_servir(self):
        """Pages en attente ou actives."""
        return self._filtrer(lambda r: etat_page(r) in ETATS_SERVIS)

    def resume(self):
        """Compteurs seulement (sans identifiant) : sûr pour les logs d'un dépôt public."""
        compte = {}
        for rec in self.pages.values():
            e = etat_page(rec)
            compte[e] = compte.get(e, 0) + 1
        compte["total"] = len(self.pages)
        compte["lectures"] = self.fs.lectures
        compte["ecritures"] = self.fs.ecritures
        return compte
