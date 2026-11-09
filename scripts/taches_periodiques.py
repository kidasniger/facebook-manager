"""Tâches lentes exécutées par la boucle glm-live à intervalle espacé.

Statistiques : les indicateurs de la Page ne changent qu'une fois par jour, inutile de les
relire chaque minute. Même contenu que l'ancien workflow « Statistiques de la Page ».
"""
import datetime as dt
import os
import urllib.parse

METRIQUES = ["page_post_engagements", "page_media_view", "page_views_total",
             "page_follows", "page_daily_follows_unique"]
CHEMIN = ("inbox", "statistiques.json")


def statistiques_a_jour(gm, heures):
    """Vrai si inbox/statistiques.json existe et date de moins de `heures` heures."""
    actuel = gm.jload(gm.P(*CHEMIN), {})
    brut = str((actuel or {}).get("date") or "").rstrip("Z")
    try:
        quand = dt.datetime.fromisoformat(brut).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return False
    return (dt.datetime.now(dt.timezone.utc) - quand).total_seconds() < heures * 3600


def _get(gm, chemin, params):
    params = dict(params, access_token=gm.TOKEN)
    st, res = gm.http(f"{gm.GRAPH}/{chemin}?" + urllib.parse.urlencode(params), timeout=60)
    if st != 200 or not isinstance(res, dict) or res.get("error"):
        err = (res or {}).get("error") if isinstance(res, dict) else None
        message = (err or {}).get("message") if isinstance(err, dict) else None
        return {"erreur": str(message or res.get("erreur") or f"HTTP {st}")[:150]}
    return res


def ecrire_statistiques(gm):
    """Écrit inbox/statistiques.json. Renvoie (indicateurs lus, indicateurs demandés)."""
    sortie = {"date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
              "metriques": {}}
    sortie["page"] = _get(gm, gm.PAGE_ID, {"fields": "name,fan_count,followers_count"})
    sortie["page"].pop("id", None)
    for metrique in METRIQUES:
        d = _get(gm, f"{gm.PAGE_ID}/insights", {"metric": metrique, "period": "day"})
        if "erreur" in d:
            sortie["metriques"][metrique] = d
        else:
            sortie["metriques"][metrique] = [
                {"nom": x.get("name"),
                 "valeurs": [{"date": v.get("end_time"), "valeur": v.get("value")}
                             for v in x.get("values", [])[-7:]]}
                for x in d.get("data", [])]
    gm.jsave(gm.P(*CHEMIN), sortie)
    lus = [k for k, v in sortie["metriques"].items() if isinstance(v, list)]
    return len(lus), len(METRIQUES)
