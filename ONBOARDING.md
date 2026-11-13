# Onboarding fun_fish

fun_fish fabrique des **joueurs d'échecs artificiels qui ont un style**. Ils sont construits sur
Stockfish mais choisissent leurs coups pour le plaisir de la partie : attaques, pièges, sacrifices.
La force n'est pas le but. L'agent public s'appelle **Fun Fish**. Sa version 2 avec la théorie
(`fun_fish_2_theorie`) joue sur Lichess sous le nom
[FunFishBot](https://lichess.org/@/FunFishBot). Il est fait pour jouer contre des humains, pas
pour rivaliser avec Stockfish.

Ce guide donne la carte du projet, la façon dont un coup est choisi, les outils pour mesurer un
changement, et les règles de travail. Le [README](README.fr.md) détaille les commandes, le
[CADRAGE](docs/CADRAGE.md) l'historique des décisions, et [deploy/README.md](deploy/README.md)
le serveur.

## En 5 minutes

```bash
uv run fun-fish                         # échiquier dans le navigateur, mode jeu
uv run fun-fish play --debug            # toutes les personnalités, leur raisonnement, l'export de debug
uv run --extra dev --extra maia pytest -q   # ~130 tests, ~20 s ; Stockfish et Maia se téléchargent seuls
```

Il faut Python 3.10 ou plus. L'extra `maia` (onnxruntime) fournit Maia-2, le modèle de jeu humain
dont dépendent Fun Fish et ses cousins. Sans lui, ils se rabattent sur un adversaire « naïf » plus
grossier. Sans `uv`, `make install`, puis `make test`, fonctionnent aussi.

## Carte du code (`src/fun_fish/`)

| Module | Rôle |
|---|---|
| `analysis.py` | Accès à Stockfish : un client UCI minimal et un cache par position et historique. Toutes les évaluations passent par `Analyser.analyse`. |
| `engines.py` | Trouve ou télécharge Stockfish 19 et le met en cache (`~/.cache/fun_fish`). |
| `sim/maia.py` | Maia-2 (ONNX) : la probabilité qu'un humain d'un Elo donné joue chaque coup, avec un cache par position et tranche d'Elo. |
| `book.py` | Livre d'ouverture humain (base Lichess). Il remplace Maia sur les 10 premiers demi-coups. |
| `opponent.py` | L'adversaire *imaginé* : Maia à `opponent_elo`, ou l'instinct naïf (`naive.py`). |
| `personalities/` | Les joueurs. La chaîne principale est `attacker` (cogneur) → `romantic` → `corsair` → `buccaneer` (flibustier) → `funfish.py` (réglages figés). |
| `position_style.py` | Le goût des positions : un classifieur (sur la représentation de Maia-2) qui reconnaît les positions des joueurs d'attaque et des joueurs solides. Utilisé par DuBot (`personalities/dubot.py`). |
| `style.py`, `fun.py` | Les goûts : développement, roi, sacrifices… (`style`) et l'« allure » des coups sans Stockfish (`fun`). |
| `theory.py` | La surcouche « théorie » : quelques coups classiques en début de partie, avec des lignes et des poids écrits à la main. |
| `pace.py` | La gestion du temps à la pendule : elle choisit le niveau d'`effort`. |
| `game.py`, `server.py`, `static/` | Une partie, l'API web (FastAPI) et l'échiquier (chessground). |
| `lichess_bot.py` | La passerelle avec l'API bot de Lichess (bibliothèque standard uniquement). |
| `sim/` | Les matchs simulés contre Maia ou Stockfish (`sim`, `sweep`, rapports). |
| `cli.py` | La commande `fun-fish` et toutes ses sous-commandes. |

## Comment Fun Fish 2 choisit un coup

Le cœur est dans `Attacker._choose_move` (`personalities/attacker.py`) :

1. **Théorie** (`theory.py`) : dans environ une partie sur quatre, il joue d'abord 1 à 4 coups
   d'une ligne écrite. Les poids sont relatifs entre les lignes encore possibles.
2. **Candidats sains** (`Soundness`, `personalities/base.py`) : jusqu'à 12 coups qui perdent au
   plus `tolerance` (3 pions) selon Stockfish à profondeur 6.
   - La **vision** (Maia à 1800) écarte les coups calmes trop « machine », mais jamais les coups
     forcés.
   - Quand la position est gagnante, il passe progressivement en **conversion**.
3. **Coups en plus** : le flibustier ajoute ses coups « fun » et « créatifs », même s'ils sont mal
   classés par Stockfish, dans la limite de `fun_budget`.
4. **Style** : un bonus par coup, que le dialogue avec l'attaque peut tempérer.
5. **Arbre d'attaque**, en deux passes.
   - D'abord, chaque candidat est simulé contre les réponses *probables* de l'adversaire imaginé
     (Maia à 600 Elo) : les 3 plus probables, plus la meilleure de Stockfish pour le reste de la
     probabilité. On joue ensuite notre meilleure réplique.
   - Puis les 5 meilleurs, selon le score de l'agent, sont **approfondis** sur 3 coups adverses.
   - Les motifs mesurés sont le mat, le gain, le piège et l'étroitesse. Le plus fort mène, moins
     le prix du coup face à une défense parfaite.
6. **Tirage** softmax à la température 0,3, puis le **garde-fou** (`verified`). Le coup choisi
   est revérifié seul : si son évaluation était fausse, il est remplacé et l'anomalie est notée.

Le coût est **exponentiel en horizon** (`attack_moves` : ×2,6 par coup simulé) et presque plat en
profondeur de Stockfish. Maia représente environ 70 % du temps. Le réglage `effort` (0 à 5)
raccourcit l'arbre par paliers (`Attacker.EFFORT`), sans jamais toucher au filtre de sécurité ni
au nombre de candidats. C'est `pace.py` qui le choisit, d'après la pendule.

**Les décisions sont très sensibles au bruit de Stockfish.** Changer seulement la taille de sa
table de hachage modifie un coup sur trois, sans changer la qualité moyenne. Pour juger un
changement, il faut donc comparer des **mesures** sur beaucoup de positions, pas des coups isolés.

## Mesurer un changement

| Outil | Quand |
|---|---|
| `pytest` | Toujours. `tests/test_regression.py` rejoue 12 positions réelles avec des graines fixes : **s'il échoue, le comportement a changé**. |
| `python scripts/perfbench.py run LABEL [-s param=val]` | Pour tout changement qui touche au choix de coup ou à sa vitesse. Il rejoue 292 positions réelles en parallèle, en environ une minute sur 14 cœurs. |
| `python scripts/perfbench.py table BASE A B…` | Compare des runs : temps, appels Stockfish et Maia, perte attendue (Stockfish profondeur 14), erreurs ≥ 200 cp, accord avec la référence. |
| `python scripts/perfbench.py compare A B` | Vérifie qu'une optimisation donne **exactement** les mêmes coups : il faut 100 %. |
| `python scripts/perfbench.py golden` | Régénère les coups attendus du test de non-régression, après un changement **voulu** et mesuré. |
| `python scripts/clocksim.py 180 0` | Fait jouer des parties à la pendule, pour vérifier qu'il ne perd pas au temps (à lancer aussi sur le serveur). |
| `fun-fish sim` / `sweep` | Pour la force et le style à grande échelle, contre Maia ou Stockfish (voir le README). |
| `fun-fish replay EXPORT --ply N` | Pour rejouer la réflexion sur un coup suspect, à partir d'un export de debug (bouton du mode debug, ou partie du bot). |

`runs/` n'est pas versionné. Il contient les résultats de mesure, dont `runs/perf/positions.json`
et `truth.json`, que `perfbench.py positions` et `truth` reconstruisent.

## Le bot Lichess et le serveur

- **Les comptes :** quatre comptes BOT, un service chacun : [FunFishBot](https://lichess.org/@/FunFishBot)
  (Fun Fish 2 + théorie), [CrazyFishBot](https://lichess.org/@/CrazyFishBot) (`crazy_fish`),
  [DuBot](https://lichess.org/@/DuBot) (`dubot`, goût des positions d'attaque) et
  [CapaBot](https://lichess.org/@/CapaBot) (`capabot`, goût des positions solides).
- **Les défis acceptés :** à partir de 2+1 et jusqu'à environ 15 minutes estimées (`decline_reason`).
  Les autres bots sont rationnés, pour laisser la place aux humains (voir `BOT_PAUSE`) : après une
  partie contre un bot, une pause de trois fois sa durée ; après une partie contre un humain, deux
  minutes pour qu'il enchaîne ; le même bot au plus toutes les deux heures ; jusqu'à 10+0 ; et jamais
  deux parties contre des bots en même temps, tous nos comptes réunis (marqueurs dans `BOT_GAMES_DIR`).
  Le journal note la cadence de chaque défi, accepté ou refusé.
- **La recherche d'adversaires (`--matchmaking`, activée dans les quatre services) :** un compte libre
  depuis 10 minutes défie un bot en ligne de niveau proche (±250), dans la cadence où son classement
  est le plus incertain, pour le stabiliser face à des adversaires variés. Mêmes règles que les défis
  reçus (place commune, pauses, même bot espacé), plus 5 minutes entre deux défis lancés par nos
  comptes réunis ; jamais l'un de nos bots, ni un bot qui a refusé depuis moins d'un jour. Un humain
  qui arrive passe devant : notre défi en attente est annulé (`seek`, `MATCH_*`).
- **Contre les bots, en amical seulement (`BOT_RATED`) :** nos défis aux bots ne sont pas classés, et
  un défi classé d'un bot est refusé (« casual »). L'échelle des bots sur Lichess est sans rapport avec
  celle des humains (des moteurs classés 1850 jouent à 96 % de précision) : notre classement ne doit
  venir que des parties contre des humains.
- **Le serveur :** il tourne sur un serveur Hetzner (ARM) en service systemd, installé par
  `deploy/setup.sh`. Voir [deploy/README.md](deploy/README.md).
- **Les données :** chaque partie laisse un export de debug compressé dans
  `~/.local/share/fun_fish/bot_games/`, avec les anomalies du garde-fou.
- **Un seul bot par compte :** ne jamais lancer `fun-fish lichess-bot` sur sa machine pendant que
  le service tourne.

## Règles de travail

- **Langue :** le code, les commentaires et les messages sont **en français**.
- **Commits :** sous l'identité `vespertine`
  (`git -c user.email=1964308+vespertine@users.noreply.github.com commit …`), jamais avec une
  adresse professionnelle.
- **Secrets :** les jetons sont dans `~/.config/fun_fish/` (chmod 600) et ne s'affichent jamais :
  - `lichess_token` : explorateur des maîtres ;
  - `lichess_bot_token` : le bot ;
  - `gh_token` : `gh`, limité à ce dépôt, s'utilise avec `GH_TOKEN=$(cat …) gh …` ;
  - `hcloud_token` : Hetzner, en lecture seule.
- **Avant de toucher au moteur :** lancer perfbench avant et après. Une optimisation doit donner
  100 % des mêmes coups. Un changement de comportement se mesure (perte, erreurs, style) avant de
  régénérer le golden.
- **Calculs longs :** les lancer en arrière-plan et prévenir. Les simulations peuvent occuper
  toute une machine pendant des heures.
- **Flux de travail :**
  - une branche et une PR par sujet ; la CI (tests sur Linux, macOS et Windows, non-régression
    comprise) doit être au vert avant la fusion ;
  - les versions sont des tags `vX.Y.Z` ; la CI en fait une release GitHub ;
  - `deploy/release.sh vX.Y.Z` déploie sur le serveur, avec les tests sur place et un retour
    arrière automatique en cas d'échec. Voir [deploy/README.md](deploy/README.md#publier-une-version).
