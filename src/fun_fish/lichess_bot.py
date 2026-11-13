"""Passerelle Lichess : une personnalité joue sur un compte BOT (API bot de Lichess).

Accepte les défis de parties standard d'humains (bullet 2+1, blitz, rapides courtes), au plus `max_games`
à la fois, joue chaque partie dans son propre fil avec son propre Stockfish, et garde un export
de debug compressé de chaque partie (même format que le bouton « Export debug »).
Bibliothèque standard seulement : pas de dépendance en plus.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import chess

LICHESS = "https://lichess.org"
TOKEN_PATH = Path("~/.config/fun_fish/lichess_bot_token").expanduser()
GAMES_DIR = Path("~/.local/share/fun_fish/bot_games").expanduser()
DRAIN_PATH = Path("~/.config/fun_fish/drain").expanduser()  # présent : on finit ses parties sans en accepter (déploiement)
BLOCKED_PATH = Path("~/.config/fun_fish/blocked").expanduser()  # un compte Lichess par ligne : défis toujours refusés
MAX_ESTIMATE = 900  # durée estimée par Lichess (base + 40 × incrément, en s) : blitz et rapides courtes
MIN_LIMIT = 120  # bullet à partir de 2+1 (≈ 1,7 s de réflexion par coup) ; 1+0, 1+1, 2+0 refusés
# Défis d'autres bots : le bot ne joue qu'une partie à la fois, il doit rester libre pour les humains.
# - Après une partie contre un bot, pause de BOT_PAUSE × sa durée réelle : au plus un quart du temps
#   contre des bots, et une partie courte coûte moins qu'une longue (2+1 : ~15 min ; 5+2 : ~35 min).
# - Après une partie contre un humain, HUMAN_GRACE pour qu'il puisse enchaîner (revanche, nouveau défi).
# - Le même bot, au plus une fois par BOT_SAME_OPPONENT : un seul bot ne prend pas toutes les places.
# - Jusqu'à 10+0 (BOT_MAX_ESTIMATE, même estimation que MAX_ESTIMATE) ; au-delà, « tooSlow ».
BOT_MAX_ESTIMATE = 600  # s
BOT_PAUSE = 3
HUMAN_GRACE = 120  # s
BOT_SAME_OPPONENT = 7200  # s
# Jamais deux parties contre des bots en même temps, tous nos comptes réunis (des services distincts) :
# un marqueur par partie dans ce dossier partagé. Au-delà de BOT_GAME_STALE, un marqueur est oublié (plantage).
BOT_GAMES_DIR = Path("~/.local/state/fun_fish/bot_games").expanduser()
BOT_GAME_STALE = 3600  # s
# Recherche d'adversaires (`--matchmaking`) : un compte resté libre MATCH_IDLE défie un bot en ligne, pour
# stabiliser son classement face à des adversaires variés. Mêmes règles que les défis reçus (place
# commune, pauses, même bot espacé) ; en plus, MATCH_SPACING entre deux défis lancés par nos comptes
# réunis. La cadence est celle où notre classement est le plus incertain (rd de Lichess) ; l'adversaire,
# un bot de niveau proche (MATCH_RANGE), ni l'un des nôtres, ni un bot qui nous a refusé depuis peu.
MATCH_IDLE = 600  # s sans partie
MATCH_SPACING = 300  # s
MATCH_CHECK = 60  # s entre deux examens, en moyenne : tiré entre la moitié et une fois et demie, pour que
# nos comptes, qui se disputent le même créneau (MATCH_SPACING), ne soient pas toujours servis dans le même ordre
MATCH_RANGE = 250  # points de classement
MATCH_DECLINED = 86400  # s : un bot qui a refusé n'est pas redéfié avant
MATCH_WAIT = 30  # s : sans réponse, le défi est oublié (Lichess l'annule au bout de 20 s)
MATCH_CLOCKS = {"bullet": [(120, 1)], "blitz": [(180, 0), (180, 2), (300, 0)]}
OUR_BOTS = {"funfishbot", "crazyfishbot", "dubot", "capabot"}
# Les parties contre des bots sont amicales, dans les deux sens (défis lancés, défis reçus).
# L'échelle des bots sur Lichess n'a rien à voir avec celle des humains (des moteurs classés 1850 jouent
# à 96 % de précision) : un classement fait contre eux tromperait les humains qui nous choisissent.
BOT_RATED = False
MATCH_DEFAULT = 2000  # classement de référence d'un compte qui n'a encore joué aucune partie


def reference_rating(perfs: dict, speed: str) -> int:
    """Notre classement pour choisir un adversaire. Une cadence jamais jouée s'affiche à 3000 (rd 500) :
    on prend alors le classement le plus sûr (rd le plus bas) des cadences déjà jouées. Un classement
    provisoire compte au plus MATCH_DEFAULT : cinq victoires contre un seul joueur faible suffisent à
    le porter à 3000, et le bot irait défier les meilleurs moteurs."""
    def value(v):
        provisional = v.get("prov") or v.get("rd", 500) > PROVISIONAL_RD
        return min(v["rating"], MATCH_DEFAULT) if provisional else v["rating"]
    if perfs.get(speed, {}).get("games", 0):
        return value(perfs[speed])
    played = [v for k, v in perfs.items() if k in ("bullet", "blitz", "rapid", "classical") and v.get("games", 0)]
    return value(min(played, key=lambda v: v.get("rd", 500))) if played else MATCH_DEFAULT
PROVISIONAL_RD = 110  # au-delà, Lichess affiche le classement comme provisoire (« ? »)
CHAT_LATE = 120  # s : on relit le chat de la partie après sa fin, pour les messages d'adieu
QUICK_TIMEOUT = 3  # s : nulle, chat ; jamais au prix du coup à jouer
LOW_CLOCK_MS = 5_000  # sous ce temps restant, dernier recours : Stockfish peu profond plutôt que perdre au temps
# Nulles : seulement les nulles absolues, partie totalement fermée (voir dead_draw). Proposée après
# DRAW_STREAK coups de suite dans cet état, à partir du coup DRAW_FROM ; acceptée si la position l'est
# au moment de l'offre. (La probabilité de nulle de Stockfish ne suffit pas : 99 % pour toute position égale.)
DRAW_SCORE, DRAW_STREAK, DRAW_FROM, DRAW_DEPTH = 15, 8, 30, 16
DRAW_OFFER = ("La position est complètement bloquée : je vous propose la nulle.",
              "The position is completely locked: I offer a draw.")
GREETING = ("Bonjour ! Je suis Fun Fish : je préfère les coups qui ont du style aux coups parfaits.",
            "Hi! I'm Fun Fish: I prefer stylish moves to perfect ones. Have fun!")  # 140 caractères au plus
GREETINGS = {"crazy_fish": ("Salut à vous ! Je suis Crazy Fish : mes cavaliers vont où bon leur semble, et la raison suit… parfois.",
                            "Well met! I am Crazy Fish: my knights roam where fancy leads, and reason follows… sometimes. Have fun!"),
             "dubot": ("Bonjour ! Je suis DuBot : je cherche les positions où ça brûle, quitte à y laisser un pion ou deux.",
                       "Hi! I'm DuBot: I go looking for positions on fire, even if it costs me a pawn or two. Have fun!"),
             "capabot": ("Bonjour ! Je suis CapaBot : des positions claires, des pièces bien placées, et la patience d'attendre.",
                         "Hi! I'm CapaBot: clear positions, well-placed pieces, and all the patience in the world. Have fun!")}


def log(*parts) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Lichess:
    def __init__(self, token: str):
        self.token = token

    def _request(self, method: str, path: str, data: dict | None = None, timeout: float | None = 30):
        body = urllib.parse.urlencode(data).encode() if data is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(LICHESS + path, data=body, method=method,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "User-Agent": "fun_fish-bot"})
        return urllib.request.urlopen(req, timeout=timeout)

    def get(self, path: str) -> dict:
        with self._request("GET", path) as r:
            return json.load(r)

    def post(self, path: str, data: dict | None = None, *, quick: bool = False) -> bool:
        """`quick` : action facultative (nulle, chat) pendant une partie : un seul essai de QUICK_TIMEOUT s
        au plus, sans les reprises (60 s après un 429) réservées aux actions indispensables."""
        if quick:
            try:
                with self._request("POST", path, data, timeout=QUICK_TIMEOUT):
                    return True
            except (urllib.error.HTTPError, OSError) as exc:
                log("action facultative abandonnée", path, getattr(exc, "code", exc))
                return False
        for attempt in range(3):
            try:
                with self._request("POST", path, data):
                    return True
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(60)
                    continue
                log("refusé par Lichess", path, exc.code, exc.read()[:200])
                return False
            except OSError as exc:
                log("erreur réseau", path, exc)
                time.sleep(2 + attempt * 3)
        return False

    def create(self, path: str, data: dict) -> dict | None:
        """POST qui renvoie un objet (création d'un défi), sans reprise. None : refus net de Lichess.
        OSError : issue inconnue (réseau, réponse illisible), le défi existe peut-être."""
        try:
            with self._request("POST", path, data) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            log("refusé par Lichess", path, exc.code, exc.read()[:200])
            return None
        except ValueError as exc:
            raise OSError(f"réponse illisible : {exc}") from exc

    def lines(self, path: str) -> list[dict]:
        """Une réponse ndjson courte et complète (liste de bots en ligne)."""
        with self._request("GET", path) as r:
            return [json.loads(line) for raw in r if (line := raw.strip())]

    def stream(self, path: str):
        """Lignes JSON d'un flux ndjson (les lignes vides sont les « keep-alive »)."""
        with self._request("GET", path, timeout=None) as r:
            for raw in r:
                line = raw.strip()
                if line:
                    yield json.loads(line)


VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def locked(board: chess.Board) -> bool:
    """Mur de pions infranchissable : seulement des rois et des pions, aucun pion ne peut avancer ni
    prendre, et aucun roi ne peut atteindre un pion adverse non protégé (en contournant le mur sans
    passer par une case attaquée par un pion adverse)."""
    if board.pieces_mask(chess.PAWN, chess.WHITE) == 0 or any(
            board.pieces_mask(p, c) for p in VALUES for c in chess.COLORS):
        return False
    if board.has_legal_en_passant():  # une prise en passant ouvre le mur
        return False
    for color in chess.COLORS:
        side = board.copy(stack=False)
        side.turn, side.ep_square = color, None  # la case en passant ne vaut que pour le camp au trait
        if any(side.piece_type_at(m.from_square) == chess.PAWN for m in side.generate_pseudo_legal_moves()):
            return False
    pawns = board.pieces_mask(chess.PAWN, chess.WHITE) | board.pieces_mask(chess.PAWN, chess.BLACK)
    for color in chess.COLORS:
        enemy = not color
        guarded = chess.SquareSet()  # cases que les pions adverses attaquent : interdites au roi
        for sq in board.pieces(chess.PAWN, enemy):
            guarded |= board.attacks(sq)
        prey = [sq for sq in board.pieces(chess.PAWN, enemy) if sq not in guarded]  # pions adverses non protégés
        start = board.king(color)
        seen, todo = {start}, [start]
        while todo:  # toutes les cases que le roi peut atteindre
            sq = todo.pop()
            if any(chess.square_distance(sq, p) == 1 for p in prey):
                return False  # il peut venir manger un pion : le mur n'est pas fermé
            for nxt in chess.SquareSet(chess.BB_KING_ATTACKS[sq]):
                if nxt not in seen and not (pawns & chess.BB_SQUARES[nxt]) and nxt not in guarded:
                    seen.add(nxt)
                    todo.append(nxt)
    return True


def bare(board: chess.Board) -> bool:
    """Plus de pions et un matériel identique et réduit (tour contre tour, fou contre cavalier…)."""
    if board.pieces_mask(chess.PAWN, chess.WHITE) | board.pieces_mask(chess.PAWN, chess.BLACK):
        return False
    material = [sum(v * len(board.pieces(p, c)) for p, v in VALUES.items()) for c in chess.COLORS]
    return material[0] == material[1] <= 5


def dead_draw(board: chess.Board, analyser) -> bool:
    """Nulle absolue : partie totalement fermée (mur de pions, ou presque plus rien sur l'échiquier) et
    Stockfish à ±DRAW_SCORE. Une position simplement égale, où il reste à creuser, ne compte pas."""
    if board.fullmove_number < DRAW_FROM or board.is_game_over() or not (locked(board) or bare(board)):
        return False
    lines = analyser.analyse(board, multipv=1, depth=DRAW_DEPTH)
    return bool(lines) and abs(lines[0].score) <= DRAW_SCORE


class DrawSense:
    """Quand proposer ou accepter la nulle (une seule proposition par tranche de 20 coups)."""

    def __init__(self):
        self.streak = 0
        self.offered_at = -100

    def offer(self, board: chess.Board, dead: bool) -> bool:
        """À notre tour : faut-il proposer la nulle ?"""
        self.streak = self.streak + 1 if dead else 0
        if self.streak >= DRAW_STREAK and board.fullmove_number - self.offered_at >= 20:
            self.offered_at = board.fullmove_number
            return True
        return False


class BotGame(threading.Thread):
    def __init__(self, bot: "Bot", game_id: str):
        super().__init__(daemon=True, name=f"partie-{game_id}")
        self.bot, self.id = bot, game_id
        self.over = False  # fin confirmée par Lichess (et non flux coupé ou erreur)

    def run(self) -> None:
        from fun_fish.analysis import Analyser
        from fun_fish.game import Game

        try:
            with Analyser(self.bot.stockfish, threads=1, hash_mb=64) as analyser:
                self.play(analyser, Game)
        except Exception as exc:  # une partie qui plante ne doit pas arrêter le bot
            log(self.id, "erreur", repr(exc))
        finally:
            self.bot.finished(self.id, self.over)

    def play(self, analyser, Game) -> None:
        from fun_fish import personalities

        from fun_fish.pace import Pace

        api, game, ours, greeted, pace = self.bot.api, None, None, False, Pace()
        draws, answered = DrawSense(), None  # answered : nombre de coups au moment de la dernière offre traitée
        for event in api.stream(f"/api/bot/game/stream/{self.id}"):
            if event.get("type") == "gameFull":
                if event.get("initialFen", "startpos") != "startpos":
                    self.over = api.post(f"/api/bot/game/{self.id}/abort")
                    return
                ours = chess.WHITE if event["white"].get("id") == self.bot.me else chess.BLACK
                agent = personalities.create(self.bot.personality, rng=random.Random(), **self.bot.params)
                game = Game(agent, human_color=not ours)
                opponent = event["black" if ours == chess.WHITE else "white"]
                log(self.id, "contre", opponent.get("name", "?"), opponent.get("rating", "?"),
                    "avec les", "Blancs" if ours == chess.WHITE else "Noirs")
                state = event["state"]
            elif event.get("type") == "gameState":
                state = event
            else:
                if event.get("type") == "chatLine" and event.get("username", "").lower() != self.bot.me:
                    log(self.id, "chat de", event.get("username", "?"), ":", event.get("text", ""))
                continue
            if game is None:
                continue
            if not greeted:
                for text in GREETINGS.get(self.bot.personality, GREETING):
                    api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text})
                greeted = True
            moves = state.get("moves", "").split()
            for uci in moves[len(game.board.move_stack):]:  # rattrape les coups (reconnexion comprise)
                game.board.push_uci(uci)
            if state.get("status", "started") != "started":
                self.over = True
                self.save(game, state, analyser)
                return
            clock = state.get("wtime" if ours == chess.WHITE else "btime", 10**9)
            offered = state.get("bdraw" if ours == chess.WHITE else "wdraw", False)
            if offered and answered != len(moves) and clock >= 2 * LOW_CLOCK_MS:
                answered = len(moves)  # refuser, c'est simplement jouer : l'offre tombe avec notre coup
                accept = dead_draw(game.board, analyser)
                log(self.id, "nulle proposée par l'adversaire :", "acceptée" if accept else "on joue")
                if accept and api.post(f"/api/bot/game/{self.id}/draw/yes", quick=True):
                    continue
            if game.board.turn == ours and not game.over:
                offering = clock >= 2 * LOW_CLOCK_MS and draws.offer(game.board, dead_draw(game.board, analyser))
                increment = state.get("winc" if ours == chess.WHITE else "binc", 0)
                if clock < LOW_CLOCK_MS:
                    move = analyser.analyse(game.board, multipv=1, depth=6)[0].move
                    game.board.push(move)
                else:
                    agent = game.personality
                    effort = pace.effort(clock / 1000, increment / 1000, game.board.fullmove_number)
                    if hasattr(agent, "effort"):
                        agent.effort = effort
                    start = time.monotonic()
                    move = game.play_engine(analyser).move
                    pace.observe(time.monotonic() - start, effort)
                # la proposition de nulle voyage avec le coup (une seule requête) ; le message suit, sans le retarder
                suffix = "?offeringDraw=true" if offering else ""
                if not api.post(f"/api/bot/game/{self.id}/move/{move.uci()}{suffix}"):
                    game.board.pop()  # coup refusé : on se resynchronisera sur le prochain état
                elif offering:
                    log(self.id, "propose la nulle (position bloquée)")
                    for text in DRAW_OFFER:
                        api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text}, quick=True)

    def save(self, game, state: dict, analyser) -> None:
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        export = game.debug_export(engine=analyser.engine_name)
        export["anomalies"] = getattr(game.personality, "anomalies", [])
        export["lichess"] = {"id": self.id, "url": f"{LICHESS}/{self.id}", "status": state.get("status"),
                             "winner": state.get("winner")}
        export["chat"] = None  # lu à part : une lecture lente ne doit pas retarder la libération de la place
        path = GAMES_DIR / f"{time.strftime('%Y-%m-%d')}-{self.id}.json.gz"
        write_export(path, export)
        log(self.id, "terminée :", state.get("status"), "gagnant :", state.get("winner", "aucun"))
        self.chat_lock = threading.Lock()  # la lecture immédiate et la relecture tardive n'écrivent pas ensemble
        self.chat_now = threading.Thread(target=self.late_chat, args=(path, export, False), daemon=True)
        self.chat_now.start()
        later = threading.Timer(CHAT_LATE, self.late_chat, (path, export))  # le « gg » vient souvent après
        later.daemon = True
        later.start()

    def chat(self) -> list | None:
        """Le chat de la partie (les retours des joueurs), relu chez Lichess ; None s'il est injoignable."""
        try:
            return self.bot.api.get(f"/api/bot/game/{self.id}/chat")
        except (OSError, ValueError) as exc:
            log(self.id, "chat illisible :", exc)
            return None

    def late_chat(self, path, export: dict, announce: bool = True) -> None:
        """Relit le chat (hors du fil de la partie) et complète l'export s'il a grandi. `announce` : mettre au
        journal les nouveaux messages de l'adversaire (ceux de la partie y sont déjà, notés en direct)."""
        try:
            self._late_chat(path, export, announce)
        except Exception as exc:  # fil d'arrière-plan : une erreur ici ne doit rien casser d'autre
            log(self.id, "chat non enregistré :", repr(exc))

    def _late_chat(self, path, export: dict, announce: bool) -> None:
        chat = self.chat()
        with self.chat_lock:
            if chat is None or chat == export.get("chat"):
                return
            write_export(path, {**export, "chat": chat})  # en cas d'échec, l'export et son chat restent tels quels
            new, export["chat"] = chat[len(export.get("chat") or []):], chat
        for line in new if announce else ():
            if line.get("user", "").lower() != self.bot.me:
                log(self.id, "chat de", line.get("user", "?"), "(après la partie) :", line.get("text", ""))


def write_export(path, export: dict) -> None:
    """Écrit l'export d'une partie d'un coup : fichier voisin, puis remplacement. Une écriture qui échoue
    (disque plein…) laisse l'ancienne version intacte."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def blocked_players() -> set[str]:
    """Les comptes dont on refuse les défis (triche, farming...), relus à chaque défi : sans redémarrage."""
    try:  # un octet invalide ne doit pas bloquer tous les défis : on garde les noms lisibles
        text = BLOCKED_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {name.lower() for line in text.splitlines() if (name := line.split("#", 1)[0].strip())}


def bot_game_running() -> bool:
    """Un de nos comptes joue-t-il déjà contre un bot (ou s'apprête-t-il à le faire) ?"""
    try:
        marks = list(BOT_GAMES_DIR.glob("*--*"))
    except OSError:
        return False
    now = time.time()
    for mark in marks:
        try:
            if now - mark.stat().st_mtime < BOT_GAME_STALE:
                return True
        except OSError:  # effacé entre-temps
            pass
    return False


@contextlib.contextmanager
def bot_games_lock():
    """Verrou commun à tous nos comptes (des processus distincts), le temps de vérifier puis réserver
    la place : sans lui, deux comptes pourraient voir la place libre et l'accepter ensemble.
    Un verrou de plus de 10 s est celui d'un processus mort : on le reprend."""
    BOT_GAMES_DIR.mkdir(parents=True, exist_ok=True)
    lock = BOT_GAMES_DIR / "verrou"
    for _ in range(200):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            time.sleep(0.01)
    else:
        raise OSError("verrou des parties contre des bots toujours pris")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def reserve_bot_game(account: str, game_id: str, spacing: float = 0) -> bool:
    """Réserve la place commune (une seule partie contre un bot à la fois), atomiquement. `spacing` :
    pour un défi lancé, au moins ce délai depuis le précédent défi lancé par l'un de nos comptes."""
    try:
        with bot_games_lock():
            if bot_game_running():
                return False
            if spacing:
                last = BOT_GAMES_DIR / "dernier-defi"
                if last.exists() and time.time() - last.stat().st_mtime < spacing:
                    return False
                last.touch()
            (BOT_GAMES_DIR / f"{account}--{game_id}").touch()
            return True
    except OSError as exc:  # verrou occupé trop longtemps, dossier inutilisable : dans le doute, pas de bot
        log("réservation de partie contre un bot impossible :", exc)
        return False


def bot_game_marks(account: str) -> dict[str, float]:
    """Nos parties contre des bots marquées : identifiant → âge du marqueur (s)."""
    try:
        marks = list(BOT_GAMES_DIR.glob(f"{account}--*"))
    except OSError:
        return {}
    found, now = {}, time.time()
    for mark in marks:
        try:
            found[mark.name.split("--", 1)[1]] = now - mark.stat().st_mtime
        except OSError:
            pass
    return found


def rename_bot_game(account: str, old: str, new: str) -> None:
    try:
        (BOT_GAMES_DIR / f"{account}--{old}").replace(BOT_GAMES_DIR / f"{account}--{new}")
    except OSError as exc:
        log("marqueur de partie contre un bot impossible à renommer :", exc)


def unmark_bot_games(account: str, game_id: str = "*") -> None:
    try:
        for mark in BOT_GAMES_DIR.glob(f"{account}--{game_id}"):
            mark.unlink(missing_ok=True)
    except OSError:
        pass


class Bot:
    def __init__(self, token: str, stockfish, personality: str, params: dict, max_games: int = 2,
                 accept_bots: bool = False, matchmaking: bool = False):
        self.api = Lichess(token)
        # Maia peut prendre les cœurs libres : avec une seule partie à la fois, 2 fois plus vite sur 2 cœurs
        os.environ.setdefault("FUN_FISH_MAIA_THREADS", str(max(1, min(4, (os.cpu_count() or 1) // max(1, max_games)))))
        self.stockfish, self.personality, self.params = stockfish, personality, params
        self.max_games, self.accept_bots = max_games, accept_bots
        # Défis de bots (horloge time.monotonic) : pas de bot avant bot_free_at ; parties en cours contre
        # des bots (début) ; dernier défi accepté de chaque bot adverse.
        self.bot_free_at = 0.0
        self.bot_games: dict[str, float] = {}
        self.bot_opponents: dict[str, float] = {}
        # Recherche d'adversaires : défi lancé en attente (id, heure, adversaire ; id "" tant que Lichess
        # ne l'a pas donné, ou si sa réponse s'est perdue) ; refus et annulations reçus pendant ce temps ;
        # bots qui ont refusé ; depuis quand le compte est libre.
        self.matchmaking = matchmaking
        self.outgoing: tuple[str, float, str] | None = None
        self.early_drops: dict[str, bool] = {}
        self.declined: dict[str, float] = {}
        self.idle_since = time.monotonic()
        # Défis acceptés dont la partie n'a pas commencé : None tant que le POST d'acceptation est en
        # cours (il peut durer : 429, réseau), puis l'heure de sa réponse.
        self.accepting: dict[str, float | None] = {}
        self.games: dict[str, BotGame] = {}
        self.lock = threading.Lock()
        self.me = ""

    def finished(self, game_id: str, over: bool = True) -> None:
        """Fin du fil de la partie. Sans fin confirmée (flux coupé, erreur), une partie contre un bot
        garde sa place et la pause de sécurité : elle continue peut-être, et `gameStart` la reprendra."""
        now = time.monotonic()
        with self.lock:
            self.games.pop(game_id, None)
            self.accepting.pop(game_id, None)
            self.idle_since = now
            if not over:
                return
            start = self.bot_games.pop(game_id, None)
            if start is not None:
                self.bot_free_at = now + BOT_PAUSE * (now - start)
            else:  # un humain : il a le temps d'enchaîner avant qu'un bot prenne la place
                self.bot_free_at = max(self.bot_free_at, now + HUMAN_GRACE)
        unmark_bot_games(self.me, game_id)

    def resume_bot_games(self) -> None:
        """Au démarrage : nos marqueurs de parties encore en cours sur Lichess (le service a redémarré
        en pleine partie) sont gardés et suivis ; les autres parties sont finies."""
        marks = bot_game_marks(self.me)
        if not marks:
            return
        try:
            playing = {g.get("gameId") for g in self.api.get("/api/account/playing").get("nowPlaying", [])}
        except (OSError, ValueError) as exc:  # dans le doute, on garde tout (oublié au bout d'une heure)
            log("parties en cours inconnues :", exc)
            playing = set(marks)
        now = time.monotonic()
        for game_id, age in marks.items():
            if game_id in playing:
                self.bot_games[game_id] = now - age
                self.bot_free_at = max(self.bot_free_at, now + BOT_GAME_STALE)
            else:
                unmark_bot_games(self.me, game_id)

    def decline_reason(self, challenge: dict) -> str | None:
        if DRAIN_PATH.exists():
            return "later"
        challenger = challenge.get("challenger", {})
        if {challenger.get("id", ""), challenger.get("name", "").lower()} & blocked_players():
            return "generic"
        if challenge.get("variant", {}).get("key") != "standard":
            return "standard"
        clock = challenge.get("timeControl", {})
        if clock.get("type") != "clock" or clock.get("limit", 0) + 40 * clock.get("increment", 0) > MAX_ESTIMATE:
            return "tooSlow"
        if clock.get("limit", 0) < MIN_LIMIT or (clock.get("limit", 0) < 180 and clock.get("increment", 0) < 1):
            return "tooFast"
        if challenger.get("title") == "BOT" and challenge.get("rated") and not BOT_RATED:
            return "casual"
        if self.rationed(challenge):
            if clock.get("limit", 0) + 40 * clock.get("increment", 0) > BOT_MAX_ESTIMATE:
                return "tooSlow"
            now = time.monotonic()
            last = self.bot_opponents.get(challenge.get("challenger", {}).get("id", ""))
            if now < self.bot_free_at or (last is not None and now - last < BOT_SAME_OPPONENT):
                return "later"
            if bot_game_running():  # contrôle rapide ; la réservation atomique est dans handle_challenge
                return "later"
        with self.lock:
            if len(self.games) + len(self.accepting_now()) >= self.max_games:
                return "later"
        return None

    def accepting_now(self) -> dict[str, float | None]:
        """Acceptations en cours, ou confirmées sans `gameStart` (sous verrou). Elles ne s'oublient pas
        seules : `reconcile_accepting` les vérifie auprès de Lichess."""
        return self.accepting

    def reconcile_accepting(self) -> bool:
        """Acceptations confirmées depuis une minute sans `gameStart` (flux coupé ou en retard) : une partie
        active sur Lichess est jouée, les autres sont oubliées. Sans réponse de Lichess, elles restent (False)."""
        now = time.monotonic()
        with self.lock:
            stale = [k for k, t in self.accepting.items() if t is not None and now - t >= 60]
        if not stale:
            return True
        try:
            playing = {g.get("gameId") for g in self.api.get("/api/account/playing").get("nowPlaying", [])}
        except (OSError, ValueError) as exc:
            log("acceptations sans gameStart, parties en cours inconnues :", exc)
            return False
        for game_id in stale:
            if game_id in playing:
                log(game_id, ": acceptée, commencée sans gameStart, reprise")
                self.start_game(game_id)
            else:
                with self.lock:
                    self.accepting.pop(game_id, None)
        return True

    def handle_challenge(self, c: dict) -> None:
        """Accepte ou refuse un défi. Le quota des bots n'est compté qu'une fois l'acceptation confirmée
        par Lichess : un échec (défi annulé entre-temps, réseau) ne bloque pas les bots suivants."""
        reason = self.decline_reason(c)
        who = c.get("challenger", {}).get("name", "?")
        what = describe(c)
        if reason:
            log("défi de", who, "refusé :", reason, what)
            self.api.post(f"/api/challenge/{c['id']}/decline", {"reason": reason})
            return
        rationed = self.rationed(c)
        if rationed and not reserve_bot_game(self.me, c["id"]):  # un autre compte vient de la prendre
            log("défi de", who, "refusé : later (un autre compte joue contre un bot)", what)
            self.api.post(f"/api/challenge/{c['id']}/decline", {"reason": "later"})
            return
        cancelled = rationed or self.cancel_outgoing()  # pour un humain : notre défi lancé lui cède la place
        with self.lock:  # même verrou que la recherche : jamais un défi lancé et une acceptation ensemble
            free = cancelled and self.outgoing is None
            if free:
                self.accepting[c["id"]] = None  # POST en cours : n'expire pas
        if not free:
            log("défi de", who, "refusé : later (notre défi à un bot est en cours)", what)
            self.api.post(f"/api/challenge/{c['id']}/decline", {"reason": "later"})
            if rationed:
                unmark_bot_games(self.me, c["id"])
            return
        log("défi de", who, "accepté", what)
        ok = False
        try:
            ok = self.api.post(f"/api/challenge/{c['id']}/accept")  # False : Lichess a refusé, ou réseau
        finally:
            with self.lock:
                if not ok:
                    self.accepting.pop(c["id"], None)
                elif c["id"] in self.accepting:  # gameStart pas encore là : une minute pour arriver
                    self.accepting[c["id"]] = time.monotonic()
            if not ok:
                if rationed:
                    unmark_bot_games(self.me, c["id"])  # place rendue
        if not ok or not rationed:
            return
        now = time.monotonic()
        self.bot_games[c["id"]] = self.bot_opponents[c.get("challenger", {}).get("id", "")] = now
        self.bot_free_at = now + BOT_GAME_STALE  # au cas où la fin de partie ne serait jamais vue

    def cancel_outgoing(self) -> bool:
        """Annule notre défi lancé (place laissée à un humain). False : impossible (en cours de création,
        ou déjà accepté)."""
        with self.lock:
            pending = self.outgoing
        if pending is None:
            return True
        if not pending[0] or not self.api.post(f"/api/challenge/{pending[0]}/cancel"):
            return False
        self.drop_outgoing(pending[0])
        log("défi à", pending[2], "annulé : un humain arrive")
        return True

    def drop_outgoing(self, challenge_id: str, declined: bool = False) -> None:
        """Notre défi est refusé, annulé ou expiré : la place est rendue. Reçu avant que Lichess ait
        donné l'identifiant de notre défi, l'événement est gardé pour `seek`."""
        with self.lock:
            if self.outgoing is not None and self.outgoing[0] == "" and challenge_id:
                self.early_drops[challenge_id] = declined
                return
            if self.outgoing is None or self.outgoing[0] != challenge_id:
                return
            opponent, self.outgoing = self.outgoing[2], None
            if declined:
                self.declined[opponent] = time.monotonic()
        unmark_bot_games(self.me, challenge_id or "sortant")

    def settle_outgoing(self, pending: tuple[str, float, str]) -> bool:
        """Défi resté sans nouvelles (pas de réponse, réponse de création perdue, flux coupé) : avant de
        rendre la place, Lichess dit si une partie a commencé contre cet adversaire ; elle est alors suivie
        et jouée. Sans réponse de Lichess, la place reste prise (False) : on réessaiera."""
        try:
            playing = self.api.get("/api/account/playing").get("nowPlaying", [])
        except (OSError, ValueError) as exc:
            log("défi sans nouvelles, parties en cours inconnues :", exc)
            return False
        game_id = next((g.get("gameId") for g in playing if g.get("gameId") == pending[0] or (
            not pending[0] and g.get("opponent", {}).get("id") == pending[2])), None)
        if not game_id:
            self.drop_outgoing(pending[0])
            return True
        with self.lock:
            if self.outgoing != pending:  # réglé entre-temps (gameStart, refus)
                return True
            if not pending[0]:
                rename_bot_game(self.me, "sortant", game_id)
            self.outgoing = None
            self.accepting[game_id] = None  # compte occupé sans interruption jusqu'à `start_game`
            self.bot_games[game_id] = pending[1]
            self.bot_free_at = time.monotonic() + BOT_GAME_STALE
        log(f"défi à {pending[2]} : partie {game_id} commencée sans nouvelles, reprise")
        self.start_game(game_id)
        return True

    def ready_to_seek(self, now: float) -> bool:
        """Compte libre depuis assez longtemps, sans défi en cours ni pause (sous verrou)."""
        return (not self.games and not self.accepting_now() and self.outgoing is None
                and not DRAIN_PATH.exists() and now >= self.bot_free_at and now - self.idle_since >= MATCH_IDLE)

    def seek(self) -> str | None:
        """Recherche d'adversaires : lance un défi si le compte est libre depuis longtemps. Renvoie son id."""
        now = time.monotonic()
        with self.lock:
            pending = self.outgoing
        if pending and now - pending[1] > MATCH_WAIT and not self.settle_outgoing(pending):
            return None
        if not self.reconcile_accepting():
            return None
        with self.lock:
            if not self.ready_to_seek(now):
                return None
        perfs = self.api.get("/api/account").get("perfs", {})
        speed = max(MATCH_CLOCKS, key=lambda k: (perfs.get(k, {}).get("rd", 500), random.random()))
        rating = reference_rating(perfs, speed)

        def fits(bot: dict) -> bool:
            name = bot.get("id", "")
            last_played, last_declined = self.bot_opponents.get(name), self.declined.get(name)
            return (name not in OUR_BOTS | {self.me} and not bot.get("disabled") and not bot.get("tosViolation")
                    and abs(bot.get("perfs", {}).get(speed, {}).get("rating", -10**4) - rating) <= MATCH_RANGE
                    and (last_played is None or now - last_played >= BOT_SAME_OPPONENT)
                    and (last_declined is None or now - last_declined >= MATCH_DECLINED)
                    and name not in blocked_players())
        candidates = [b["id"] for b in self.api.lines("/api/bot/online?nb=200") if fits(b)]
        if not candidates:
            log(f"recherche d'adversaire ({speed}, {rating}) : aucun bot disponible")
            return None
        opponent = random.choice(candidates)
        now = time.monotonic()
        with self.lock:  # l'état a pu changer pendant les lectures (humain accepté, vidange) : on revérifie
            if not self.ready_to_seek(now):
                return None
            self.outgoing, self.early_drops = ("", now, opponent), {}  # dès lors, humains et bots attendent
        if not reserve_bot_game(self.me, "sortant", spacing=MATCH_SPACING):
            with self.lock:
                self.outgoing = None
            return None
        limit, increment = random.choice(MATCH_CLOCKS[speed])
        try:
            created = self.api.create(f"/api/challenge/{opponent}", {
                "rated": str(BOT_RATED).lower(), "clock.limit": limit, "clock.increment": increment, "color": "random",
                "variant": "standard"})
        except OSError as exc:  # le défi existe peut-être : place gardée jusqu'à MATCH_WAIT (`game_started`)
            log(f"défi à {opponent} : issue inconnue ({exc})")
            with self.lock:
                self.bot_opponents[opponent] = now
            return None
        game_id = (created or {}).get("id")
        with self.lock:
            if self.outgoing is None or self.outgoing[0] != "":  # déjà commencé (`game_started`)
                self.bot_opponents[opponent] = now
                return game_id
            if not game_id:  # refus net de Lichess (compte fermé aux défis, limite atteinte) : on passe
                self.outgoing = None
                self.declined[opponent] = now
                unmark_bot_games(self.me, "sortant")
                return None
            self.bot_opponents[opponent] = now
            rename_bot_game(self.me, "sortant", game_id)
            self.outgoing = (game_id, now, opponent)
            early = self.early_drops.pop(game_id, None)
        if early is not None:  # refusé ou annulé avant même la réponse de Lichess
            self.drop_outgoing(game_id, declined=early)
            return None
        log(f"défi lancé à {opponent} ({limit // 60}+{increment} {speed}, notre classement {rating})")
        return game_id

    def watch_forever(self) -> None:
        """Chaque minute : les acceptations sans gameStart, puis la recherche d'adversaires si elle est active."""
        while True:
            time.sleep(random.uniform(MATCH_CHECK / 2, MATCH_CHECK * 1.5))
            try:
                self.seek() if self.matchmaking else self.reconcile_accepting()
            except Exception as exc:  # jamais au prix du bot
                log("surveillance :", repr(exc))

    def game_started(self, game_id: str) -> None:
        """`gameStart`. Pendant la création de notre défi (identifiant pas encore connu, ou réponse perdue),
        humains et bots sont refusés : une partie qui commence alors est forcément la nôtre."""
        with self.lock:
            if self.outgoing is not None and self.outgoing[0] in (game_id, ""):  # notre défi a été accepté
                if self.outgoing[0] == "":
                    rename_bot_game(self.me, "sortant", game_id)
                self.outgoing = None
                now = time.monotonic()
                self.bot_games[game_id] = now
                self.bot_free_at = now + BOT_GAME_STALE
        self.start_game(game_id)

    def rationed(self, challenge: dict) -> bool:
        """Un défi d'un autre bot, soumis aux limites (sauf `--accept-bots`)."""
        return challenge.get("challenger", {}).get("title") == "BOT" and not self.accept_bots

    def start_game(self, game_id: str) -> None:
        with self.lock:
            if game_id in self.games:
                return
            thread = self.games[game_id] = BotGame(self, game_id)
            self.accepting.pop(game_id, None)
            self.idle_since = time.monotonic()
        thread.start()

    def run(self) -> None:
        account = self.api.get("/api/account")
        self.me = account["id"]
        if account.get("title") != "BOT":
            raise SystemExit(f"Le compte {account['username']} n'est pas un compte BOT "
                             "(voir `fun-fish lichess-bot --upgrade`).")
        log(f"connecté en tant que {account['username']} — {self.personality}, {self.max_games} parties à la fois")
        self.resume_bot_games()  # marqueurs laissés par un arrêt : parties encore en cours, ou finies
        threading.Thread(target=self.watch_forever, daemon=True, name="surveillance").start()
        while True:
            try:
                for event in self.api.stream("/api/stream/event"):
                    kind = event.get("type")
                    if kind == "challenge":
                        c = event["challenge"]
                        if c.get("challenger", {}).get("id") == self.me:
                            continue
                        self.handle_challenge(c)
                    elif kind == "gameStart":
                        self.game_started(event["game"]["gameId"] if "gameId" in event["game"] else event["game"]["id"])
                    elif kind in ("challengeDeclined", "challengeCanceled"):
                        c = event.get("challenge", {})
                        if kind == "challengeDeclined" and c.get("challenger", {}).get("id") == self.me:
                            log("défi refusé par", c.get("destUser", {}).get("name", "?"), ":",
                                c.get("declineReasonKey", "?"))  # (pas l'écho de nos propres refus)
                        self.drop_outgoing(c.get("id", ""), declined=kind == "challengeDeclined")
            except (OSError, ValueError) as exc:
                log("flux interrompu, reconnexion dans 10 s :", exc)
                time.sleep(10)


def describe(challenge: dict) -> str:
    """La cadence d'un défi, pour le journal : « (3+2 blitz, bot) »."""
    clock = challenge.get("timeControl", {})
    pace = f"{clock.get('limit', 0) / 60:g}+{clock.get('increment', 0)}" if clock.get("type") == "clock" \
        else clock.get("type", "?")
    bot = ", bot" if challenge.get("challenger", {}).get("title") == "BOT" else ""
    return f"({pace} {challenge.get('speed', '?')}{bot})"


def read_token(path: Path = TOKEN_PATH) -> str:
    token = os.environ.get("LICHESS_BOT_TOKEN") or (path.read_text().strip() if path.exists() else "")
    if not token:
        raise SystemExit(f"Jeton introuvable : mettez-le dans {path} (chmod 600) ou dans LICHESS_BOT_TOKEN.")
    return token


def upgrade(token: str) -> None:
    """Passe le compte en BOT : définitif, et seulement pour un compte qui n'a joué aucune partie."""
    api = Lichess(token)
    account = api.get("/api/account")
    if account.get("title") == "BOT":
        print(f"{account['username']} est déjà un compte BOT.")
        return
    if api.post("/api/bot/account/upgrade"):
        print(f"{account['username']} est maintenant un compte BOT.")
