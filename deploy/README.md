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

Le script installe Python, le code dans `/opt/fun_fish`, Stockfish et Maia, et un service
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
| mettre à jour le code | relancer la commande `curl … setup.sh \| sudo bash` |
| rapatrier les parties (exports de debug) | `rsync -av --rsync-path="sudo rsync" ubuntu@ADRESSE:/home/funfish/.local/share/fun_fish/bot_games/ bot_games/` |

Un seul bot doit tourner par compte Lichess : arrêter celui de votre machine avant de démarrer
celui du serveur.
