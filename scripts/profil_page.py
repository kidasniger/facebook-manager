#!/usr/bin/env python3
"""Profil d'une Page (personnalisation du bot) : schéma, nettoyage, complétion, données du prompt.

Firestore :
  users/{uid}/profil/{pageId}        profil validé (écrit par l'appli)
  users/{uid}/proposition/{pageId}   proposition du bot (lecture des posts), jamais appliquée seule
  users/{uid}/questions/{id}         questions au propriétaire (champ pageId)

Module pur, sans réseau. Le profil est une DONNÉE de la Page : bloc_donnees() le place dans un
bloc délimité et nettoyé, jamais dans les consignes. Rien ici n'est écrit dans les logs.
"""
import datetime as dt
import json
import re
import unicodedata

import pages_schema as sc

TONS = ("chaleureux", "neutre", "direct")
_LANGUE = re.compile(r"^[a-z]{2,3}$")
_HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_QID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
DEBUT, FIN = "<<<DONNEES_PAGE", "DONNEES_PAGE>>>"

# champ texte -> longueur maximale
TEXTES = {"activite": 300, "public": 200, "horaires": 200, "reponsePrix": 300}
# champ liste -> (nombre maximal, longueur maximale par élément)
LISTES = {"langues": (5, 10), "eviter": (20, 100), "motsInterdits": (50, 40)}
OFFRES_MAX, FAQ_MAX = 20, 20
# champs qui comptent dans la complétion (ordre = ordre des questions)
COMPLETION = ("activite", "ton", "public", "horaires", "offres", "eviter", "langues", "reponsePrix", "faq")


def _texte(valeur, limite):
    """Texte sur une ligne, sans caractères de contrôle ni marqueurs du bloc de données."""
    t = "".join(ch for ch in str(valeur or "") if unicodedata.category(ch)[0] != "C" or ch in " \n\t")
    t = t.replace(DEBUT, "").replace(FIN, "").replace("<<<", "").replace(">>>", "")
    return sc.tronquer(t, limite)


def _liste(valeur, nombre, limite):
    vus, out = set(), []
    for v in valeur if isinstance(valeur, list) else []:
        t = _texte(v, limite)
        if t and t.lower() not in vus:
            vus.add(t.lower())
            out.append(t)
        if len(out) >= nombre:
            break
    return out


def nettoyer(brut):
    """Document Firestore décodé -> profil propre. Champs inconnus ignorés."""
    b = brut if isinstance(brut, dict) else {}
    p = {k: _texte(b.get(k), n) for k, n in TEXTES.items() if _texte(b.get(k), n)}
    for k, (nombre, limite) in LISTES.items():
        valeurs = _liste(b.get(k), nombre, limite)
        if k == "langues":
            valeurs = [v.lower() for v in valeurs if _LANGUE.match(v.lower())]
        if valeurs:
            p[k] = valeurs
    if b.get("ton") in TONS:
        p["ton"] = b["ton"]
    offres = []
    for o in b.get("offres") if isinstance(b.get("offres"), list) else []:
        if isinstance(o, dict) and _texte(o.get("nom"), 80):
            offres.append({"nom": _texte(o.get("nom"), 80), "prix": _texte(o.get("prix"), 40)})
        if len(offres) >= OFFRES_MAX:
            break
    if offres:
        p["offres"] = offres
    faq = []
    for f in b.get("faq") if isinstance(b.get("faq"), list) else []:
        if isinstance(f, dict) and _texte(f.get("q"), 200) and _texte(f.get("r"), 400):
            faq.append({"q": _texte(f.get("q"), 200), "r": _texte(f.get("r"), 400)})
        if len(faq) >= FAQ_MAX:
            break
    if faq:
        p["faq"] = faq
    s = b.get("silence") if isinstance(b.get("silence"), dict) else {}
    if s.get("actif") is True and _HEURE.match(str(s.get("debut"))) and _HEURE.match(str(s.get("fin"))):
        try:
            decalage = max(-840, min(840, int(s.get("decalageMin") or 0)))
        except (TypeError, ValueError):
            decalage = 0
        p["silence"] = {"actif": True, "debut": s["debut"], "fin": s["fin"], "decalageMin": decalage}
    p["reponsesAuto"] = b.get("reponsesAuto") is not False      # actif par défaut
    if b.get("dryRun") is True:
        p["dryRun"] = True
    return p


def champs_vides(profil):
    """Champs de la complétion encore vides, dans l'ordre des questions."""
    p = profil or {}
    return [k for k in COMPLETION if not p.get(k)]


def completion(profil):
    """Pourcentage (0 à 100) des champs remplis."""
    return round(100 * (len(COMPLETION) - len(champs_vides(profil))) / len(COMPLETION))


def en_silence(profil, maintenant):
    """True si l'heure locale de la Page est dans la plage de silence (gère minuit)."""
    s = (profil or {}).get("silence")
    if not isinstance(s, dict) or s.get("actif") is not True:
        return False
    local = maintenant.astimezone(dt.timezone.utc) + dt.timedelta(minutes=int(s.get("decalageMin") or 0))
    m = local.hour * 60 + local.minute
    d = int(s["debut"][:2]) * 60 + int(s["debut"][3:])
    f = int(s["fin"][:2]) * 60 + int(s["fin"][3:])
    if d == f:
        return False
    return d <= m < f if d < f else (m >= d or m < f)


def mot_interdit(profil, texte):
    """True si le texte contient un mot interdit du profil (sans accents ni majuscules, 2 lettres min)."""
    def simple(s):
        return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    t = simple(texte)
    return any(len(simple(m).strip()) >= 2 and simple(m).strip() in t
               for m in (profil or {}).get("motsInterdits") or [])


def bloc_donnees(profil):
    """Profil validé -> bloc de DONNÉES pour le prompt (JSON délimité). Vide si rien d'utile."""
    p = {k: v for k, v in (profil or {}).items()
         if k in TEXTES or k in ("ton", "langues", "eviter", "offres", "faq")}
    if not p:
        return ""
    return DEBUT + "\n" + json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" + FIN


# ---------- chemins ----------
def chemin_proposition(uid, page_id):
    sc.chemin_profil(uid, page_id)                         # même validation des identifiants
    return f"users/{uid}/proposition/{page_id}"


def chemin_question(uid, question_id):
    sc.chemin_file(uid, "x")                               # validation de l'uid
    if not _QID.match(str(question_id)):
        raise ValueError("identifiant de question invalide")
    return f"users/{uid}/questions/{question_id}"
