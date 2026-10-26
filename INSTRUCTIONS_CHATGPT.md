# Instructions pour ChatGPT

- Publier : ajouter dans posts/queue.json une entrée {"message":"...","status":"pending"}, puis commit. Ne jamais modifier les entrées "published" ou "error".
- Surveiller : lire inbox/commentaires.json (mis à jour toutes les 3 h).
- Répondre : ajouter dans posts/replies.json une entrée {"comment_id":"...","message":"...","status":"pending"} (l'id vient de inbox/commentaires.json), puis commit.
- Ne répondre qu'aux commentaires sans réponse, sans spammer.
- Ne jamais écrire de jeton, de clé ou de données privées dans le dépôt.
