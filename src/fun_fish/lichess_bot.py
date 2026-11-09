"""Passerelle Lichess : une personnalité joue sur un compte BOT (API bot de Lichess).

Accepte les défis de parties standard d'humains (blitz, rapide, classique), au plus `max_games`
à la fois, joue chaque partie dans son propre fil avec son propre Stockfish, et garde un export
de debug compressé de chaque partie (même format que le bouton « Export debug »).
Bibliothèque standard seulement : pas de dépendance en plus.
"""

from __future__ import annotations

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
SPEEDS = ("blitz", "rapid", "classical")
LOW_CLOCK_MS = 15_000  # sous ce temps restant, il joue vite (Stockfish peu profond) plutôt que de perdre au temps
GREETING = ("Bonjour ! Je suis Fun Fish : je préfère les coups qui ont du style aux coups parfaits.",
            "Hi! I'm Fun Fish: I prefer stylish moves to perfect ones. Have fun!")  # 140 caractères au plus


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

    def post(self, path: str, data: dict | None = None) -> bool:
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

    def stream(self, path: str):
        """Lignes JSON d'un flux ndjson (les lignes vides sont les « keep-alive »)."""
        with self._request("GET", path, timeout=None) as r:
            for raw in r:
                line = raw.strip()
                if line:
                    yield json.loads(line)


class BotGame(threading.Thread):
    def __init__(self, bot: "Bot", game_id: str):
        super().__init__(daemon=True, name=f"partie-{game_id}")
        self.bot, self.id = bot, game_id

    def run(self) -> None:
        from fun_fish.analysis import Analyser
        from fun_fish.game import Game

        try:
            with Analyser(self.bot.stockfish, threads=1, hash_mb=64) as analyser:
                self.play(analyser, Game)
        except Exception as exc:  # une partie qui plante ne doit pas arrêter le bot
            log(self.id, "erreur", repr(exc))
        finally:
            self.bot.finished(self.id)

    def play(self, analyser, Game) -> None:
        from fun_fish import personalities

        api, game, ours, greeted = self.bot.api, None, None, False
        for event in api.stream(f"/api/bot/game/stream/{self.id}"):
            if event.get("type") == "gameFull":
                if event.get("initialFen", "startpos") != "startpos":
                    api.post(f"/api/bot/game/{self.id}/abort")
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
                continue
            if game is None:
                continue
            if not greeted:
                for text in GREETING:
                    api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text})
                greeted = True
            moves = state.get("moves", "").split()
            for uci in moves[len(game.board.move_stack):]:  # rattrape les coups (reconnexion comprise)
                game.board.push_uci(uci)
            if state.get("status", "started") != "started":
                self.save(game, state, analyser)
                return
            if game.board.turn == ours and not game.over:
                clock = state.get("wtime" if ours == chess.WHITE else "btime", 10**9)
                if clock < LOW_CLOCK_MS:
                    move = analyser.analyse(game.board, multipv=1, depth=6)[0].move
                    game.board.push(move)
                else:
                    move = game.play_engine(analyser).move
                if not api.post(f"/api/bot/game/{self.id}/move/{move.uci()}"):
                    game.board.pop()  # coup refusé : on se resynchronisera sur le prochain état

    def save(self, game, state: dict, analyser) -> None:
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        export = game.debug_export(engine=analyser.engine_name)
        export["lichess"] = {"id": self.id, "url": f"{LICHESS}/{self.id}", "status": state.get("status"),
                             "winner": state.get("winner")}
        with gzip.open(GAMES_DIR / f"{time.strftime('%Y-%m-%d')}-{self.id}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False)
        log(self.id, "terminée :", state.get("status"), "gagnant :", state.get("winner", "aucun"))


class Bot:
    def __init__(self, token: str, stockfish, personality: str, params: dict, max_games: int = 2,
                 accept_bots: bool = False):
        self.api = Lichess(token)
        self.stockfish, self.personality, self.params = stockfish, personality, params
        self.max_games, self.accept_bots = max_games, accept_bots
        self.games: dict[str, BotGame] = {}
        self.lock = threading.Lock()
        self.me = ""

    def finished(self, game_id: str) -> None:
        with self.lock:
            self.games.pop(game_id, None)

    def decline_reason(self, challenge: dict) -> str | None:
        if challenge.get("variant", {}).get("key") != "standard":
            return "standard"
        if challenge.get("speed") not in SPEEDS:
            return "tooFast" if challenge.get("speed") in ("ultraBullet", "bullet") else "timeControl"
        if challenge.get("challenger", {}).get("title") == "BOT" and not self.accept_bots:
            return "noBot"
        with self.lock:
            if len(self.games) >= self.max_games:
                return "later"
        return None

    def start_game(self, game_id: str) -> None:
        with self.lock:
            if game_id in self.games:
                return
            thread = self.games[game_id] = BotGame(self, game_id)
        thread.start()

    def run(self) -> None:
        account = self.api.get("/api/account")
        self.me = account["id"]
        if account.get("title") != "BOT":
            raise SystemExit(f"Le compte {account['username']} n'est pas un compte BOT "
                             "(voir `fun-fish lichess-bot --upgrade`).")
        log(f"connecté en tant que {account['username']} — {self.personality}, {self.max_games} parties à la fois")
        while True:
            try:
                for event in self.api.stream("/api/stream/event"):
                    kind = event.get("type")
                    if kind == "challenge":
                        c = event["challenge"]
                        if c.get("challenger", {}).get("id") == self.me:
                            continue
                        reason = self.decline_reason(c)
                        who = c.get("challenger", {}).get("name", "?")
                        if reason:
                            log("défi de", who, "refusé :", reason)
                            self.api.post(f"/api/challenge/{c['id']}/decline", {"reason": reason})
                        else:
                            log("défi de", who, "accepté")
                            self.api.post(f"/api/challenge/{c['id']}/accept")
                    elif kind == "gameStart":
                        self.start_game(event["game"]["gameId"] if "gameId" in event["game"] else event["game"]["id"])
            except (OSError, ValueError) as exc:
                log("flux interrompu, reconnexion dans 10 s :", exc)
                time.sleep(10)


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
