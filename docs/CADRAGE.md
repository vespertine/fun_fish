# fun_fish — Cadrage

## 1. Objectif

Un programme facile à lancer sous Ubuntu, macOS et Windows, qui ouvre un échiquier
agréable et permet de jouer contre des **personnalités** : des joueurs artificiels
qui choisissent leurs coups à partir des évaluations d'un moteur fort (Stockfish),
mais selon des critères qui ne se limitent pas à « le meilleur coup ».

On ne réécrit ni l'échiquier, ni les règles, ni le moteur. Tout l'intérêt du projet
est dans la **couche de sélection des coups**.

Exemple de personnalité : le *hustler*, qui préfère les lignes « sharp », même si elles
sont un peu moins bonnes, quand l'adversaire n'y dispose que de peu de coups qui ne
perdent pas.

## 2. Architecture visée

```
┌───────────────────────┐      ┌───────────────────────────────┐      ┌─────────────┐
│ Interface (navigateur) │◄────►│ Serveur local Python          │◄────►│ Stockfish   │
│ échiquier + panneau    │ HTTP │  - état de la partie          │ UCI  │ (binaire    │
│ « pourquoi ce coup ? » │  WS  │  - Personality.choose_move()  │      │  officiel)  │
└───────────────────────┘      └───────────────────────────────┘      └─────────────┘
                                         ▲
                                         │ même code
                       ┌─────────────────┴──────────────────┐
                       │ Adaptateur UCI « fun_fish-engine »  │ → En Croissant, Cute Chess,
                       │ (une personnalité = un moteur UCI)  │   lichess-bot, cutechess-cli…
                       └────────────────────────────────────┘
```

Le cœur est une bibliothèque Python qui ne dépend d'aucune interface :

```python
class Personality(Protocol):
    name: str
    def choose_move(self, board: chess.Board, analyser: Analyser) -> Decision: ...
# Decision = coup choisi + candidats évalués + explication lisible
```

Deux façons de l'utiliser :

1. **Notre interface web locale** : `fun-fish` lance un petit serveur et ouvre le
   navigateur. Il n'y a pas de toolkit graphique natif à maintenir sur trois OS, et
   l'affichage est le même partout. On peut ajouter un panneau qui montre les
   candidats et la raison du choix, ce qui sera très utile pour régler les
   personnalités.
2. **Moteur UCI** : chaque personnalité est aussi exposée comme un moteur UCI. Elle
   fonctionne donc dans n'importe quelle interface d'échecs existante, peut affronter
   d'autres moteurs dans des tournois automatisés, et pourra même jouer sur Lichess
   via lichess-bot.

## 3. Briques open source réutilisées

| Rôle | Projet | Licence | Pourquoi |
|---|---|---|---|
| Moteur | [Stockfish](https://github.com/official-stockfish/Stockfish) | GPL-3.0 | Le meilleur moteur ; binaires officiels pour Linux, macOS et Windows ; MultiPV, profondeur et WDL disponibles en UCI |
| Règles, UCI, PGN | [python-chess](https://github.com/niklasf/python-chess) | GPL-3.0 | Référence en Python : `chess.engine` gère le protocole UCI, le MultiPV et les infos WDL |
| Échiquier web | [chessground](https://github.com/lichess-org/chessground) | GPL-3.0 | L'échiquier de Lichess : glisser-déposer, flèches, animations, bon rendu |
| Règles côté client | [chessops](https://github.com/niklasf/chessops) ou [chess.js](https://github.com/jhlywa/chess.js) | GPL-3.0 / BSD-2 | Coups légaux et surlignage des destinations dans le navigateur |
| Pièces | Sets de Lichess (cburnett, merida…) | GPL / CC selon le set | Livrés avec chessground |
| Serveur | FastAPI + uvicorn | MIT / BSD | Léger, WebSocket natif |
| Packaging | [uv](https://github.com/astral-sh/uv), puis PyInstaller | Apache/MIT, GPL avec exception | `uvx`/`uv run` pour les développeurs, exécutables par OS via GitHub Actions |
| Tournois et tests | [cutechess-cli](https://github.com/cutechess/cutechess) / python-chess | GPL-3.0 | Matchs entre personnalités, calcul d'Elo |
| Bot en ligne *(plus tard)* | [lichess-bot](https://github.com/lichess-bot-devs/lichess-bot) | AGPL-3.0 | Faire jouer une personnalité sur Lichess |
| Coups « humains » | [Maia-2](https://github.com/CSSLab/maia2) (PyTorch, sans lc0) | MIT | Prédit le coup qu'un humain d'un Elo donné jouerait (tranches de 100 de 1100 à 2000) : adversaire simulé pour les matchs, et bientôt modèle d'adversaire des personnalités |

**Conséquence sur la licence** : Stockfish, python-chess et chessground sont sous
GPL-3.0. Le projet sera donc publié sous **GPL-3.0**. Ce n'est pas une contrainte pour
un projet perso open source. On redistribue les binaires Stockfish en citant la licence
et un lien vers les sources.

**Fourniture de Stockfish** : au premier lancement, on télécharge depuis les releases
GitHub officielles le binaire adapté à l'OS et à l'architecture, puis on le met en
cache dans le dossier utilisateur. Si un `stockfish` est déjà présent dans le `PATH`,
on l'utilise. Les exécutables PyInstaller pourront l'embarquer directement.

### Alternatives écartées (pour l'instant)

- **Tout dans le navigateur (Stockfish WASM, page GitHub Pages)** : aucune
  installation, ce qui est séduisant. Mais les personnalités demandent beaucoup
  d'analyses (MultiPV sur les réponses adverses), le WASM est plus lent et
  multi-thread seulement avec des en-têtes COOP/COEP, et l'expérimentation est bien
  plus confortable en Python. On pourra porter plus tard une fois les personnalités
  stabilisées.
- **Interface native (Qt, Tkinter, pygame)** : plus de travail pour un résultat moins
  joli et un packaging plus pénible sur trois OS.
- **Tout déléguer à une interface existante (En Croissant, Arena)** : possible grâce à
  l'adaptateur UCI, mais on perd le panneau « pourquoi ce coup » et la maîtrise de
  l'expérience. On garde donc les deux.

## 4. Principe des personnalités

Chaque personnalité est un **filtre et une fonction de score sur les candidats de
Stockfish**, pas un moteur à part.

1. **Analyse** : Stockfish en MultiPV (par exemple 8 à 12 lignes) à profondeur ou
   temps fixe. On obtient pour chaque candidat l'éval en centipions, le WDL et la PV.
2. **Garde-fou** : on écarte les coups qui perdent plus de *X* cp, ou qui font passer
   le WDL sous un seuil, par rapport au meilleur coup. Ce *X* fixe le niveau.
3. **Analyse spécifique** à la personnalité, par exemple pour le *hustler* : pour
   chaque candidat retenu, on analyse la position adverse en MultiPV et on mesure
   - la **largeur du chemin** : le nombre de réponses qui gardent l'adversaire à
     moins de *Y* cp de sa meilleure réponse ;
   - la **profondeur du trou** : ce que coûtent les réponses naturelles mais
     mauvaises ;
   - plus tard, la **probabilité humaine** de tomber dans le piège, via Maia.
4. **Score** = éval − λ·(perte vs meilleur coup) + μ·(critère de la personnalité),
   puis tirage softmax avec une température, pour garder un peu de variété.
5. **Explication** : chaque décision renvoie les candidats, leurs scores et une
   phrase lisible, affichées dans le panneau de l'interface.

Le coût est le point à surveiller : l'étape 3 multiplie les analyses. On jouera sur
la profondeur, le nombre de candidats, un cache par FEN, et une analyse à faible
profondeur pour le second niveau.

Idées de personnalités à explorer ensuite : *hustler* (pièges et positions
étroites), *bourrin* (toujours vers le roi adverse), *matérialiste* (prend tout ce qui
traîne), *pacifiste* (simplifie et échange), *Tal du dimanche* (sacrifie dès que c'est
défendable), *hypermoderne têtu*, *rancunier* (change de style quand il est mené)…

## 5. Roadmap

### Phase 0 — Squelette (ce cadrage)
- [x] Cadrage, choix des briques, licence
- [x] Dépôt git local
- [x] Remote `git@github-vespertine:vespertine/fun_fish.git`, commits signés
      `1964308+vespertine@users.noreply.github.com`

### Phase 1 — Proof of concept de bout en bout
- [x] Projet Python (`uv`, `pyproject.toml`), package `fun_fish`
- [x] Gestion de Stockfish : détection dans le `PATH`, sinon téléchargement et mise
      en cache selon l'OS
- [x] Wrapper `Analyser` sur `chess.engine` (MultiPV, cache par FEN)
- [x] Interface `Personality` et personnalité **dummy** : Stockfish à faible
      profondeur, ou `Skill Level`
- [x] Serveur FastAPI et page web avec chessground : jouer Blancs ou Noirs, nouvelle
      partie, choix de la personnalité, export PGN
- [x] Commande `fun-fish` qui lance le serveur et ouvre le navigateur
- [x] Vérification sous Linux
- [x] CI GitHub Actions sur les trois OS

### Phase 2 — Banc d'essai des personnalités
- [ ] Adaptateur UCI : `fun-fish-engine --personality hustler`
- [x] Panneau « pourquoi ce coup ? » (candidats, scores, flèches), livré dès la phase 1
- [ ] Positions de test (FEN et lignes concrètes) avec rapport du coup choisi par
      chaque personnalité
- [x] Matchs automatiques contre Maia-2 par tranche d'Elo (`fun-fish sim`), avec
      estimation d'Elo et statistiques de style (taux de sacrifices, longueur des
      parties, nombre de pièges tendus ou réussis…)

### Phase 3 — Personnalités intéressantes
- [ ] *Hustler* v1 : largeur du chemin adverse
- [ ] 2 ou 3 autres personnalités contrastées
- [ ] Réglage itératif sur des lignes concrètes
- [x] Maia-2 intégré comme adversaire simulé (dépendance optionnelle `sim`)
- [ ] Maia comme modèle d'adversaire de l'arnaqueur (à la place ou en plus de l'instinct)
- [ ] Personnalité agressive : mats et gros gains matériels rapides contre une tranche d'Elo
- [ ] Un seul curseur de niveau pour l'utilisateur (Elo visé), validé par simulations

### Phase 4 — Distribution
- [ ] Exécutables PyInstaller Linux, macOS et Windows via GitHub Actions (releases)
- [ ] README avec installation en une ligne (`uvx fun-fish`)
- [ ] *(option)* lichess-bot pour jouer contre les personnalités sur Lichess
- [ ] *(option)* Portage navigateur (Stockfish WASM)

## 6. Décisions

- Échiquier : **chessground**. Son bundle JS pré-construit est committé dans le
  dépôt, pour que l'utilisateur final n'ait pas besoin de Node.
- Identité git : email `noreply` GitHub du compte vespertine.

## 7. Questions ouvertes

- Contrôle du temps : la personnalité joue-t-elle à vitesse constante, ou simule-t-on
  un temps de réflexion « humain » ?
