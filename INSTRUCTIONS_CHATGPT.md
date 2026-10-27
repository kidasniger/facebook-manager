# Instructions pour ChatGPT

Tout se fait en modifiant des fichiers JSON puis en faisant un commit. Ne jamais modifier une entrée dont le status n'est plus "pending".

1. Post texte simple : posts/queue.json, entrée {"message":"...","status":"pending"}
2. Post avec photo, vidéo ou programmé : posts/avance.json, entrée {"message":"...","status":"pending"} avec en option :
   - "image_path":"media/photo.jpg" (image dans le dépôt) ou "image_url":"https://..."
   - "video_path":"media/video.mp4" ou "video_url":"https://..."
   - "publish_at":"2026-11-01T09:00:00Z" (heure UTC, entre 10 minutes et 30 jours dans le futur)
3. Supprimer ou modifier un post : posts/actions.json, entrée {"action":"delete","post_id":"...","status":"pending"} ou {"action":"edit","post_id":"...","message":"nouveau texte","status":"pending"}
4. Surveiller : lire inbox/commentaires.json (mis à jour toutes les 3 h)
5. Répondre : posts/replies.json, entrée {"comment_id":"...","message":"...","status":"pending"}

Règles : ne répondre qu'aux commentaires sans réponse, sans spammer. Ne jamais écrire de jeton, de clé ou de données privées dans le dépôt. Ne jamais modifier le dossier .github/workflows.

Dans inbox/commentaires.json, un commentaire est sans réponse si "page_a_repondu" vaut false. "from" donne l auteur, "attachments" décrit les posts photo ou vidéo.

## Règles de réponse aux commentaires
- Ne jamais répondre à un commentaire dont l'auteur est la Page elle-même.
- Ignorer les commentaires de test (par exemple "@followers") et les simples mentions sans question ni message.
- Répondre uniquement à de vrais messages, avec une réponse liée à ce que la personne a écrit. Ne rien inventer sur les posts ou les produits.
- Le champ "from" est souvent vide (limite de Facebook) : ne pas en déduire que l'auteur est inconnu ou suspect.
- Toujours proposer les réponses d'abord et attendre mon accord avant de les écrire dans posts/replies.json.
Avant chaque publication ou modification de publication, lire LIGNE_EDITORIALE.md et respecter son positionnement, son ton, les langues, les thèmes, les règles de hashtags/émojis et les éléments à éviter.

## Audit complet

Pour lancer un audit complet de la Page Facebook :
1. Mettre à jour `audit/demande.json` avec un objet de la forme `{"status":"demande","date":"<date et heure UTC>"}`, en utilisant la date et l'heure UTC réelles de la demande, puis faire un commit.
2. Le workflow `audit.yml` lance les tâches `surveiller`, `statistiques`, `archiver` et `archive-mensuelle`. L'exécution peut prendre environ 5 minutes.
3. Attendre que `audit/resultat.json` contienne une date strictement plus récente que celle de la demande. Ne pas interpréter un ancien résultat comme celui du nouvel audit.
4. Après cette confirmation, lire `inbox/commentaires.json`, `inbox/statistiques.json` et les fichiers pertinents de `archive/`, puis rédiger un rapport couvrant les publications, l'engagement, les commentaires sans réponse et les points à améliorer.
5. Pendant toute la durée de l'audit, ne publier aucun contenu, ne modifier aucune publication et ne répondre à aucun commentaire sans l'accord explicite de l'utilisateur. L'audit est une opération de lecture et de collecte de données ; ne lancer aucune autre action de publication ou de réponse.

