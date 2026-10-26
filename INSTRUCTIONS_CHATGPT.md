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
