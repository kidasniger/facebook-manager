#!/usr/bin/env python3
"""Écriture groupée des données par Page vers Firestore, sous budget.

- ajouter() met un document en file ; un document identique au dernier envoyé est ignoré
  (empreinte en mémoire) : aucune écriture gaspillée.
- vider() envoie la file par lots (pr.LOT_MAX), prioritaires d'abord, dans la limite du budget.
  Un lot refusé reste en file pour le cycle suivant ; rien n'est compté tant qu'il n'est pas passé.
- dry_run : rien n'est envoyé, rien n'est compté.
Dépôt public : seuls des compteurs sont renvoyés, jamais de chemin, d'identifiant ni de texte.
"""
import pages_registry as pr
import pages_schema as sc

FILE_MAX = 2000        # documents en attente au plus (les plus anciens sont abandonnés)
EMPREINTES_MAX = 5000  # taille du cache d'empreintes


class Synchro:
    def __init__(self, fs, budget, dry_run=False):
        self.fs = fs
        self.budget = budget
        self.dry_run = bool(dry_run)
        self.file = {}          # chemin -> (champs, supprimer, prioritaire, empreinte)
        self.empreintes = {}    # chemin -> empreinte du dernier envoi réussi

    def ajouter(self, chemin, champs, supprimer=None, prioritaire=False):
        """Met un document en file. Renvoie False s'il est identique au dernier envoyé."""
        supprimer = list(supprimer or [])
        empreinte = sc.empreinte({"c": champs, "s": supprimer})
        if self.empreintes.get(chemin) == empreinte and chemin not in self.file:
            return False
        if chemin in self.file:
            prioritaire = prioritaire or self.file[chemin][2]
        self.file[chemin] = (dict(champs), supprimer, bool(prioritaire), empreinte)
        while len(self.file) > FILE_MAX:
            self.file.pop(next(iter(self.file)))
        return True

    def vider(self):
        """Envoie la file sous budget. Renvoie des compteurs."""
        bilan = {"envoyes": 0, "restants": 0, "refus_budget": 0, "erreurs": 0}
        if self.dry_run:
            bilan["restants"] = len(self.file)
            return bilan
        ordre = sorted(self.file.items(), key=lambda kv: not kv[1][2])   # prioritaires d'abord
        taille = pr.LOT_MAX
        for debut in range(0, len(ordre), taille):
            lot = ordre[debut:debut + taille]
            prioritaire = all(v[2] for _, v in lot)
            if not self.budget.autoriser(len(lot), prioritaire=prioritaire):
                bilan["refus_budget"] += len(lot)
                continue
            ecritures = [pr.ecriture_maj(self.fs.projet, chemin, champs, supprimer=supprimer)
                         for chemin, (champs, supprimer, _, _) in lot]
            try:
                self.fs.commit(ecritures)
            except pr.FirestoreError:
                bilan["erreurs"] += len(lot)
                continue
            self.budget.enregistrer(len(lot))
            bilan["envoyes"] += len(lot)
            for chemin, (_, _, _, empreinte) in lot:
                self.file.pop(chemin, None)
                self.empreintes[chemin] = empreinte
        if len(self.empreintes) > EMPREINTES_MAX:
            self.empreintes.clear()
        bilan["restants"] = len(self.file)
        return bilan
