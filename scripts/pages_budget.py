#!/usr/bin/env python3
"""Budget d'écritures Firestore (offre gratuite Spark : 20 000 écritures par jour).

Module pur, sans réseau. Compte les écritures du bot pour le jour UTC en cours.
- cible = limite * (1 - reserve) : au-delà, les écritures ordinaires sont refusées ;
  la réserve est laissée aux écritures de l'appli (file, traite).
- les écritures prioritaires (résultats de la file) passent jusqu'à la limite.
- facteur() : multiplicateur (1 à FACTEUR_MAX) à appliquer à la fréquence par Page quand la
  projection de la journée dépasse la cible.
Le compteur est en mémoire : il repart de zéro à chaque session du bot (voir persistance plus tard).
"""
import datetime as dt
import time

LIMITE_JOUR = 20000
RESERVE = 0.2
FACTEUR_MAX = 8.0
FRACTION_MIN = 0.05   # pas de projection fiable avant 5 % de la journée (72 min)


class BudgetEcritures:
    def __init__(self, limite=LIMITE_JOUR, reserve=RESERVE, maintenant=time.time, utilise=0):
        self.limite = max(1, int(limite))
        self.reserve = min(0.9, max(0.0, float(reserve)))
        self._now = maintenant
        self.jour = self._jour()
        self.utilise = max(0, int(utilise))

    def _moment(self):
        return dt.datetime.fromtimestamp(self._now(), dt.timezone.utc)

    def _jour(self):
        return self._moment().strftime("%Y-%m-%d")

    def _maj_jour(self):
        jour = self._jour()
        if jour != self.jour:
            self.jour, self.utilise = jour, 0

    @property
    def cible(self):
        return int(self.limite * (1 - self.reserve))

    def enregistrer(self, nombre):
        """Compte des écritures réellement envoyées."""
        self._maj_jour()
        self.utilise += max(0, int(nombre))
        return self.utilise

    def restant(self, prioritaire=False):
        self._maj_jour()
        plafond = self.limite if prioritaire else self.cible
        return max(0, plafond - self.utilise)

    def autoriser(self, nombre, prioritaire=False):
        """True si `nombre` écritures tiennent dans le budget."""
        return int(nombre) <= self.restant(prioritaire)

    def fraction_jour(self):
        m = self._moment()
        return (m.hour * 3600 + m.minute * 60 + m.second) / 86400.0

    def facteur(self):
        """Ralentissement conseillé (1.0 = normal) d'après la projection de la journée."""
        self._maj_jour()
        if self.utilise >= self.cible:
            return FACTEUR_MAX
        fraction = self.fraction_jour()
        if fraction < FRACTION_MIN:
            return 1.0
        projection = self.utilise / fraction
        if projection <= self.cible:
            return 1.0
        return min(FACTEUR_MAX, projection / self.cible)

    def resume(self):
        """Compteurs seulement, pour les logs."""
        self._maj_jour()
        return {"utilise": self.utilise, "cible": self.cible, "limite": self.limite,
                "facteur": round(self.facteur(), 2)}
