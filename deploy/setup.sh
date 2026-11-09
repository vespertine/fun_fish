#!/usr/bin/env bash
# Installe le bot Lichess de fun_fish sur un serveur Ubuntu/Debian neuf (x86-64 ou ARM64).
# À lancer en root :  curl -fsSL https://raw.githubusercontent.com/vespertine/fun_fish/main/deploy/setup.sh | sudo bash
# Relançable sans risque : met à jour le code et redémarre le service s'il tourne déjà.
set -euo pipefail

REPO="${FUN_FISH_REPO:-https://github.com/vespertine/fun_fish.git}"
BRANCH="${FUN_FISH_BRANCH:-main}"
USER_NAME=funfish
APP=/opt/fun_fish
TOKEN_FILE=/home/$USER_NAME/.config/fun_fish/lichess_bot_token

echo "== Paquets système"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv ca-certificates

echo "== Utilisateur $USER_NAME (sans droits d'administration)"
id "$USER_NAME" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$USER_NAME"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 700 "/home/$USER_NAME/.config/fun_fish"

echo "== Code ($REPO, branche $BRANCH)"
if [ -d "$APP/.git" ]; then
  sudo -u "$USER_NAME" git -C "$APP" fetch -q origin "$BRANCH"
  sudo -u "$USER_NAME" git -C "$APP" reset -q --hard "origin/$BRANCH"
else
  install -d -o "$USER_NAME" -g "$USER_NAME" "$APP"
  sudo -u "$USER_NAME" git clone -q --branch "$BRANCH" "$REPO" "$APP"
fi

echo "== Environnement Python"
sudo -u "$USER_NAME" python3 -m venv "$APP/.venv"
sudo -u "$USER_NAME" "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u "$USER_NAME" "$APP/.venv/bin/pip" install -q "$APP[maia]"

echo "== Stockfish et Maia (téléchargés une fois)"
sudo -u "$USER_NAME" -H "$APP/.venv/bin/fun-fish" stockfish
sudo -u "$USER_NAME" -H "$APP/.venv/bin/python" -c "from fun_fish.sim import maia; assert maia.onnx_ready(), 'Maia indisponible'"

if [ ! -d /run/systemd/system ]; then
  echo "== Pas de systemd ici (conteneur ?) : installation terminée sans service."
  exit 0
fi

echo "== Service systemd"
install -m 644 "$APP/deploy/fun-fish-bot.service" /etc/systemd/system/fun-fish-bot.service
systemctl daemon-reload
systemctl enable -q fun-fish-bot

if [ -s "$TOKEN_FILE" ]; then
  systemctl restart fun-fish-bot
  echo "== Bot (re)démarré. Journal : journalctl -u fun-fish-bot -f"
else
  echo
  echo "Il reste à poser le jeton Lichess du bot (il ne s'affichera pas) :"
  echo "  sudo install -o $USER_NAME -g $USER_NAME -m 600 /dev/null $TOKEN_FILE"
  echo "  sudo nano $TOKEN_FILE"
  echo "puis démarrer :  sudo systemctl start fun-fish-bot"
fi
