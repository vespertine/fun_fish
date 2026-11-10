# fun_fish

*[Version française](README.fr.md)*

Play chess against **personalities**: artificial players built on Stockfish's evaluations, but
choosing their moves by style (attacks, traps, sacrifices…) rather than by strength alone. They
play to win, but above all for a fun game.

## Play

Two ways:

1. **On Lichess, nothing to install** (the easiest): challenge
   [FunFishBot](https://lichess.org/@/FunFishBot) or [CrazyFishBot](https://lichess.org/@/CrazyFishBot),
   its wilder cousin. Bullet 2+1, blitz and short rapid; games against humans.
2. **On your own machine**, with every personality and its move-by-move reasoning: see
   [Installation](#installation).

## Installation

You need Python 3.10 or later and [uv](https://docs.astral.sh/uv/) (to install it:
[docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/)).

```bash
git clone https://github.com/vespertine/fun_fish.git
cd fun_fish
uv run --extra maia fun-fish
```

Without uv:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[maia]"        # Windows: .venv\Scripts\pip install -e .
.venv/bin/fun-fish
```

Your browser opens on the board in **play mode**: pick your opponent among the public agents
(including the Lichess bots, with the same settings). `fun-fish play --debug` opens **debug mode**:
every personality, its settings, the AI's reasoning at each move, candidate arrows and a debug
export. On first launch, Stockfish 19 and the Maia-2 human model (93 MB, the `maia` extra) are
downloaded and cached. Without that extra, the agents fall back to a cruder model of the opponent and
no longer play like the Lichess bots.

## Personalities

The public ones:

| Key | Name | Style |
|---|---|---|
| `fun_fish_2_theorie` | Fun Fish 2 + theory | Plays on Lichess as FunFishBot. Attacks and traps against the likely replies of a human opponent (modelled by [Maia-2](https://github.com/CSSLab/maia2)); one game in four opens with a few moves of classical theory |
| `fun_fish_2` | Fun Fish 2 | The same, without the theory |
| `fun_fish` | Fun Fish | The first version, less varied in the opening |
| `crazy_fish` | Crazy Fish | Plays on Lichess as CrazyFishBot. Believes in all its traps, dares creative moves, and once winning cares about style more than material (*panache*) |

Every move stays **sound**: a safety net checked by Stockfish rejects real blunders. The
experimental personalities (blitzer, hustler, attacker, romantic…) are described in the
[French README](README.fr.md#personnalités).

## Contributing

The code, comments and developer docs are in French. Start with [ONBOARDING.md](ONBOARDING.md):
a map of the code, how a move is chosen, the measuring tools and the working rules.

```bash
uv run --extra dev --extra maia pytest -q    # tests, including move-choice non-regression
```

Running your own bot on a server: [deploy/README.md](deploy/README.md).

## License

GPL-3.0-or-later. The project uses Stockfish, python-chess and chessground, all three under
GPL-3.0.
