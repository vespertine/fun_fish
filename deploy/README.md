# Déployer le bot Lichess sur un serveur

Un petit serveur Ubuntu (22.04 ou 24.04, x86-64 ou ARM64) suffit : 2 vCPU et 2 à 4 Go de mémoire
pour 2 parties simultanées.

## 1. Le serveur

**Oracle Cloud « Always Free »** (0 €) : créer une instance *Ampere A1* (ARM), image Ubuntu,
2 OCPU / 12 Go, avec votre clé SSH publique. Si la création échoue faute de capacité, réessayer
plus tard ou dans un autre domaine de disponibilité.

**Hetzner** (environ 6 €/mois) : un serveur CAX11 (ARM) ou CX23 (x86), image Ubuntu, avec votre
clé SSH publique.

Aucun port à ouvrir : le bot se connecte lui-même à Lichess.

## 2. L'installation

```sh
ssh ubuntu@ADRESSE        # « root@ADRESSE » chez Hetzner
curl -fsSL https://raw.githubusercontent.com/vespertine/fun_fish/main/deploy/setup.sh | sudo bash
```

Le script installe Python et la dernière version taguée dans `/srv/fun_fish/releases/vX.Y.Z`,
avec son environnement Python, Stockfish et Maia, après l'avoir testée sur place. Il installe
aussi un service
`fun-fish-bot` qui tourne sous un utilisateur dédié sans droits (`funfish`).

## 3. Le jeton du bot

```sh
sudo install -o funfish -g funfish -m 600 /dev/null /home/funfish/.config/fun_fish/lichess_bot_token
sudo nano /home/funfish/.config/fun_fish/lichess_bot_token     # coller le jeton, Ctrl+O, Ctrl+X
sudo systemctl start fun-fish-bot
```

## Au quotidien

| Pour… | Commande |
| --- | --- |
| suivre le bot | `journalctl -u fun-fish-bot -f` |
| l'arrêter / le relancer | `sudo systemctl stop fun-fish-bot` / `sudo systemctl restart fun-fish-bot` |
| changer ses réglages | `echo 'FUN_FISH_BOT_ARGS="--max-games 2 -s luxury_cap=200"' \| sudo tee /etc/default/fun-fish-bot` puis relancer |
| mettre à jour le code | une version taguée : `deploy/release.sh vX.Y.Z` (voir plus bas) |
| rapatrier les parties (exports de debug) | `rsync -av --rsync-path="sudo rsync" ubuntu@ADRESSE:/home/funfish/.local/share/fun_fish/bot_games/ bot_games/` |

Un seul bot doit tourner par compte Lichess : arrêter celui de votre machine avant de démarrer
celui du serveur.

## Publier une version

Le travail se fait sur une branche, puis une PR vers `main`. La CI (tests sur Linux, macOS et
Windows, non-régression du choix de coup comprise) doit être au vert avant la fusion. Pour
publier :

1. **Choisir le numéro :** `vX.Y.Z`. Z pour une correction, Y pour un comportement ou une
   fonctionnalité, X pour une rupture. Le reporter dans `pyproject.toml` et
   `src/fun_fish/__init__.py`, puis passer par une PR.
2. **Taguer `main` et pousser le tag :** `git tag vX.Y.Z && git push origin vX.Y.Z`. La CI
   rejoue les tests et crée la release GitHub avec ses notes, générées à partir des PR.
3. **Déployer :** `deploy/release.sh vX.Y.Z`. L'adresse du serveur est lue dans
   `~/.config/fun_fish/server` (`root@IP`), qui n'est pas versionné puisque le dépôt est public.
   Sur le serveur, `deploy/update.sh` de ce tag :
   - installe la version **à part**, dans `/srv/fun_fish/releases/vX.Y.Z` avec son environnement
     Python, et lance **tous les tests** ; s'ils échouent, rien ne change ;
   - passe le bot en **vidange** (drapeau `~funfish/.config/fun_fish/drain`) : il refuse les
     nouveaux défis et finit ses parties ;
   - **ne bascule que lorsque Lichess confirme qu'aucune partie n'est en cours**. Si Lichess ne
     répond pas, il continue d'attendre. Au-delà de 30 minutes, il annule la bascule sans rien
     changer ;
   - bascule le lien `/srv/fun_fish/current` et l'unité systemd, redémarre le bot et vérifie
     qu'il se reconnecte. Sinon, il remet l'ancien lien et l'ancienne unité.

   La version en place est inscrite dans `/srv/fun_fish/DEPLOYED`, et les 3 dernières versions
   restent sur le disque. `tests/test_deploy.py` vérifie ces scénarios avec des doubles de git,
   pip, curl et systemd.

`setup.sh` ne sert qu'à la première installation, avec la dernière version taguée.
L'ancienne installation (`/opt/fun_fish`, avant la v0.2.0) peut être supprimée une fois la
première release déployée.
