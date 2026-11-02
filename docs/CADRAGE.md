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
| Coups « humains » | [Maia-2](https://github.com/CSSLab/maia2) (PyTorch, sans lc0) | MIT | Prédit le coup qu'un humain d'un Elo donné jouerait (tranches de 100 de 1100 à 2000) : adversaire simulé pour les matchs, et modèle d'adversaire de l'arnaqueur (`opponent_elo`) |

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
- [x] Maia comme modèle d'adversaire de l'arnaqueur (`opponent_elo`), avec élagage des réponses improbables : mats plus rapides contre Maia (médiane 20 coups contre 27 à 1100-1500, 26 contre 36 à 1900, sur 12 parties par tranche)
- [ ] Accélérer : Maia en lot, profondeur du filtre de santé
- [ ] En live, approximer Maia sans PyTorch (quitte à regrouper les Elo en quelques
      niveaux) ; comparer plusieurs approches
- [x] Modèle d'adversaire commun (`opponent.py` : instinct ou Maia, Maia en lot)
- [x] Personnalité agressive, le cogneur (`attacker`) : probabilité de mater ou de gagner
      ≥ 3 pions en 3 coups contre les réponses probables de Maia. Sur 12 parties par
      tranche : mat médian en 20 coups à 1100, 16 à 1500, 34 à 1900 (l'arnaqueur : 20, 20, 26).
      Sur 200 parties par tranche, l'écart se resserre : cogneur 20/22/25, arnaqueur 21/22/23 ;
      98-99 % de score pour les deux, le cogneur gagne plus de matériel (+15 au coup 40 contre +8).
      Toutes les nulles (2-3 %) étaient des répétitions en position gagnante, corrigées depuis
- [x] Chaîne d'étages : un premier étage commun (`Soundness`, filtre de santé) puis le choix
      de la personnalité. **Conversion progressive** (pas de bascule vers un niveau Stockfish) :
      de +3 à +8, il vérifie jusqu'à 4 demi-coups plus loin, sa tolérance descend jusqu'à
      0,6 pion, et une technique de conversion (simplifier, pousser ses pions passés, freiner
      ceux de l'adversaire, activer son roi) entre dans le style. Sur une partie réelle où le
      cogneur (profondeur 4) tournait en rond à +6, depuis le coup 21 contre Stockfish
      profondeur 16 : 6 mats sur 6 (dont un en 61 coups, après être retombé sous +5), contre
      2 mats, 1 pat, 1 répétition, 1 partie perdue et 1 inachevée sans conversion ; 6 sur 6
      contre Maia 1900. Coût : ~1,2 s par coup au lieu de ~1 s
- [x] Fin de partie stricte (`rules.py`) : `outcome(claim_draw=True)` de python-chess
      déclarait nulle une position où le camp au trait *pouvait* répéter. Les nulles « par
      répétition en position gagnante » des séries de 200 parties en venaient en partie
- [x] Étage de style (`style.py`, presque gratuit, calculé avec python-chess) : développement,
      lignes ouvertes, pression sur le roi, activité, initiative plutôt que matériel (Morphy),
      goût du sacrifice à position égale ; `style_first` place le style avant le calcul.
      Profil **Le romantique** (`romantic`) = cogneur + style. Contre Maia 1500 (24 parties) :
      même score (100 %, mat médian 20 coups contre 19) et même coût, mais pièces développées au
      coup 6 : 3,2 contre 1,6 ; roque avant le coup 12 : 62 % contre 17 % ; coups de pion dans
      les 8 premiers : 2,9 contre 4,0 ; matériel au coup 10 : +0,4 contre +2,5 à éval égale
- [x] Corrections après une partie du romantique contre un bot à 2750 (position gagnante
      perdue) : l'initiative ne pénalise plus les prises gratuites (il refusait de reprendre
      un pion), les goûts de milieu de partie (lignes, roi, activité) s'effacent quand les
      pièces disparaissent, et la vérification plus profonde s'applique toujours en finale
      (il laissait filer un pion à dame à profondeur 5)
- [x] Maia-2 n'a pas été entraîné sur les 10 premiers demi-coups (`first_n_moves: 10`) : en
      ouverture il jouait n'importe quoi (1.Cc3 Cf6 2.Cb1 à 83 %). Livre d'ouverture humain
      (`book.py`, 1,7 Mo, 32 575 positions) tiré de 2 millions de parties Lichess de septembre
      2025 (CC0), par tranche de 200 Elo, utilisé avant Maia. Contre Maia 1500 avec le livre :
      toujours 100 %, mais mat médian en 34 coups pour le cogneur (19 avant) et 26 pour le
      romantique (22) ; matériel au coup 10 du cogneur : −0,2 (+2,5 avant). Les anciens
      résultats étaient flattés par les ouvertures absurdes de Maia
- [ ] Hors du livre avant le 10e demi-coup (la moitié des cas aux demi-coups 6 à 9, nos agents
      sortant vite des sentiers battus) : remplacer Maia par autre chose (Stockfish peu
      profond avec un tirage au sort dépendant de l'Elo ?)
- [ ] Curseur « dark forest » (`w_surprise`, branché, pas encore évalué), surtout en ouverture : sortir l'adversaire de la théorie (coups
      peu probables pour Maia 2000). À manier avec prudence : comparer coups très humains et
      coups très surprenants, et ce que ça change au ressenti
- [ ] Ordre des étages (style puis cogneur, ou l'inverse) ; plus tard, vote entre personnalités
      (produit pondéré des probabilités, Borda...) si la chaîne ne suffit pas
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
