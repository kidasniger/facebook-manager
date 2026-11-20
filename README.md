# Facebook Manager

Moteur d'automatisation de la Page Facebook, piloté par KI DAS Contrôle.

## Bot opérationnel unique

Le seul workflow qui exécute les actions du bot est **[Facebook Manager — Bot central](.github/workflows/glm-live.yml)**.

Il lance `scripts/glm_boucle.py`, qui orchestre :
- lecture paginée des publications, commentaires et réponses imbriquées ;
- réponses GLM, vérification de leur présence réelle et registre anti-doublons ;
- likes automatiques des nouvelles publications, avec plafond et mémoire ;
- publication de textes, photos et vidéos, et programmation ;
- réponses Messenger et actions sur les publications/commentaires ;
- boîte de réception, état de santé et statistiques périodiques.

La boucle contrôle les nouveautés toutes les 60 secondes par défaut. Un balayage complet périodique complète le cache incrémental. Le watchdog est un deuxième job **dans le même fichier de workflow** : il vérifie toutes les 5 minutes que le bot tourne et le relance si nécessaire.

Les tâches de maintenance (archive, apprentissage, assistant et audit) ainsi que le workflow de tests restent séparés : ils ne lancent pas de second moteur de réponses ou de publication.

## Variables GitHub Actions

- `GLM_LIVE=off` : arrête le bot et empêche le watchdog de le relancer.
- Le réglage « Réponses automatiques » est lu depuis `glm/config.json` et rechargé à chaque cycle.
- `FILE_ATTENTE=glm` : fait traiter les files d'attente par le bot central (valeur recommandée).
- `BALAYAGE_MINUTES` : intervalle du balayage complet (20 par défaut).
- `MESSAGES_SECONDES` : intervalle de lecture Messenger (120 par défaut).
- `STATS_HEURES` : intervalle des statistiques (6 par défaut).

## Secrets requis

- `DATA_PAT` : accès au dépôt privé `kidasniger/facebook-data`.
- `FB_PAGE_TOKEN` et `FB_PAGE_ID` : accès à la Page Facebook.
- `ZAI_API_KEY` : génération des réponses GLM.

Les données opérationnelles, historiques, journaux et files d'attente restent dans le dépôt privé. Aucun jeton ne doit être écrit dans le dépôt public.

## Tests

Le workflow `test-comment-processing.yml` exécute la compilation syntaxique Python et les tests unitaires, notamment les tests de pagination, de reprise, de visibilité des réponses, de likes et de files d'attente.
