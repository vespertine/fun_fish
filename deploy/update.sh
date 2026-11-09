#!/usr/bin/env bash
# Met le serveur sur une version (tag), en root. Lancé par deploy/release.sh (ou par setup.sh).
#   bash update.sh v0.2.0
#
# Chaque version s'installe à part (/srv/fun_fish/releases/TAG, avec son environnement Python) et y
# est testée sans toucher au bot en marche. La bascule n'a lieu que lorsque Lichess confirme
# qu'aucune partie n'est en cours ; pendant l'attente, le bot refuse les nouveaux défis (drapeau
# DRAIN). Bascule = lien `current` + unité systemd ; en cas d'échec, l'ancien lien et l'ancienne
# unité sont remis. Si les parties ne finissent pas à temps, rien ne change.
set -euo pipefail

TAG="${1:?usage : update.sh vX.Y.Z}"
REPO="${FUN_FISH_REPO:-https://github.com/vespertine/fun_fish.git}"
USER_NAME=funfish
BASE=${FUN_FISH_BASE:-/srv/fun_fish}
RELEASE="$BASE/releases/$TAG"
CURRENT="$BASE/current"
UNIT=${FUN_FISH_UNIT:-/etc/systemd/system/fun-fish-bot.service}
CONFIG=${FUN_FISH_CONFIG:-/home/$USER_NAME/.config/fun_fish}
TOKEN_FILE=$CONFIG/lichess_bot_token
DRAIN=$CONFIG/drain  # présent : le bot refuse les nouveaux défis (voir lichess_bot.DRAIN_PATH)
WAIT_GAMES=${FUN_FISH_WAIT_GAMES:-1800}  # s d'attente max de la fin des parties
KEEP=3  # versions gardées sur le disque
as_user() { runuser -u "$USER_NAME" -- env HOME="/home/$USER_NAME" "$@"; }
log() { echo "== $*"; }
fail() { echo "!! $*" >&2; exit 1; }

bot_running() { systemctl is-active -q fun-fish-bot; }

# Nombre de parties en cours, ou « ? » si Lichess ne répond pas proprement (jamais 0 par défaut).
games_in_progress() {
  local body
  [ -s "$TOKEN_FILE" ] || { echo 0; return; }
  body=$(curl -fsS --max-time 20 -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
         https://lichess.org/api/account/playing) || { echo "?"; return; }
  python3 -c "import json, sys; print(len(json.loads(sys.argv[1])['nowPlaying']))" "$body" 2>/dev/null || echo "?"
}

# Redémarre et attend la connexion à Lichess de CE lancement-ci : le journal est filtré par
# l'identifiant d'invocation systemd (une ligne d'un démarrage précédent ne compte pas).
restart_bot() {
  local invocation
  systemctl restart fun-fish-bot || return 1
  invocation=$(systemctl show -p InvocationID --value fun-fish-bot) || return 1
  [ -n "$invocation" ] || return 1
  for _ in $(seq 1 12); do
    sleep 5
    if journalctl _SYSTEMD_INVOCATION_ID="$invocation" -o cat | grep -q "connecté en tant que"; then
      return 0
    fi
  done
  return 1
}

# ---------- 1. installer et tester à part (le bot en marche n'est pas touché) ----------
mkdir -p "$BASE/releases"
chown "$USER_NAME:" "$BASE" "$BASE/releases"
# Le virtualenv est créé à son emplacement définitif : pip écrit le chemin absolu de l'interpréteur
# dans ses lanceurs (fun-fish, pip), qu'un renommage casserait. Une installation n'est complète
# qu'avec le marqueur .installed ; sinon, on recommence de zéro.
if [ ! -f "$RELEASE/.installed" ]; then
  log "Installation de $TAG dans $RELEASE"
  rm -rf "$RELEASE"
  installed() {
    as_user git clone -q --depth 1 --branch "$TAG" "$REPO" "$RELEASE" || { echo "!! Tag $TAG introuvable" >&2; return 1; }
    as_user python3 -m venv "$RELEASE/.venv" || return 1
    as_user "$RELEASE/.venv/bin/pip" install -q --upgrade pip || return 1
    as_user "$RELEASE/.venv/bin/pip" install -q "$RELEASE[maia,dev]" || return 1
    as_user "$RELEASE/.venv/bin/fun-fish" --help >/dev/null || { echo "!! Le lanceur fun-fish ne démarre pas" >&2; return 1; }
    as_user "$RELEASE/.venv/bin/fun-fish" stockfish >/dev/null || return 1
    as_user "$RELEASE/.venv/bin/python" -c "from fun_fish.sim import maia; assert maia.onnx_ready()" \
      || { echo "!! Maia indisponible (sans lui, la non-régression serait ignorée)" >&2; return 1; }
  }
  installed || { rm -rf "$RELEASE"; fail "Installation de $TAG impossible : rien n'a changé sur le serveur"; }
  touch "$RELEASE/.installed"
fi
log "Tests de $TAG sur le serveur (dont la non-régression du choix de coup)"
(cd "$RELEASE" && as_user "$RELEASE/.venv/bin/python" -m pytest -q -p no:cacheprovider) \
  || fail "Tests en échec : rien n'a changé sur le serveur"

# ---------- 2. attendre qu'aucune partie ne soit en cours ----------
if bot_running; then
  log "Vidange : plus de nouveaux défis, attente de la fin des parties (au plus $WAIT_GAMES s)"
  install -o "$USER_NAME" -g "$USER_NAME" -m 644 /dev/null "$DRAIN"
  waited=0
  while true; do
    playing=$(games_in_progress)
    [ "$playing" = "0" ] && break
    if [ "$waited" -ge "$WAIT_GAMES" ]; then
      rm -f "$DRAIN"
      fail "Parties toujours en cours ($playing) après $WAIT_GAMES s : bascule annulée, rien n'a changé"
    fi
    sleep 20
    waited=$((waited + 20))
  done
fi

# ---------- 3. basculer, ou tout remettre comme avant ----------
PREVIOUS=$(readlink "$CURRENT" || true)
cp "$UNIT" "$BASE/unit.previous" 2>/dev/null || rm -f "$BASE/unit.previous"
# Retour arrière : à partir d'ici, tout échec remet le lien, l'unité et la vidange comme avant
# (chaque étape est tolérante : on restaure le plus possible, puis on sort en erreur).
rollback() {
  echo "!! Bascule en échec : retour à ${PREVIOUS:-la version précédente}" >&2
  if [ -n "$PREVIOUS" ]; then ln -sfn "$PREVIOUS" "$CURRENT" || true; else rm -f "$CURRENT" || true; fi
  if [ -f "$BASE/unit.previous" ]; then cp "$BASE/unit.previous" "$UNIT" || true; fi
  systemctl daemon-reload || true
  rm -f "$DRAIN" || true
  restart_bot || echo "!! L'ancienne version ne redémarre pas non plus : journalctl -u fun-fish-bot" >&2
  exit 1
}
switch() {
  ln -sfn "$RELEASE" "$CURRENT" || return 1
  install -m 644 "$RELEASE/deploy/fun-fish-bot.service" "$UNIT" || return 1
  systemctl daemon-reload || return 1
  systemctl enable -q fun-fish-bot || return 1
  rm -f "$DRAIN" || return 1
  if [ -s "$TOKEN_FILE" ]; then
    restart_bot || return 1
  else
    log "Pas encore de jeton Lichess : service installé, non démarré (voir deploy/README.md)"
  fi
}
log "Bascule sur $TAG"
switch || rollback
echo "$TAG $(date -u +%FT%TZ)" > "$BASE/DEPLOYED"

# ---------- 4. ménage : garder les $KEEP dernières versions (et toujours la courante) ----------
ls -1dt "$BASE"/releases/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  [ "$(readlink -f "$old")" = "$(readlink -f "$CURRENT")" ] || rm -rf "$old"
done
log "$TAG déployé (avant : ${PREVIOUS:-première installation})"
