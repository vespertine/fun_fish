"""Mémoire des adversaires : une ligne par partie terminée, dans une petite base SQLite partagée.

Les cinq bots du serveur écrivent dans le même fichier (mode WAL, une connexion courte par accès). On en
tire l'accueil des humains qui reviennent (`welcome`) et, plus tard, le choix des bots à défier : un bot
qui joue comme Stockfish (perte quasi nulle par coup) ne donne pas de partie intéressante.
Bibliothèque standard seulement, comme `lichess_bot`.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import random
import sqlite3
import time
import urllib.request
from dataclasses import astuple, dataclass, fields
from pathlib import Path

DB_PATH = Path("~/.local/share/fun_fish/opponents.db").expanduser()
LICHESS = "https://lichess.org"
SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,      -- partie Lichess
    account TEXT NOT NULL,    -- notre compte (identifiant Lichess, en minuscules)
    opponent TEXT NOT NULL,   -- identifiant de l'adversaire ; « ai-level-N » pour l'IA de Lichess
    title TEXT,               -- BOT, GM… ou rien
    bot INTEGER NOT NULL,     -- 1 : un programme (compte BOT ou IA de Lichess)
    rating INTEGER,           -- classement de l'adversaire au début de la partie
    speed TEXT,
    rated INTEGER,
    color TEXT NOT NULL,      -- notre couleur : white ou black
    result TEXT NOT NULL,     -- pour nous : win, loss, draw ou aborted
    status TEXT,              -- fin selon Lichess : mate, resign, outoftime…
    plies INTEGER,
    ended_at REAL NOT NULL,   -- horodatage Unix
    their_loss REAL,          -- perte moyenne de l'adversaire par coup (centipions, selon nos évals)
    our_loss REAL             -- la nôtre : l'écart volontaire au meilleur coup (le style)
    -- + les colonnes de MIGRATIONS (ajoutées aux bases existantes)
);
CREATE INDEX IF NOT EXISTS games_pair ON games (account, opponent, ended_at);
CREATE TABLE IF NOT EXISTS challenges (
    id TEXT,                  -- défi Lichess reçu
    account TEXT NOT NULL,    -- notre compte
    challenger TEXT NOT NULL,
    title TEXT,
    bot INTEGER NOT NULL,     -- 1 : un programme
    rating INTEGER,
    speed TEXT,
    clock TEXT,               -- « 180+2 », ou correspondence…
    rated INTEGER,
    decision TEXT NOT NULL,   -- accepted, declined, failed (acceptation refusée par Lichess ou réseau)
    reason TEXT,              -- raison du refus : later, tooFast, casual…, ou précision (« compte occupé »)
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS challenges_account ON challenges (account, at);
CREATE TABLE IF NOT EXISTS highlights (  -- les coups notables de nos parties (notable.py)
    game TEXT NOT NULL,       -- partie Lichess
    account TEXT NOT NULL,
    ply INTEGER NOT NULL,
    san TEXT NOT NULL,
    kind TEXT NOT NULL,       -- piège, sacrifice, style, notre erreur, leur erreur, garde-fou
    cost INTEGER,             -- cp : meilleur candidat moins coup choisi (éval de la personnalité)
    swing INTEGER,            -- cp : de l'éval du coup à celle de notre coup suivant (une erreur, de qui ?)
    chance INTEGER,           -- % : probabilité d'erreur annoncée par le piège
    gain REAL,                -- pions : gain annoncé par le piège
    explanation TEXT,         -- la justification de la personnalité (début)
    PRIMARY KEY (game, ply, kind)
);
CREATE TABLE IF NOT EXISTS declines (
    opponent TEXT NOT NULL,   -- le bot qui a refusé l'un de nos défis (tous nos comptes réunis)
    reason TEXT NOT NULL,     -- declineReasonKey de Lichess : nobot, rated, timecontrol…
    clock TEXT,               -- la cadence proposée, « 180+2 »
    at REAL NOT NULL          -- horodatage Unix
);
CREATE INDEX IF NOT EXISTS declines_opponent ON declines (opponent, at);
"""
MIGRATIONS = {  # colonnes de `games` ajoutées après coup : (nom, type) ; absentes des parties importées
    "ending": "TEXT",  # fin à notre initiative ou anormale : abandon, tricotage, nulle acceptée, flux perdu
    "min_clock": "REAL",  # notre pendule la plus basse avant l'un de nos coups (s)
    "longest_move": "REAL",  # notre plus longue réflexion (s)
    "reduced": "REAL",  # part de nos coups à réflexion réduite (effort ≥ REDUCED_EFFORT) ou sans analyse
}
REDUCED_EFFORT = 3  # niveau d'effort (pace.COST) dès lequel la réflexion est nettement raccourcie (≤ 38 %)
INIT_TRIES = 20
EVAL_CAP = 1000  # cp : au-delà, la partie est jouée ; les écarts n'y veulent plus rien dire (convention de l'ACPL)


@dataclass
class GameRecord:
    id: str
    account: str
    opponent: str
    title: str | None
    bot: bool
    rating: int | None
    speed: str | None
    rated: bool | None
    color: str
    result: str
    status: str | None
    plies: int | None
    ended_at: float
    their_loss: float | None = None
    our_loss: float | None = None
    ending: str | None = None
    min_clock: float | None = None
    longest_move: float | None = None
    reduced: float | None = None


@dataclass
class History:
    """Le passé d'un adversaire face à l'un de nos comptes (parties annulées exclues)."""

    games: int = 0
    wins: int = 0  # les nôtres
    draws: int = 0
    losses: int = 0
    last: float | None = None  # fin de la dernière partie


def migrate(con: sqlite3.Connection) -> None:
    """Ajoute aux bases existantes les colonnes de MIGRATIONS (un autre bot a pu le faire entre-temps)."""
    have = {row[1] for row in con.execute("PRAGMA table_info(games)")}
    for name, kind in MIGRATIONS.items():
        if name not in have:
            try:
                con.execute(f"ALTER TABLE games ADD COLUMN {name} {kind}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc):
                    raise


@contextlib.contextmanager
def connect(path: Path | None = None):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    try:
        for attempt in range(INIT_TRIES):  # base neuve ouverte par plusieurs bots : le passage en WAL peut buter
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                migrate(con)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == INIT_TRIES - 1:
                    raise
                time.sleep(0.05 * (attempt + 1) + random.random() * 0.05)
        with con:  # transaction : validée en sortie, annulée sur exception
            yield con
    finally:
        con.close()


def record(game: GameRecord, path: Path | None = None, how: str = "merge") -> None:
    """Enregistre une partie, ou la met à jour si elle y est déjà (un import ne double rien), selon `how` :
    « merge » (import) : des mesures absentes ne remplacent pas celles déjà notées par le bot ;
    « confirmed » (fin confirmée par le bot) : remplace tout, y compris une fin « flux perdu » notée avant ;
    « absent » (partie perdue de vue, notée d'après Lichess) : seulement si elle n'y est pas encore (atomique)."""
    names = [f.name for f in fields(GameRecord)]
    kept = {"their_loss", "our_loss", *MIGRATIONS} if how == "merge" else set()
    updates = ", ".join(f"{n} = coalesce(excluded.{n}, games.{n})" if n in kept else f"{n} = excluded.{n}"
                        for n in names if n != "id")
    conflict = "DO NOTHING" if how == "absent" else f"DO UPDATE SET {updates}"
    with connect(path) as con:
        con.execute(f"INSERT INTO games ({', '.join(names)}) VALUES ({', '.join('?' * len(names))}) "
                    f"ON CONFLICT (id) {conflict}", astuple(game))


def note_highlights(game_id: str, account: str, export: dict, path: Path | None = None) -> int:
    """Les coups notables d'une partie (sans doublon si on la renote). Renvoie leur nombre."""
    from fun_fish import notable
    rows = [(game_id, account.lower(), h["ply"], h["san"], h["kind"], h["cost"], h["swing"], h["chance"], h["gain"],
             h["explanation"]) for h in notable.highlights(export)]
    with connect(path) as con:
        con.executemany("INSERT OR REPLACE INTO highlights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def challenge(account: str, c: dict, decision: str, reason: str | None, at: float | None = None,
              path: Path | None = None) -> None:
    """Un défi reçu et ce qu'on en a fait (événement `challenge` de Lichess)."""
    name, title, program, rating = player(c.get("challenger") or {})
    clock = c.get("timeControl") or {}
    pace = f"{clock.get('limit', 0)}+{clock.get('increment', 0)}" if clock.get("type") == "clock" else clock.get("type")
    with connect(path) as con:
        con.execute("INSERT INTO challenges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (c.get("id"), account.lower(), name, title, program, rating, c.get("speed"), pace,
                     c.get("rated"), decision, reason, time.time() if at is None else at))


def history(account: str, opponent: str, path: Path | None = None) -> History:
    with connect(path) as con:
        row = con.execute("SELECT count(*), sum(result = 'win'), sum(result = 'draw'), sum(result = 'loss'), "
                          "max(ended_at) FROM games WHERE account = ? AND opponent = ? AND result != 'aborted'",
                          (account.lower(), opponent.lower())).fetchone()
    return History(row[0], row[1] or 0, row[2] or 0, row[3] or 0, row[4])


def result_for(color: str, status: str | None, winner: str | None) -> str:
    if status in ("aborted", "noStart"):
        return "aborted"
    if winner is None:
        return "draw"
    return "win" if winner == color else "loss"


def losses(moves: list[dict], color: str) -> tuple[float | None, float | None]:
    """Pertes moyennes par coup (adversaire, nous), tirées des évals de nos propres coups, sans analyse en plus.

    À chacun de nos coups, l'export garde les candidats (évals du point de vue des Blancs) : le meilleur
    donne l'éval de la position, le coup choisi celle d'après. Les deux à notre point de vue : l'adversaire
    a perdu ce dont notre éval a monté entre notre coup et le suivant ; nous, l'écart du meilleur au choisi.
    """
    sign = 1 if color == "white" else -1
    ours: dict[int, tuple[int, int]] = {}  # ply -> (meilleure éval, éval du coup choisi), pour nous
    for m in moves:
        candidates = (m.get("decision") or {}).get("candidates") or []
        chosen = [c["score"] for c in candidates if c.get("chosen")]
        if m.get("by") == "ai" and chosen:
            clip = lambda s: max(-EVAL_CAP, min(EVAL_CAP, sign * s))  # noqa: E731
            ours[m["ply"]] = (max(clip(c["score"]) for c in candidates), clip(chosen[0]))
    their = [max(0, ours[ply + 2][0] - chosen) for ply, (_, chosen) in ours.items() if ply + 2 in ours]
    mine = [best - chosen for best, chosen in ours.values()]
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return mean(their), mean(mine)


# Accueil des humains qui reviennent : un message par langue (140 caractères au plus), puis le compteur.
REMATCH = 3600  # s : la partie d'avant vient de finir
LONG_ABSENCE = 14 * 86400  # s : des retrouvailles
REGULAR = 10  # parties : un habitué
WELCOMES = {
    "revanche": [("On remet ça ? Avec plaisir !", "Another one? With pleasure!"),
                 ("Encore une ! Je replace les pièces.", "One more! Let me reset the pieces.")],
    "retrouvailles": [("Ça faisait longtemps ! Quel plaisir de te retrouver.", "It's been a while! So glad to see you again."),
                      ("Te revoilà ! Les pièces prenaient la poussière.", "There you are at last! The pieces were gathering dust.")],
    "habitué": [("Tiens, qui voilà ! Toujours un plaisir de jouer avec toi.", "Look who's here! Always a pleasure to play you."),
                ("Ah, un habitué ! Installe-toi, je replace les pièces.", "Ah, a regular! Have a seat, I'll set up the pieces."),
                ("Content de te revoir, l'ami ! On reprend où on en était ?", "Great to see you again, friend! Where were we?")],
    "retour": [("Re-bonjour !", "Welcome back!"),
               ("Content de te revoir !", "Good to see you again!")],
}


def welcome_kind(past: History, now: float) -> str | None:
    if not past.games or past.last is None:
        return None
    gap = now - past.last
    if gap >= LONG_ABSENCE:
        return "retrouvailles"
    if gap <= REMATCH:
        return "revanche"
    return "habitué" if past.games >= REGULAR else "retour"


CHAT_MAX = 140
BALANCED = 0.25  # part des points du camp le moins heureux : en dessous, le compteur se tait (0-18 ne se rappelle pas)


def milestone(games: int) -> bool:
    """La partie qui commence est-elle un cap à fêter (10e, 20e, 50e, puis toutes les centaines) ?"""
    return games in (10, 20, 50) or (games >= 100 and games % 100 == 0)


def balanced(past: History) -> bool:
    ours = past.wins + past.draws / 2
    return min(ours, past.games - ours) >= BALANCED * past.games


def welcome(past: History, now: float | None = None, rng: random.Random | None = None) -> tuple[str, str] | None:
    """L'accueil (français, anglais) d'un humain déjà rencontré ; None pour un inconnu. Le compteur s'y ajoute
    s'il est assez équilibré, et le numéro de la partie si c'est un cap."""
    kind = welcome_kind(past, time.time() if now is None else now)
    if kind is None:
        return None
    lines = [(rng or random).choice(WELCOMES[kind])]
    n = past.games + 1
    if milestone(n):
        lines.append((f"C'est notre {n}e partie ensemble !", f"This is our {n}th game together!"))
    if balanced(past):
        draws_fr = f", {past.draws} nulle{'s' if past.draws > 1 else ''}" if past.draws else ""
        draws_en = f", {past.draws} draw{'s' if past.draws > 1 else ''}" if past.draws else ""
        lines.append((f"Le compteur : toi {past.losses}, moi {past.wins}{draws_fr}.",
                      f"Score so far: you {past.losses}, me {past.wins}{draws_en}."))
    while True:  # 140 caractères au plus (Lichess refuse au-delà) : le compteur, puis le cap, cèdent la place
        fr, en = (" ".join(part[i] for part in lines) for i in (0, 1))
        if len(lines) == 1 or max(len(fr), len(en)) <= CHAT_MAX:
            return fr, en
        lines.pop()


# Import de l'historique : les parties publiques de nos comptes, complétées par nos exports de debug.

def player(side: dict) -> tuple[str, str | None, bool, int | None]:
    """(identifiant, titre, programme ?, classement) d'un joueur, tel que Lichess le décrit."""
    if "aiLevel" in side:
        return f"ai-level-{side['aiLevel']}", None, True, None
    user = side.get("user") or side
    title = user.get("title")
    return (user.get("id") or user.get("name") or "?").lower(), title, title == "BOT", side.get("rating")


def from_lichess(game: dict, account: str, exports: dict[str, dict]) -> GameRecord | None:
    """Une partie de l'export public de Lichess (ndjson), vue depuis notre compte."""
    players = game.get("players", {})
    color = next((c for c in ("white", "black") if player(players.get(c, {}))[0] == account), None)
    if color is None or game.get("variant", "standard") != "standard":
        return None
    opponent, title, bot, rating = player(players.get("black" if color == "white" else "white", {}))
    export = exports.get(game["id"])
    their, mine = losses(export["moves"], color) if export else (None, None)
    return GameRecord(game["id"], account, opponent, title, bot, rating, game.get("speed"), game.get("rated"),
                      color, result_for(color, game.get("status"), game.get("winner")), game.get("status"),
                      len(game.get("moves", "").split()) or None, (game.get("lastMoveAt") or game["createdAt"]) / 1000,
                      their, mine)


def read_exports(games_dir: Path) -> dict[str, dict]:
    """Les exports de debug, par identifiant de partie Lichess."""
    exports = {}
    for path in sorted(Path(games_dir).glob("*.json.gz")):
        try:
            export = json.load(gzip.open(path, "rt", encoding="utf-8"))
        except (OSError, ValueError):
            continue
        game_id = (export.get("lichess") or {}).get("id")
        if game_id:
            exports[game_id] = export
    return exports


def lichess_games(account: str):
    """Toutes les parties terminées d'un compte (export public, sans jeton)."""
    req = urllib.request.Request(f"{LICHESS}/api/games/user/{account}?moves=true&finished=true",
                                 headers={"Accept": "application/x-ndjson", "User-Agent": "fun_fish-bot"})
    with urllib.request.urlopen(req, timeout=60) as r:
        for raw in r:
            if line := raw.strip():
                yield json.loads(line)


def import_history(accounts, games_dir: Path, fetch=lichess_games, path: Path | None = None) -> int:
    """Verse dans la base les parties passées de nos comptes. Renvoie le nombre de parties enregistrées."""
    exports, count = read_exports(games_dir), 0
    for account in accounts:
        for game in fetch(account):
            rec = from_lichess(game, account.lower(), exports)
            if rec is not None:
                record(rec, path)
                if rec.id in exports:
                    note_highlights(rec.id, account, exports[rec.id], path)
                count += 1
    return count


# Choix des bots à défier : le profil de chaque bot, tous nos comptes réunis, lissé dans le temps (une
# partie pèse moitié moins tous les HALF_LIFE) pour suivre un bot mis à jour. Un bot qui joue comme
# Stockfish (perte quasi nulle, et nous perdons) est presque écarté ; les parties disputées sont
# préférées ; un bot que l'on domine garde sa place (il fait des parties, et plaît peut-être aux humains).
HALF_LIFE = 30 * 86400  # s
EVIDENCE = 2.5  # parties pondérées avant de juger un bot : environ trois parties récentes
STOCKFISH_LOSS = 20.0  # cp par coup : à peu près le bruit de nos évals, celui d'un jeu parfait
STOCKFISH_SCORE = 0.3  # notre score au plus
DISPUTED = (0.25, 0.75)  # notre score
INTEREST = {"stockfish": 0.1, "disputé": 1.5, "dominé": 1.0, "coriace": 1.0, "nouveau": 1.0}


@dataclass
class Profile:
    games: float = 0.0  # pondérées par l'ancienneté
    score: float | None = None  # le nôtre, de 0 à 1
    their_loss: float | None = None
    measured: float = 0.0  # poids des parties dont la perte est mesurée (un import sans export n'en a pas)

    @property
    def kind(self) -> str:
        if self.games < EVIDENCE or self.score is None:
            return "nouveau"
        if (self.their_loss is not None and self.measured >= EVIDENCE  # pas sur une seule mesure ancienne
                and self.their_loss <= STOCKFISH_LOSS and self.score <= STOCKFISH_SCORE):
            return "stockfish"
        if DISPUTED[0] <= self.score <= DISPUTED[1]:
            return "disputé"
        return "dominé" if self.score > DISPUTED[1] else "coriace"

    @property
    def interest(self) -> float:
        return INTEREST[self.kind]


# Refus de nos défis. Certains disent « jamais » (nobot : pas de bots ; variant ; rated : que du classé,
# alors que nos défis sont amicaux) : on laisse ce bot tranquille longtemps. D'autres visent la cadence :
# on lui en propose une autre. Les autres (later, generic…) : un jour, comme avant.
DECLINE_DAYS = {"nobot": 30, "variant": 30, "rated": 30, "generic": 7}
CLOCK_REASONS = {"timecontrol", "toofast", "tooslow"}
CLOCK_DAYS = 7
DEFAULT_DAYS = 1


@dataclass
class Refusal:
    blocked: bool = False  # ne plus le défier pour l'instant
    clocks: frozenset[str] = frozenset()  # cadences refusées récemment (« 180+2 »)


def decline(opponent: str, reason: str, clock: str | None, at: float | None = None, path: Path | None = None) -> None:
    with connect(path) as con:
        con.execute("INSERT INTO declines VALUES (?, ?, ?, ?)",
                    (opponent, reason, clock, time.time() if at is None else at))


def refusals(now: float | None = None, path: Path | None = None) -> dict[str, Refusal]:
    """Les refus encore en vigueur, par bot."""
    now = time.time() if now is None else now
    horizon = now - 86400 * max(max(DECLINE_DAYS.values()), CLOCK_DAYS, DEFAULT_DAYS)
    blocked, clocks = set(), {}
    with connect(path) as con:
        rows = con.execute("SELECT opponent, reason, clock, at FROM declines WHERE at >= ?", (horizon,)).fetchall()
    for opponent, reason, clock, at in rows:
        if reason in CLOCK_REASONS:
            if clock and now - at < 86400 * CLOCK_DAYS:
                clocks.setdefault(opponent, set()).add(clock)
        elif now - at < 86400 * DECLINE_DAYS.get(reason, DEFAULT_DAYS):
            blocked.add(opponent)
    return {name: Refusal(name in blocked, frozenset(clocks.get(name, ()))) for name in blocked | set(clocks)}


def bot_profiles(now: float | None = None, path: Path | None = None) -> dict[str, Profile]:
    now = time.time() if now is None else now
    with connect(path) as con:
        rows = con.execute("SELECT opponent, result, their_loss, ended_at FROM games "
                           "WHERE bot = 1 AND result != 'aborted'").fetchall()
    sums: dict[str, list[float]] = {}  # poids, points, poids mesuré, perte pondérée
    for opponent, result, loss, ended_at in rows:
        w = 0.5 ** (max(0.0, now - ended_at) / HALF_LIFE)
        acc = sums.setdefault(opponent, [0.0, 0.0, 0.0, 0.0])
        acc[0] += w
        acc[1] += w * {"win": 1.0, "draw": 0.5}.get(result, 0.0)
        if loss is not None:
            acc[2] += w
            acc[3] += w * loss
    return {name: Profile(w, points / w, loss / measured if measured else None, measured)
            for name, (w, points, measured, loss) in sums.items() if w > 0}


def notable_report(path: Path | None = None) -> tuple[list[tuple], list[tuple]]:
    """Coups notables par partie, par compte et sorte ; et les pièges : annoncés contre tenus, humains et bots."""
    from fun_fish import notable
    with connect(path) as con:
        kinds = con.execute(
            "SELECT h.account, h.kind, round(1.0 * count(*) / (SELECT count(DISTINCT game) FROM highlights x "
            "WHERE x.account = h.account), 2) FROM highlights h GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
        traps = con.execute(
            "SELECT h.account, CASE g.bot WHEN 1 THEN 'bots' ELSE 'humains' END, count(*), round(avg(h.chance)), "
            "round(100.0 * sum(h.swing >= ?) / count(*)) FROM highlights h JOIN games g ON g.id = h.game "
            "WHERE h.kind = 'piège' GROUP BY 1, 2 ORDER BY 1, 2", (notable.SWING,)).fetchall()
    return kinds, traps


def challenge_report(days: float = 7, now: float | None = None, path: Path | None = None) -> list[tuple]:
    """Les défis reçus, par compte, humain ou bot, décision et raison : les humains qu'on n'a pas pu servir."""
    since = (time.time() if now is None else now) - days * 86400
    with connect(path) as con:
        return con.execute(
            "SELECT account, CASE bot WHEN 1 THEN 'bot' ELSE 'humain' END, decision, reason, count(*) FROM challenges "
            "WHERE at >= ? GROUP BY 1, 2, 3, 4 ORDER BY 1, 2 DESC, 5 DESC", (since,)).fetchall()


def report(path: Path | None = None, bots: bool = True, limit: int = 40) -> list[tuple]:
    """Bilan par adversaire, tous nos comptes réunis : parties, score, perte moyenne, longueur."""
    with connect(path) as con:
        return con.execute(
            "SELECT opponent, title, count(*) AS n, round(avg(CASE result WHEN 'win' THEN 1 WHEN 'draw' THEN 0.5 "
            "ELSE 0 END) * 100) AS score, round(avg(their_loss), 1), round(avg(our_loss), 1), round(avg(plies)), "
            "count(their_loss) FROM games WHERE result != 'aborted' AND bot = ? GROUP BY opponent "
            "ORDER BY n DESC LIMIT ?", (int(bots), limit)).fetchall()
