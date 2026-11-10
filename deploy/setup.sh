#!/usr/bin/env bash
# Première installation du bot Lichess de fun_fish sur un serveur Ubuntu/Debian neuf (x86-64 ou ARM64).
# À lancer en root :  curl -fsSL https://raw.githubusercontent.com/vespertine/fun_fish/main/deploy/setup.sh | sudo bash
# Prépare le système puis installe la dernière version taguée avec deploy/update.sh (testée sur
# place). Ensuite, les mises à jour passent par deploy/release.sh (voir deploy/README.md).
set -euo pipefail

REPO="${FUN_FISH_REPO:-https://github.com/vespertine/fun_fish.git}"
RAW="${FUN_FISH_RAW:-https://raw.githubusercontent.com/vespertine/fun_fish}"
USER_NAME=funfish
TOKEN_FILE=/home/$USER_NAME/.config/fun_fish/lichess_bot_token

echo "== Paquets système"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv ca-certificates curl

echo "== Utilisateur $USER_NAME (sans droits d'administration)"
id "$USER_NAME" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$USER_NAME"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 700 "/home/$USER_NAME/.config/fun_fish"

TAG="${FUN_FISH_TAG:-$(git ls-remote --tags --refs "$REPO" 'v*' | sed 's#.*refs/tags/##' | sort -V | tail -1)}"
[ -n "$TAG" ] || { echo "Aucune version taguée sur $REPO"; exit 1; }
echo "== Version $TAG"

if [ ! -d /run/systemd/system ]; then
  echo "Pas de systemd ici (conteneur ?) : le bot a besoin d'un service systemd."
  exit 1
fi
curl -fsSL "$RAW/$TAG/deploy/update.sh" | bash -s -- "$TAG"

if [ ! -s "$TOKEN_FILE" ]; then
  echo
  echo "Il reste à poser le jeton Lichess du bot (il ne s'affichera pas) :"
  echo "  sudo install -o $USER_NAME -g $USER_NAME -m 600 /dev/null $TOKEN_FILE"
  echo "  sudo nano $TOKEN_FILE"
  echo "puis activer et démarrer :  sudo systemctl enable --now fun-fish-bot"
fi
