#!/usr/bin/env bash
# Déploie une version taguée sur le serveur du bot (depuis sa machine, avec l'accès SSH root).
#   deploy/release.sh v0.2.0            # serveur lu dans ~/.config/fun_fish/server (root@IP, non versionné)
#   FUN_FISH_SERVER=root@1.2.3.4 deploy/release.sh v0.2.0
# Le tag doit être poussé sur GitHub (et sa CI au vert). Le serveur fait le reste (deploy/update.sh
# de ce tag) : tests, attente de la fin des parties, redémarrage, retour arrière en cas d'échec.
set -euo pipefail

TAG="${1:?usage : deploy/release.sh vX.Y.Z}"
SERVER="${FUN_FISH_SERVER:-$(cat "$HOME/.config/fun_fish/server" 2>/dev/null || true)}"
[ -n "$SERVER" ] || { echo "Serveur inconnu : FUN_FISH_SERVER=root@IP, ou ~/.config/fun_fish/server"; exit 1; }
SSH_KEY="${FUN_FISH_SSH_KEY:-$HOME/.ssh/vespertine}"

git fetch -q --tags origin
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "Tag $TAG absent de GitHub (git push origin $TAG)"; exit 1; }
git show "$TAG:deploy/update.sh" | ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$SERVER" bash -s -- "$TAG"
