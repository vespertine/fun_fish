# fun_fish

Jouer aux échecs contre des **personnalités** : des joueurs artificiels construits sur
les évaluations de Stockfish, mais qui choisissent leurs coups selon un style (sharp,
piégeux, matérialiste…) plutôt que selon la seule force.

Voir [docs/CADRAGE.md](docs/CADRAGE.md) pour l'architecture, les briques open source
réutilisées et la roadmap.

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

Le navigateur s'ouvre sur l'échiquier. Au premier lancement, Stockfish 19 est
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
| `romantic` | Le romantique | Le cogneur avec un profil de style : développe vite, ouvre les lignes, vise le roi, préfère l'initiative au matériel, apprécie un sacrifice sain à position égale |
| `casual` | Le copain du club | MultiPV 6 à profondeur 6, tire au sort parmi les coups à moins de 1,5 pion du meilleur |
| `stockfish` | Stockfish | Meilleur coup à profondeur 14 (étalon) |

Le cogneur et l'arnaqueur partagent un premier étage, le filtre de santé (`Soundness` dans
`personalities/base.py`). Quand la position est gagnante (au-delà de `convert_above`, +5 par
défaut), il passe en mode conversion : vérification à profondeur 14 et tolérance de 0,3 pion,
pour conclure au lieu de tourner en rond.

Le style (`style.py`) départage les coups sains : `w_develop`, `w_open`, `w_king`,
`w_activity`, `w_initiative`, `w_sacrifice` et `w_surprise` (coups peu théoriques en
ouverture selon Maia, « dark forest »). Tous à 0 par défaut pour le cogneur et l'arnaqueur ;
`style_first=N` ne garde que les N coups les plus à leur goût avant le calcul, ce qui est aussi
moins cher.

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

## Développement

```bash
uv run --extra dev pytest
```

## Licence

GPL-3.0-or-later. Le projet utilise Stockfish, python-chess et chessground, tous trois
sous GPL-3.0.
