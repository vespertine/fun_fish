#!/usr/bin/env bash
# Met le serveur sur une version taguée, en root : tests sur le serveur, bot redémarré entre deux
# parties, retour à la version précédente si quoi que ce soit échoue. Lancé par deploy/release.sh.
#   bash update.sh v0.2.0
set -euo pipefail

TAG="${1:?usage : update.sh vX.Y.Z}"
USER_NAME=funfish
APP=/opt/fun_fish
TOKEN_FILE=/home/$USER_NAME/.config/fun_fish/lichess_bot_token
WAIT_GAMES=${FUN_FISH_WAIT_GAMES:-1800}  # s d'attente max qu'aucune partie ne soit en cours
as_user() { runuser -u "$USER_NAME" -- env HOME="/home/$USER_NAME" "$@"; }
git_app() { as_user git -C "$APP" "$@"; }

install_version() {
  git_app checkout -q --detach "$1"
  as_user "$APP/.venv/bin/pip" install -q "$APP[maia,dev]"
}

restart_bot() {
  local since
  since=$(date '+%Y-%m-%d %H:%M:%S')
  systemctl restart fun-fish-bot
  for _ in $(seq 1 12); do
    sleep 5
    if journalctl -u fun-fish-bot --since "$since" -o cat | grep -q "connecté en tant que"; then
      return 0
    fi
  done
  return 1
}

games_in_progress() {
  [ -s "$TOKEN_FILE" ] || { echo 0; return; }
  curl -fsS -H "Authorization: Bearer $(cat "$TOKEN_FILE")" https://lichess.org/api/account/playing \
    | python3 -c "import json, sys; print(len(json.load(sys.stdin)['nowPlaying']))" 2>/dev/null || echo 0
}

PREVIOUS=$(git_app rev-parse HEAD)
PREVIOUS_NAME=$(git_app describe --tags --always HEAD)
echo "== Version actuelle : $PREVIOUS_NAME ; cible : $TAG"
git_app fetch -q --tags origin
git_app rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "Tag $TAG introuvable"; exit 1; }

rollback() {
  echo "!! Échec : retour à $PREVIOUS_NAME"
  install_version "$PREVIOUS"
  restart_bot || echo "!! Le bot ne redémarre pas non plus : journalctl -u fun-fish-bot"
  exit 1
}

echo "== Installation de $TAG"
install_version "$TAG" || rollback
install -m 644 "$APP/deploy/fun-fish-bot.service" /etc/systemd/system/fun-fish-bot.service
systemctl daemon-reload

echo "== Tests sur le serveur (dont la non-régression du choix de coup)"
(cd "$APP" && as_user "$APP/.venv/bin/python" -m pytest -q -p no:cacheprovider) || rollback

echo "== Attente de la fin des parties en cours (au plus $WAIT_GAMES s)"
waited=0
while [ "$(games_in_progress)" != "0" ] && [ "$waited" -lt "$WAIT_GAMES" ]; do
  sleep 20
  waited=$((waited + 20))
done

echo "== Redémarrage du bot"
restart_bot || rollback
echo "$TAG $(date -u +%FT%TZ)" > "$APP/DEPLOYED"
echo "== $TAG déployé (avant : $PREVIOUS_NAME)."
