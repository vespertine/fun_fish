# fun_fish

Jouer aux échecs contre des **personnalités** : des joueurs artificiels construits sur
les évaluations de Stockfish, mais qui choisissent leurs coups selon un style (sharp,
piégeux, matérialiste…) plutôt que selon la seule force.

Pour contribuer, commencer par [ONBOARDING.md](ONBOARDING.md) : la carte du code, la façon
dont un coup est choisi, les outils de mesure et les règles de travail. [docs/CADRAGE.md](docs/CADRAGE.md)
garde l'architecture d'origine, les briques open source réutilisées et la roadmap.

On peut aussi l'affronter sur Lichess : [FunFishBot](https://lichess.org/@/FunFishBot) (bullet 2+1,
blitz, rapides courtes).

## Lancer

Il faut Python 3.10 ou plus. Avec [uv](https://docs.astral.sh/uv/) :

```bash
uv run fun-fish
```

Sans uv :

```bash
python -m venv .venv
.venv/bin/pip install -e .        # Windows : .venv\Scripts\pip install -e .
.venv/bin/fun-fish
```

Ou avec make (Linux, macOS) : `make run` (ouvre le navigateur), `make serve`
(serveur seul, `PORT=9000` pour changer de port), `make test`, `make sim-install`,
`make help` pour la liste.

Le navigateur s'ouvre sur l'échiquier, en **mode jeu** : on affronte Fun Fish, l'agent public, sans
réglages ni raisonnement affichés. `fun-fish play --debug` (ou `make debug`) ouvre le **mode debug** :
toutes les personnalités, leurs réglages, le raisonnement de l'IA à chaque coup, les flèches de
candidats et l'export de débogage. Au premier lancement, Stockfish 19 est
téléchargé depuis ses releases officielles et mis en cache (`~/.cache/fun_fish`,
`~/Library/Caches/fun_fish` ou `%LOCALAPPDATA%\fun_fish`). Pour utiliser un autre
binaire : `--stockfish CHEMIN` ou la variable `FUN_FISH_STOCKFISH`.

## Autres commandes

```bash
fun-fish choose "<FEN>" -p blitz -n 50    # ce que choisirait une personnalité, et sa distribution sur 50 tirages
fun-fish choose "<FEN>" -p hustler -s tolerance=150 -s naivety=15   # avec des réglages surchargés
fun-fish read "<FEN>" --horizon 2         # lecture humaine des meilleures lignes (approfondie | court-termiste)
fun-fish replay export.json --list          # coups de l'IA d'un export de debug (bouton « ⤓ Export debug »)
fun-fish replay export.json --ply 55 -s attack_depth=8   # rejouer sa réflexion à ce demi-coup, réglages surchargés
fun-fish stockfish                         # chemin de Stockfish (le télécharge si besoin)
fun-fish play --port 9000 --no-browser
```

## Personnalités

| Clé | Nom | Comportement |
|---|---|---|
| `blitz` | Le blitzeur | Perception à profondeur 8, lecture humaine à 2 demi-coups : mat visible > niveau d'avantage > matériel, hasard entre coups jugés équivalents. Le panneau montre ce que dit la réalité (profondeur 16) |
| `hustler` | L'arnaqueur | Parmi les coups sains (perte ≤ `tolerance`), préfère ceux où un adversaire naïf (instinct : prises, échecs, fourchettes ; voir `naive.py`), simulé sur 2 coups, se trompe souvent et cher. Avec `-s opponent_elo=1300` (extra `sim`), l'adversaire imaginé est Maia-2 à cet Elo. Réglable avec `-s param=valeur` (voir `personalities/hustler.py`) |
| `attacker` | Le cogneur | Parmi les coups sains (perte ≤ 1,5 pion), cherche ceux qui matent ou gagnent ≥ 3 pions en 3 coups contre les réponses probables de l'adversaire (Maia à `opponent_elo`, ou l'instinct). Brutal contre les faibles, réfutable par une bonne défense |
| `romantic` | Le romantique | Le cogneur avec un profil de style : développe vite, ouvre les lignes, vise le roi, préfère l'initiative au matériel, apprécie un sacrifice sain à position égale. Il aime les pions au centre et gagner du temps sur les pièces adverses ; son humeur, tirée à chaque partie, varie ses goûts et donc ses ouvertures ; une partie sur cinq, il a envie d'un gambit connu. Il voit les mats en 4. Il joue des coups calmes humains (vision à 1800 Elo), mais voit toujours les coups forcés ; en difficulté, il cherche du contre-jeu |
| `corsair` | Le corsaire | Le romantique avec l'œil de l'arnaqueur, en un seul calcul : chaque coup est jugé à la fois sur l'attaque (mat, gain) et sur le piège (ce que rapporte la naïveté adverse, probabilité d'une vraie erreur) ; le motif le plus fort mène (`lead_share`), et ses coups dans l'arbre sont choisis pour refermer les pièges (`our_options`) |
| `buccaneer` | Le flibustier | Le corsaire, plus un regard qui ne juge que l'allure des coups (`fun.py`, sans Stockfish) : pièce en l'air, menace ignorée, prise non reprise, sacrifice, assaut, coup inattendu pour un humain de `fun_elo`. Ses favoris sont ajoutés aux candidats, et il accepte d'y perdre jusqu'à `fun_budget` cp |
| `fun_fish` | Fun Fish | L'agent public, figé : le flibustier avec les réglages retenus après les parties de test (adversaire imaginé à 600 Elo, tolérance 300, profondeurs 6, budget fun 270). Seul adversaire du mode jeu |
| `fun_fish_2` | Fun Fish 2 | Fun Fish plus varié en ouverture (deux cavaliers, gambits célèbres, moins d'échecs gratuits) |
| `fun_fish_theorie`, `fun_fish_2_theorie` | … + théorie | Une partie sur quatre, 1 à 4 coups de théorie classique d'abord (`theory.py`), puis à sa façon. `fun_fish_2_theorie` joue sur Lichess |
| `duo` | Le duo | Le romantique choisit ; quand il hésite entre plusieurs coups, l'arnaqueur départage en faveur du meilleur piège |
| `casual` | Le copain du club | MultiPV 6 à profondeur 6, tire au sort parmi les coups à moins de 1,5 pion du meilleur |
| `stockfish` | Stockfish | Meilleur coup à profondeur 14 (étalon) |

Le cogneur et l'arnaqueur partagent un premier étage, le filtre de santé (`Soundness` dans
`personalities/base.py`). Quand la position devient gagnante, il passe progressivement en
conversion, sans changer de niveau : de `convert_from` (+3) à `convert_full` (+8), il vérifie
jusqu'à `convert_depth` (4) demi-coups plus loin, sa tolérance descend jusqu'à
`convert_tolerance` (0,6 pion), et la technique de conversion (`w_technique` : simplifier,
pions passés, roi actif) pèse dans son choix. Il peut encore gâcher une position gagnante,
mais il ne tourne plus en rond.

Le style (`style.py`) départage les coups sains : `w_develop`, `w_open`, `w_king`,
`w_activity`, `w_initiative`, `w_sacrifice` et `w_surprise` (coups peu théoriques en
ouverture selon Maia, « dark forest »). Tous à 0 par défaut pour le cogneur et l'arnaqueur ;
`style_first=N` ne garde que les N coups les plus à leur goût avant le calcul, ce qui est aussi
moins cher.

Le style et l'attaque dialoguent : quand l'attaque voit une occasion nettement meilleure
(mat, gain de matériel), le style des autres coups est tempéré (`style_margin`) et ne peut
plus la renverser ; quand elle n'a pas d'idée tranchée, le style décide. `w_repertoire` donne
du poids au répertoire d'ouvertures de la personnalité (`repertoire.py`), dans la part
`repertoire_rate` des parties où il en a envie. `w_center` (pions et contrôle au centre) et
`w_threat` (gagner du temps sur les pièces adverses) donnent une opinion d'ouverture ; `mood`
multiplie chaque goût par un facteur aléatoire tiré à chaque partie. Chaque mesure a un écart
type plancher : une différence minuscule entre coups ne fait pas une opinion tranchée.
L'humeur redistribue les goûts sans changer leur poids total.

`vision_elo` limite ses coups calmes à ceux qu'un humain de cet Elo jouerait (probabilité Maia ≥
`vision_min`) : un coup d'attente ou prophylactique trop « machine » lui échappe. Les coups
forcés (échecs, prises, promotions) restent toujours visibles, comme pour un attaquant qui les
regarde d'abord : les fulgurances survivent. Ses `vision_breadth` (6) coups les plus naturels sont toujours
envisagés, et quand ça brûle (ce qu'il voit perd au moins `vision_panic` cp sur le vrai meilleur
coup), il voit tout. `w_human` ajoute un goût pour les coups naturels
(log-probabilité Maia, coups forcés exemptés) : un coup bizarre doit rapporter pour passer.
En difficulté (de `defend_from` à `defend_full` cp de retard), `w_counterplay` lui fait
préférer les coups qui posent des questions : échecs, menaces, roi adverse visé. Sa tolérance
descend alors comme en conversion : le contre-jeu doit rester sain.
En conversion,
`convert_elo` fait monter l'adversaire imaginé (il compte moins sur les gaffes), `w_repeat`
pénalise le retour à une position déjà vue, et le mat est cherché un coup plus loin.

**Conseils à plusieurs étages** (`personalities/council.py`) : une personnalité propose, la
suivante départage. Le premier étage juge tous les coups et garde ceux qu'il aime presque autant
que son favori (`keep`) ; s'il a un avis tranché, il est suivi. Sinon l'étage suivant ne juge que
ces coups-là, et les deux distributions se multiplient. Pour un nouveau conseil, sous-classer
`Council` avec la liste `STAGES`. Les réglages communs (`opponent_elo`, `tolerance`,
`soundness_depth`) valent pour tous les étages ; ceux de chaque étage sont préfixés par
l'initiale de sa clé : `-s r_convert_tolerance=150` pour le romantique du duo, `h_w_trap` pour
l'arnaqueur.

Pour en ajouter une, sous-classer `Personality` dans `src/fun_fish/personalities/`
et l'inscrire dans le registre de `personalities/__init__.py`.

## Simulations contre Maia

[Maia-2](https://github.com/CSSLab/maia2) prédit le coup qu'un humain d'un Elo donné
jouerait (tranches < 1100, 1100 à 1999 par 100, ≥ 2000). On s'en sert comme adversaire
simulé pour mesurer une personnalité à grande échelle. Installation (PyTorch CPU d'abord,
pour éviter les 2 Go de CUDA) :

```bash
.venv/bin/pip install "torch==2.8.*" --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -e ".[sim]"
```

Les poids (280 Mo) sont téléchargés au premier lancement dans le cache de fun_fish.

**Sans PyTorch.** L'extra `maia` (`pip install -e '.[maia]'` : onnxruntime, 24 Mo) suffit.
Maia-2 exporté en ONNX (93 Mo) est alors téléchargé depuis la release GitHub du projet,
avec vérification SHA-256. fun_fish préfère ce moteur, plus léger et plus rapide, avec les
mêmes probabilités. `fun-fish maia-export` refait l'export à partir de PyTorch (fait par
`make sim-install`).

```bash
# 20 parties par tranche de 1100 à 1900 (pas de 200), couleurs alternées, en parallèle
fun-fish sim -p hustler -s tolerance=150 --elos 1100-1900:200 -g 20 -o runs/hustler_t150.jsonl
fun-fish sim-report runs/*.jsonl         # synthèse : score, mats, durée, éval et matériel aux coups 10/20/30/40
```

La valeur `match` prend l'Elo de l'adversaire de chaque partie : `-s opponent_elo=match`
fait jouer l'arnaqueur contre un Maia qu'il modélise exactement.

Un fichier existant est repris là où il s'est arrêté. Ordres de grandeur sur 22 cœurs :
le blitzeur joue ~3 parties par seconde, l'arnaqueur ~1 partie par minute et par cœur.

### Réglage paramètre par paramètre

```bash
fun-fish sweep -p romantic -s opponent_elo=300 -s tolerance=300 --vary impatience=0.5,1 --vary w_plan=1 \
    --elos 1100,1500,1800 -g 12 -o runs/sweep_romantic
fun-fish sweep-report runs/sweep_romantic
```

Une référence, puis chaque paramètre modifié seul, sur les mêmes parties (mêmes graines). Le
critère principal est la rapidité : coups par partie, une partie sans notre mat comptant 100. À
côté : score, sacrifices, fulgurances, coups calmes « machine », plans poursuivis, temps par coup.

### Coups célèbres

`fun-fish brilliancies -p romantic -n 4` rejoue des coups célèbres (Morphy, Anderssen, Fischer,
Réti) et dit s'ils sont vus et joués ; `--file` pour ses propres parties (JSON : `name`, `moves`,
`targets` comme « 10.Nxb5 » ou « 17...Be6 »).

### Livre d'ouverture

Maia-2 n'a pas été entraîné sur les 10 premiers demi-coups des parties. En ouverture, les
probabilités humaines viennent donc d'un livre (`src/fun_fish/data/book.json.gz`) construit à
partir de la base publique de Lichess (CC0) : `fun-fish book-build --month 2025-09 --games
2000000` le reconstruit (environ 600 Mo lus en flux, extra `sim`).

## Bot Lichess

```bash
fun-fish lichess-bot --upgrade              # une fois : passer un compte neuf en compte BOT (définitif)
fun-fish lichess-bot -p fun_fish_2_theorie --max-games 2
```

Le jeton (scope `bot:play`) est lu dans `~/.config/fun_fish/lichess_bot_token` ou dans la variable
`LICHESS_BOT_TOKEN`. Le bot accepte les défis d'humains en partie standard, de 2+1 à environ 15
minutes estimées. Pressé par la pendule, il raccourcit sa réflexion (`effort`, voir `pace.py`).
Chaque partie laisse un export de debug dans `~/.local/share/fun_fish/bot_games/`. Pour le
faire tourner sur un serveur : [deploy/README.md](deploy/README.md).

## Développement

```bash
uv run --extra dev --extra maia pytest -q                    # tests, dont la non-régression du choix de coup
python scripts/perfbench.py run essai -s attack_moves=2      # banc : temps et qualité sur 292 positions réelles
python scripts/perfbench.py table base essai
python scripts/clocksim.py 180 0                             # parties simulées à la pendule (3+0)
```

Voir [ONBOARDING.md](ONBOARDING.md) pour la méthode : une optimisation doit donner exactement les
mêmes coups ; un changement de comportement se mesure avant de régénérer les coups attendus
(`perfbench.py golden`).

## Licence

GPL-3.0-or-later. Le projet utilise Stockfish, python-chess et chessground, tous trois
sous GPL-3.0.
