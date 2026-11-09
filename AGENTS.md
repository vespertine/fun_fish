# Consignes pour les agents

À lire avant toute action dans ce dépôt. Pour comprendre le projet (code, choix d'un coup, outils
de mesure), voir [ONBOARDING.md](ONBOARDING.md).

## Langue

Toujours répondre **en français**, avec les accents. Le code, les commentaires, les messages de
commit et les descriptions de PR sont aussi en français.

## Identité Git et GitHub

C'est un projet **personnel**, publié sous le compte GitHub **vespertine**. Le poste de travail
porte aussi un compte professionnel, qui ne doit jamais apparaître ici.

- **Commit :** `git -c user.email=1964308+vespertine@users.noreply.github.com commit …`, jamais
  avec une autre adresse.
- **Remote :** `git@github-vespertine:vespertine/fun_fish.git` (alias SSH vespertine).
- **`gh` :** toujours passer le jeton du dépôt, jamais le compte connecté par défaut :
  ```bash
  GH_TOKEN=$(cat ~/.config/fun_fish/gh_token) gh pr create -R vespertine/fun_fish …
  ```
  Ce jeton (fine-grained) donne accès à **ce dépôt seulement** : contenu, PR et workflows en
  écriture, Actions et statuts en lecture.
  - Ne **jamais** utiliser `gh auth switch` ni `gh auth login` : c'est un état global, partagé
    avec le compte professionnel.
  - En cas de doute, vérifier avec `GH_TOKEN=… gh api user --jq .login`, qui doit répondre
    `vespertine`.

## Flux de travail

- **Branches :** une branche et une PR par sujet, vers `main`. Pas de commit direct sur `main`.
- **Fusion :** la CI (tests sur Linux, macOS et Windows, non-régression du choix de coup
  comprise) doit être au vert. Ne fusionner qu'avec l'accord du propriétaire, en **squash** (un
  commit par PR sur `main`, avec le titre de la PR).
- **Versions :** des tags `vX.Y.Z`, sur `main`, après avoir reporté le numéro dans
  `pyproject.toml` et `src/fun_fish/__init__.py`. La CI crée la release GitHub.
- **Déploiement :** uniquement avec `deploy/release.sh vX.Y.Z`. Ne jamais modifier le code du
  serveur à la main. Voir [deploy/README.md](deploy/README.md#publier-une-version).

## Secrets

Les jetons sont dans `~/.config/fun_fish/`, en chmod 600. On ne les **affiche jamais** : pas de
`cat` vers la sortie, pas de copie dans un message, un log ou un commit. Pour les transmettre, on
les redirige (`ssh … 'cat > fichier' < jeton`).

| Fichier | Usage |
|---|---|
| `lichess_token` | Explorateur des maîtres de Lichess (aucun scope) |
| `lichess_bot_token` | Compte BOT FunFishBot (`bot:play`) ; présent aussi sur le serveur |
| `gh_token` | `gh` pour vespertine/fun_fish (voir plus haut) |
| `hcloud_token` | API Hetzner en **lecture seule** : état et métriques du serveur |
| `server` | Adresse SSH du serveur (`root@IP`), lue par `deploy/release.sh` |

Ne jamais demander de mot de passe de compte (Gmail, Lichess, Hetzner…) : seulement des jetons
limités, ou un connecteur.

## Le bot et le serveur

- **Un seul bot par compte Lichess :** FunFishBot tourne sur le serveur (systemd
  `fun-fish-bot`), donc ne pas lancer `fun-fish lichess-bot` en local, sauf accord explicite et
  après avoir arrêté le service.
- **Accès :** `ssh -i ~/.ssh/vespertine -o IdentitiesOnly=yes $(cat ~/.config/fun_fish/server)`. L'adresse
  n'est pas versionnée (dépôt public). Lecture des
  journaux : `journalctl -u fun-fish-bot`.
- **Parties :** les exports de debug sont dans `/home/funfish/.local/share/fun_fish/bot_games/`.

## Avant de toucher au moteur

- **Une optimisation** doit donner exactement les mêmes coups :
  `python scripts/perfbench.py compare AVANT APRÈS` doit afficher 100 %.
- **Un changement de comportement** se mesure (`perfbench.py table` : perte attendue, erreurs,
  accord) et s'explique au propriétaire. Ensuite seulement, on régénère les coups attendus
  (`perfbench.py golden`) dans la même PR.
- **Les tests :** `uv run --extra dev --extra maia pytest -q`, ou `.venv/bin/python -m pytest -q`.
  Les lancer avant chaque commit et lire le résultat : pas de « 1 failed » ignoré.

## Ressources

- **Calculs longs** (simulations, bancs, matchs contre Maia) : les lancer en arrière-plan et
  prévenir. Ils peuvent occuper toute la machine pendant des heures.
- **Attente :** ne pas surveiller une tâche en boucle, sa fin est notifiée.
- **Sobriété :** économiser les tokens, le propriétaire suit sa limite hebdomadaire. Lire des
  extraits plutôt que des fichiers entiers, et ne pas multiplier les sous-agents.
