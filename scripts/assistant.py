#!/usr/bin/env python3
"""Assistant IA de l'application KI DAS Contrôle : répond via glm-4.5.

Lancé par .github/workflows/assistant.yml (workflow_dispatch, appelé par
l'application). Deux modes :
- sans input « message » : lit la conversation glm/chat.json du dépôt de
  données, répond au dernier message utilisateur et ajoute la réponse au
  même fichier (mode discussion de l'écran Assistant IA) ;
- avec input « message » : répond à ce message seul et écrit le résultat
  dans glm/reponse.json (mode suggestion de réponse d'un commentaire).

Secrets requis : ZAI_API_KEY. Optionnels : ZAI_BASE_URL, ZAI_MODEL.
Le modèle et l'API sont repris de glm/config.json (comme le reste du bot).
La clé ne doit jamais être affichée ni écrite dans le dépôt.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DATA = os.environ.get("DATA_DIR") or "data"


def lire_cfg():
    try:
        with open(os.path.join(DATA, "glm", "config.json"), encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


CFG = lire_cfg()
_url = (os.environ.get("ZAI_BASE_URL") or CFG.get("api_url") or
        "https://api.z.ai/api/paas/v4/chat/completions").strip()
if not _url.endswith("/chat/completions"):
    _url = _url.rstrip("/") + "/chat/completions"
ZAI_URL = _url
MODEL = (os.environ.get("ZAI_MODEL") or CFG.get("modele") or "glm-4.5-flash").strip()
CHAT = os.path.join(DATA, "glm", "chat.json")
REPONSE = os.path.join(DATA, "glm", "reponse.json")
INPUT_MESSAGE = (os.environ.get("INPUT_MESSAGE") or "").strip()

BASE = (
    "Tu es l'assistant IA intégré à KI DAS Contrôle, l'application de gestion "
    "de la Page Facebook « Aboubacar Sidik | KI DAS ». Tu discutes avec "
    "Aboubacar, le gérant de la Page (pas avec les clients). Ton rôle : "
    "l'aider à comprendre ce qui se passe sur sa Page, préparer des réponses "
    "aux commentaires et aux messages, proposer des publications conformes à "
    "la ligne éditoriale et conseiller. Réponds toujours en français, de "
    "façon concise, concrète et actionnable (listes courtes, réponses prêtes "
    "à copier). Si une information manque dans le contexte, dis-le "
    "franchement au lieu d'inventer."
)


def read_text(path, limit):
    try:
        with open(path, encoding="utf-8") as f:
            t = f.read().strip()
        return t[:limit] if t else ""
    except OSError:
        return ""


def system_prompt():
    parties = [BASE]
    for nom, limite in (
        ("PERSONNALISATION_CHATGPT.md", 2400),
        ("LIGNE_EDITORIALE.md", 3500),
        ("faits.md", 3500),
    ):
        t = read_text(os.path.join(DATA, nom), limite)
        if t:
            parties.append("=== " + nom + " ===\n" + t)
    return "\n\n".join(parties)


def http(url, payload, key, timeout=120):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:  # réseau, délai dépassé
        return 0, {"error": {"message": type(e).__name__}}


def glm(messages):
    key = os.environ.get("ZAI_API_KEY", "")
    if not key:
        raise RuntimeError("ZAI_API_KEY manquant (secret GitHub)")
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 900,
        "thinking": {"type": "disabled"},
    }
    code = 0
    for _ in range(3):
        code, res = http(ZAI_URL, payload, key)
        if code == 200 and res.get("choices"):
            m = res["choices"][0].get("message") or {}
            return (m.get("content") or "").strip()
        if code == 400 and "thinking" in payload:
            payload.pop("thinking")  # modèle sans option thinking : on réessaie
            continue
        if code in (429, 500, 502, 503, 0):
            print("Essai Z.ai (HTTP %s) : %s" % (code, str(res.get("error") or res)[:160]))
            time.sleep(6)
            continue
        raise RuntimeError("Erreur API Z.ai (HTTP %s) : %s" % (code, str(res)[:200]))
    raise RuntimeError("API Z.ai indisponible après plusieurs essais (HTTP %s)." % code)


def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mode_discussion():
    """Lit glm/chat.json, répond au dernier message utilisateur, y ajoute 'a'."""
    try:
        with open(CHAT, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = []
    if not isinstance(hist, list):
        hist = []
    conv = [
        m
        for m in hist
        if isinstance(m, dict) and m.get("r") in ("u", "a") and str(m.get("t") or "").strip()
    ][-16:]
    if not conv:
        print("glm/chat.json vide ou sans message utilisateur : rien à faire.")
        return
    messages = [{"role": "system", "content": system_prompt()}]
    for m in conv:
        role = "user" if m.get("r") == "u" else "assistant"
        messages.append({"role": role, "content": str(m.get("t"))})
    reponse = glm(messages) or ""
    if not reponse:
        reponse = "Je n'ai pas réussi à formuler une réponse, réessaie."
    hist.append({"r": "a", "t": reponse, "at": maintenant()})
    with open(CHAT, "w", encoding="utf-8") as f:
        json.dump(hist[-200:], f, ensure_ascii=False, indent=1)
    print("Réponse écrite dans glm/chat.json (%s caractères)." % len(reponse))


def mode_message():
    """Répond à l'input « message » seul et écrit glm/reponse.json."""
    messages = [{"role": "system", "content": system_prompt()}]
    messages.append({"role": "user", "content": INPUT_MESSAGE})
    reponse = glm(messages) or ""
    if not reponse:
        reponse = "Je n'ai pas réussi à formuler une réponse, réessaie."
    with open(REPONSE, "w", encoding="utf-8") as f:
        json.dump({"t": reponse, "at": maintenant()}, f, ensure_ascii=False, indent=1)
    print("Réponse écrite dans glm/reponse.json (%s caractères)." % len(reponse))


if __name__ == "__main__":
    if INPUT_MESSAGE:
        mode_message()
    else:
        mode_discussion()
