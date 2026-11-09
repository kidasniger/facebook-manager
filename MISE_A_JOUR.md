# Mise à jour du bot : lecteur unique, cache et files d'attente

## Ce qui change
- **Lecture en cache** (`scripts/lecture_cache.py`) : un appel par tranche de 100 publications donne le nombre de
  commentaires de chacune. Seules les publications dont ce nombre a changé sont relues en profondeur.
  Un balayage complet a lieu toutes les 20 min (variable `BALAYAGE_MINUTES`) comme filet de sécurité.
- **Quota surveillé** : la boucle lit les en-têtes de Facebook (X-Business-Use-Case-Usage / X-App-Usage) et ralentit
  seule : >= 50 % cycle doublé, >= 75 % un cycle toutes les 5 min, >= 90 % pause de 15 min.
- **Codes d'erreur enfin utilisés** : avant, seul le texte de l'erreur remontait, donc jeton expiré et limite
  d'appels n'étaient jamais détectés. Le code 80001 (limite des Pages) et 80000 à 80014 sont gérés, ainsi que 368.
- **GLM Live est le chef d'orchestre** : il écrit `inbox/commentaires.json` (remplace « Surveiller la Page »), exécute
  les files d'attente de l'app (publier, programmer, répondre, modifier/supprimer) et met à jour
  `inbox/statistiques.json` toutes les 6 h (`STATS_HEURES`).
- **Sans doublon** : un élément est marqué (`claimed_at`) et enregistré avant l'appel à Facebook. Si la session
  s'arrête entre-temps, il passe en `error` au redémarrage ; l'app permet de le remettre en attente.
- **API Graph v26.0 partout** (surchargeable avec la variable `FB_GRAPH_VERSION`).
- **Inchangé** : toute la logique de réponse aux commentaires (`glm_manager.gerer_reponses`) et les réglages de l'IA.

## Retour arrière en une variable
Variable Actions `FILE_ATTENTE` :
- absente ou `glm` : GLM Live exécute les files d'attente ; dispatcher et anciens workflows sont inactifs.
- `workflows` : ancien fonctionnement (dispatcher + publier.yml, publier-avance.yml, repondre.yml, actions-posts.yml).
Les workflows manuels `surveiller` et `statistiques` restent utilisables pour forcer une lecture.

## Premier essai conseillé
1. Lancer GLM Live à la main avec `dry_run` = true : aucun envoi, mais le journal montre
   « appels Facebook : N — quota utilisé : X % » à chaque cycle.
2. Vérifier que N tombe à 1 ou 2 hors balayage, puis passer en mode réel.
3. Si le journal affiche « Facebook refuse le comptage des commentaires », la boucle repasse
   automatiquement à la lecture complète (comme avant, mais avec le quota surveillé).

## Non testé contre le vrai Facebook
Tout est couvert par 41 tests et une simulation de la boucle, mais je n'ai pas pu appeler Facebook.
Points à observer au premier lancement : le comptage `comments.filter(stream).summary(true)`
et les envois de la file d'attente (un texte, une photo, une programmation).
