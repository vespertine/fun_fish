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

Le navigateur s'ouvre sur l'échiquier. Au premier lancement, Stockfish 19 est
téléchargé depuis ses releases officielles et mis en cache (`~/.cache/fun_fish`,
`~/Library/Caches/fun_fish` ou `%LOCALAPPDATA%\fun_fish`). Pour utiliser un autre
binaire : `--stockfish CHEMIN` ou la variable `FUN_FISH_STOCKFISH`.

## Autres commandes

```bash
fun-fish choose "<FEN>" -p blitz -n 50    # ce que choisirait une personnalité, et sa distribution sur 50 tirages
fun-fish choose "<FEN>" -p hustler -s tolerance=150 -s naivety=15   # avec des réglages surchargés
fun-fish read "<FEN>" --horizon 2         # lecture humaine des meilleures lignes (approfondie | court-termiste)
fun-fish stockfish                         # chemin de Stockfish (le télécharge si besoin)
fun-fish play --port 9000 --no-browser
```

## Personnalités

| Clé | Nom | Comportement |
|---|---|---|
| `blitz` | Le blitzeur | Perception à profondeur 8, lecture humaine à 2 demi-coups : mat visible > niveau d'avantage > matériel, hasard entre coups jugés équivalents. Le panneau montre ce que dit la réalité (profondeur 16) |
| `hustler` | L'arnaqueur | Parmi les coups sains (perte ≤ `tolerance`), préfère ceux où un adversaire naïf (instinct : prises, échecs, fourchettes ; voir `naive.py`), simulé sur 2 coups, se trompe souvent et cher. Réglable avec `-s param=valeur` (voir `personalities/hustler.py`) |
| `casual` | Le copain du club | MultiPV 6 à profondeur 6, tire au sort parmi les coups à moins de 1,5 pion du meilleur |
| `stockfish` | Stockfish | Meilleur coup à profondeur 14 (étalon) |

Pour en ajouter une, sous-classer `Personality` dans `src/fun_fish/personalities/`
et l'inscrire dans le registre de `personalities/__init__.py`.

## Développement

```bash
uv run --extra dev pytest
```

## Licence

GPL-3.0-or-later. Le projet utilise Stockfish, python-chess et chessground, tous trois
sous GPL-3.0.
