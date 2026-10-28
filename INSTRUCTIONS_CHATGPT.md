# Instructions pour ChatGPT (à lire en premier)

Ce dépôt gère la Page Facebook « Aboubacar Sidik | KI DAS ». Tu n'as aucune mémoire entre les conversations : ce fichier est ta seule source. Suis-le à la lettre.
Avant chaque publication ou modification de publication, lis LIGNE_EDITORIALE.md (positionnement, ton, langues, thèmes, hashtags, émojis, interdits).

Tout se fait en modifiant des fichiers JSON puis en faisant un commit. Ne jamais modifier une entrée dont le status n'est plus "pending".

## Quoi faire selon ma demande
1. Post texte simple : posts/queue.json, entrée {"message":"...","status":"pending"}
2. Post avec photo, vidéo ou programmé : posts/avance.json, entrée {"message":"...","status":"pending"} avec en option :
   - "image_path":"media/photo.jpg" (image dans le dépôt) ou "image_url":"https://..."
   - "video_path":"media/video.mp4" ou "video_url":"https://..."
   - "publish_at":"2026-11-01T09:00:00Z" (heure UTC, entre 10 minutes et 30 jours dans le futur)
3. Supprimer ou modifier un post : posts/actions.json, entrée {"action":"delete","post_id":"...","status":"pending"} ou {"action":"edit","post_id":"...","message":"nouveau texte","status":"pending"}
4. Surveiller : lire inbox/commentaires.json (mis à jour uniquement lors d'un audit). Un commentaire est sans réponse si "page_a_repondu" vaut false.
5. Répondre : posts/replies.json, entrée {"comment_id":"...","message":"...","status":"pending"}
6. Statistiques : lire inbox/statistiques.json (abonnés, engagement, vues, nouveaux abonnés ; un indicateur marqué "erreur" est indisponible chez Facebook). Mis à jour uniquement lors d'un audit.
7. Archive : archive/ contient un résumé des posts. L'archive complète (posts, commentaires, médias, un dossier par mois) est dans le dépôt privé kidasniger/facebook-archive, créée automatiquement le 1er de chaque mois. Si tu n'y as pas accès, dis-le-moi.

## Audit complet (quand je demande un audit)
1. Modifie audit/demande.json avec {"status":"demande","date":"<date et heure UTC actuelles>"} puis fais un commit.
2. Le workflow audit.yml lance, l'un après l'autre : surveiller, statistiques, archiver, archive-mensuelle. Il ne publie rien et ne répond à rien. Il peut durer de 5 à 15 minutes selon la file d'attente de GitHub.
3. Ne relance pas d'audit pendant qu'un autre tourne. Vérifie régulièrement audit/resultat.json : l'audit est fini quand sa date "termine" est postérieure à ta demande. Tant que ce n'est pas le cas, ne présente pas d'anciennes données comme celles de l'audit.
4. Une fois fini, lis inbox/commentaires.json, inbox/statistiques.json et archive/, puis fais-moi un rapport : posts, engagement, commentaires sans réponse, points à améliorer.
5. Pendant un audit, ne publie rien et ne réponds à aucun commentaire sans mon accord.

## Règles générales
- Ne jamais écrire de jeton, de clé ou de données privées dans le dépôt.
- Ne jamais modifier le dossier .github/workflows.
- Ne jamais laisser penser qu'un post est écrit par une IA.
- Ne pas spammer ; ne rien inventer sur les posts ou les produits.

## Règles de réponse aux commentaires
- Ne jamais répondre à un commentaire dont l'auteur est la Page elle-même.
- Ignorer les commentaires de test (par exemple "@followers") et les simples mentions sans question ni message.
- Répondre uniquement à de vrais messages, avec une réponse liée à ce que la personne a écrit.
- Le champ "from" est souvent vide (limite de Facebook) : ne pas en déduire que l'auteur est inconnu ou suspect.
- Toujours proposer les réponses d'abord et attendre mon accord avant de les écrire dans posts/replies.json.
