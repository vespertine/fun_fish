"""Passerelle Lichess : une personnalité joue sur un compte BOT (API bot de Lichess).

Accepte les défis de parties standard d'humains (bullet 1+1 et 2+1, blitz, rapides courtes), au plus `max_games`
à la fois, joue chaque partie dans son propre fil avec son propre Stockfish, et garde un export
de debug compressé de chaque partie (même format que le bouton « Export debug »).
Bibliothèque standard seulement : pas de dépendance en plus.
"""

from __future__ import annotations

import contextlib
import datetime
import gzip
import json
import os
import random
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

from fun_fish import fairplay, opponents

LICHESS = "https://lichess.org"
TOKEN_PATH = Path("~/.config/fun_fish/lichess_bot_token").expanduser()
GAMES_DIR = Path("~/.local/share/fun_fish/bot_games").expanduser()
DRAIN_PATH = Path("~/.config/fun_fish/drain").expanduser()  # présent : on finit ses parties sans en accepter (déploiement)
WISHLIST_PATH = Path("~/.config/fun_fish/wishlist").expanduser()  # JSON : des bots à jouer en priorité (`wished_bots`)
WISH_SPACING = 1800  # s entre deux parties contre un même bot souhaité (au lieu de BOT_SAME_OPPONENT)
BLOCKED_PATH = Path("~/.config/fun_fish/blocked").expanduser()  # un compte Lichess par ligne : défis toujours refusés
CAMPAIGN_PATH = Path("~/.config/fun_fish/campaign").expanduser()  # JSON : une campagne contre des bots (`Campaign`)
STABLE_FRESH = 600  # s : au-delà, la stabilité du classement est redemandée à Lichess
MAX_ESTIMATE = 900  # durée estimée par Lichess (base + 40 × incrément, en s) : blitz et rapides courtes
MIN_LIMIT = 60  # bullet à partir de 1+1 (essai du 20/11/2026) ; sans incrément, 3 min au moins : 1+0, 2+0 refusés
# Défis d'autres bots : le bot ne joue qu'une partie à la fois, il doit rester libre pour les humains.
# - Après une partie contre un bot, pause de BOT_PAUSE × sa durée réelle : au plus un quart du temps
#   contre des bots, et une partie courte coûte moins qu'une longue (2+1 : ~15 min ; 5+2 : ~35 min).
# - Après une partie contre un humain, HUMAN_GRACE pour qu'il puisse enchaîner (revanche, nouveau défi).
# - Le même bot, au plus une fois par BOT_SAME_OPPONENT : un seul bot ne prend pas toutes les places.
# - Jusqu'à 10+0 (BOT_MAX_ESTIMATE, même estimation que MAX_ESTIMATE) ; au-delà, « tooSlow ».
BOT_MAX_ESTIMATE = 600  # s
BOT_PAUSE = 3
# Pendant une campagne (voir `Campaign`), on veut stabiliser le classement vite : pause d'une fois la durée
# de la partie, et défi lancé après CAMPAIGN_IDLE au lieu de MATCH_IDLE. Les humains gardent HUMAN_GRACE.
CAMPAIGN_BOT_PAUSE = 1
CAMPAIGN_IDLE = 120  # s
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
# un bot à moins de MATCH_RANGE points, ni l'un des nôtres, ni un bot qui nous a refusé depuis peu. Pour
# mesurer notre niveau face à un éventail large (plus faibles comme plus forts), on tire d'abord une tranche
# d'écart de classement (MATCH_BAND) parmi celles qui ont un candidat, puis le bot dans cette tranche.
MATCH_IDLE = 600  # s sans partie
MATCH_SPACING = 300  # s
MATCH_CHECK = 60  # s entre deux examens, en moyenne : tiré entre la moitié et une fois et demie, pour que
# nos comptes, qui se disputent le même créneau (MATCH_SPACING), ne soient pas toujours servis dans le même ordre
MATCH_RANGE = 600  # points de classement
MATCH_BAND = 250  # écart qui sépare les tranches : plus faible (≤ −250), proche, plus fort (≥ +250)
MATCH_DECLINED = 86400  # s : un bot qui a refusé n'est pas redéfié avant
MATCH_WAIT = 30  # s : sans réponse, le défi est oublié (Lichess l'annule au bout de 20 s)
MATCH_CLOCKS = {"bullet": [(120, 1)], "blitz": [(180, 0), (180, 2), (300, 0)]}
# Pendant une campagne classée, un compte stable en bullet et en blitz joue aussi du rapide, le plus court
# (10+0, la limite de BOT_MAX_ESTIMATE) : la vitesse reste celle où le classement est le plus incertain.
CAMPAIGN_CLOCKS = {**MATCH_CLOCKS, "rapid": [(600, 0)]}
# Essai du 20/11/2026 : une recherche sur FAST_SHARE lance une partie amicale en 1+1 (FAST_CLOCKS), même en
# campagne classée, pour voir comment nos bots tiennent une cadence plus rapide (en 2+1, ~1,4 à 2 s par coup
# en médiane, sans perte au temps). Parties courtes : un large échantillon pour peu de temps.
FAST_SHARE = 0.25
FAST_CLOCKS = {"bullet": [(60, 1)]}
# Pendant une campagne, CAMPAIGN_BOT_GAMES parties contre des bots en même temps, nos comptes réunis (une
# seule sinon) : le test de charge du 17/11 a tenu cinq parties de blitz sans perte au temps.
CAMPAIGN_BOT_GAMES = 2
# Tour de rôle : un compte qui veut lancer un défi laisse un marqueur `attente.<compte>` (frais pendant
# MATCH_WAITING s) ; la place va au compte en attente dont le dernier défi lancé (`servi.<compte>`) est le
# plus ancien. Sans cela, le premier arrivé l'emporte, et un compte peut attendre des heures.
MATCH_WAITING = 2 * MATCH_CHECK  # s : plus que l'écart maximal entre deux examens (1,5 × MATCH_CHECK)
OUR_BOTS = {"funfishbot", "crazyfishbot", "dubot", "capabot", "sharpdubot"}
# Les parties contre des bots sont amicales, dans les deux sens (défis lancés, défis reçus).
# L'échelle des bots sur Lichess n'a rien à voir avec celle des humains (des moteurs classés 1850 jouent
# à 96 % de précision) : un classement fait contre eux tromperait les humains qui nous choisissent.
BOT_RATED = False
MATCH_DEFAULT = 2000  # classement de référence d'un compte qui n'a encore joué aucune partie


def band(candidates: list[tuple[str, int]], rng=random) -> list[str]:
    """Les candidats (nom, écart de classement) d'une tranche tirée au hasard parmi les tranches non
    vides (voir MATCH_BAND) : autant de défis aux plus faibles, aux proches et aux plus forts, quel que
    soit leur nombre en ligne."""
    tranches: dict[int, list[str]] = {}
    for name, gap in candidates:
        tranches.setdefault(-1 if gap <= -MATCH_BAND else 1 if gap >= MATCH_BAND else 0, []).append(name)
    return tranches[rng.choice(sorted(tranches))]


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
NO_START = 60  # s : sans premier coup de l'adversaire, on annule la partie (Lichess la laisse ouverte)
NO_START_RETRY = 15  # s : nouvel essai si l'annulation échoue (429, délai dépassé)
GAME_RETRY = 3  # s entre deux reconnexions au flux d'une partie (partie dPHJHW0U : perdue au temps, fil arrêté
# sur un 503 de Lichess)
GAME_RETRY_FOR = 600  # s sans aucun événement de la partie : on cesse de la suivre
UNCONFIRMED_RECHECK = 600  # s : partie encore en cours chez Lichess quand on cesse de la suivre, regardée à nouveau
CHAT_LATE = 120  # s : on relit le chat de la partie après sa fin, pour les messages d'adieu
QUICK_TIMEOUT = 3  # s : nulle, chat ; jamais au prix du coup à jouer
CALM_CLOCK_MS = 300_000  # sans incrément, en dessous de 5 min de base, pas de pièges calculés plus loin en finale
LOW_CLOCK_MS = 5_000  # sous ce temps restant, dernier recours : Stockfish peu profond plutôt que perdre au temps
# Profondeur de ce recours : 6, sauf quand il reste peu de pièces (rois compris), où chercher à 12 ne coûte
# presque rien et mène au mat. Partie rR9ELzeJ (13/11/2026) : dame et pion contre roi, 25 coups sans mater
# à profondeur 6 ; en 3+0, gagnée seulement parce que l'adversaire est tombé. Six finales simples : à profondeur 6, une tour contre roi
# finit nulle (50 coups) et les dames matent en 9 à 27 coups ; à 12, toutes matent, en 8 à 24 coups.
LOW_CLOCK_DEPTH, LOW_CLOCK_FEW, LOW_CLOCK_FEW_DEPTH = 6, 6, 12


def low_clock_depth(board: chess.Board) -> int:
    return LOW_CLOCK_FEW_DEPTH if len(board.piece_map()) <= LOW_CLOCK_FEW else LOW_CLOCK_DEPTH
# Nulles : seulement les nulles absolues, partie totalement fermée (voir dead_draw). Proposée après
# DRAW_STREAK coups de suite dans cet état, à partir du coup DRAW_FROM ; acceptée si la position l'est
# au moment de l'offre. (La probabilité de nulle de Stockfish ne suffit pas : 99 % pour toute position égale.)
DRAW_SCORE, DRAW_STREAK, DRAW_FROM, DRAW_DEPTH = 15, 8, 30, 16
DRAW_LOCKED = ("Mur de pions infranchissable, plus rien ne peut bouger : je propose la nulle.",
               "An impassable pawn wall, nothing can move: I offer a draw.")
PIECE_NAMES = {chess.ROOK: ("tour", "rook"), chess.BISHOP: ("fou", "bishop"), chess.KNIGHT: ("cavalier", "knight")}
GREETING = ("Bonjour ! Je suis Fun Fish : je préfère les coups qui ont du style aux coups parfaits.",
            "Hi! I'm Fun Fish: I prefer stylish moves to perfect ones. Have fun!")  # 140 caractères au plus
GREETINGS = {"crazy_fish": ("Salut à vous ! Je suis Crazy Fish : mes cavaliers vont où bon leur semble, et la raison suit… parfois.",
                            "Well met! I am Crazy Fish: my knights roam where fancy leads, and reason follows… sometimes. Have fun!"),
             "dubot": ("Bonjour ! Je suis DuBot : je cherche les positions où ça brûle, quitte à y laisser un pion ou deux.",
                       "Hi! I'm DuBot: I go looking for positions on fire, even if it costs me a pawn or two. Have fun!"),
             "dubot_v2": ("Bonjour ! Je suis SharpDuBot : j'attaque toujours, mais je garde mes pions pour le coup qui compte.",
                          "Hi! I'm SharpDuBot: always on the attack, but I save my pawns for the blow that counts. Have fun!"),
             "capabot":    ("Bonjour ! Je suis CapaBot V1 : des positions claires, des pièces bien placées, et la patience d'attendre.",
                            "Hi! I'm CapaBot V1: clear positions, well-placed pieces, and all the patience in the world. Have fun!"),
             "capabot_v2": ("Bonjour ! Je suis CapaBot : des positions claires, des pièces bien placées… et, de temps en temps, une surprise.",
                            "Hi! I'm CapaBot: clear positions, well-placed pieces… and the occasional surprise. Have fun!")}


def is_program(player: dict) -> bool:
    """Un compte BOT ou l'IA de Lichess (pas d'accueil personnalisé, abandon plus tôt)."""
    return player.get("title") == "BOT" or "aiLevel" in player


def log(*parts) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Lichess:
    def __init__(self, token: str):
        self.token = token

    def _request(self, method: str, path: str, data: dict | None = None, timeout: float | None = 30,
                 accept: str | None = None):
        body = urllib.parse.urlencode(data).encode() if data is not None else (b"" if method == "POST" else None)
        headers = {"Authorization": f"Bearer {self.token}", "User-Agent": "fun_fish-bot"}
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(LICHESS + path, data=body, method=method, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout)

    def get(self, path: str) -> dict:
        with self._request("GET", path, accept="application/json") as r:
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


def draw_offer_text(board: chess.Board) -> tuple[str, str]:
    """Le message qui accompagne la proposition : le cas précis de `dead_draw` (mur de pions, ou pièces seules)."""
    if locked(board):
        return DRAW_LOCKED
    names = []
    for color in chess.COLORS:  # `bare` : une pièce au plus de chaque côté (matériel égal, 5 au plus)
        kinds = [p for p in PIECE_NAMES for _ in board.pieces(p, color)]
        names.append(PIECE_NAMES[kinds[0]] if len(kinds) == 1 else None)
    if all(names):
        return (f"Plus aucun pion, {names[0][0]} contre {names[1][0]} : personne ne peut gagner, je propose la nulle.",
                f"No pawns left, {names[0][1]} against {names[1][1]}: nobody can win, I offer a draw.")
    return ("Plus aucun pion, et des forces égales : personne ne peut gagner, je propose la nulle.",
            "No pawns left and equal forces: nobody can win, I offer a draw.")


def current_score(board: chess.Board, analyser) -> int | None:
    """Une éval fraîche de la position (pour accepter une nulle proposée), à faible profondeur."""
    if board.is_game_over():
        return None
    lines = analyser.analyse(board, multipv=1, depth=SHUFFLE_DEPTH)
    return lines[0].score if lines else None


def dead_draw(board: chess.Board, analyser) -> bool:
    """Nulle absolue : partie totalement fermée (mur de pions, ou presque plus rien sur l'échiquier) et
    Stockfish à ±DRAW_SCORE. Une position simplement égale, où il reste à creuser, ne compte pas."""
    if board.fullmove_number < DRAW_FROM or board.is_game_over() or not (locked(board) or bare(board)):
        return False
    lines = analyser.analyse(board, multipv=1, depth=DRAW_DEPTH)
    return bool(lines) and abs(lines[0].score) <= DRAW_SCORE


# Abandon : position perdue sans espoir à chacun de nos derniers coups, selon l'éval du meilleur candidat
# de la personnalité (filtre de sécurité, profondeur 6 en général). Plus vite contre un bot (il ne rate
# pas un gain de 10 pions) que contre un humain, qui peut encore se tromper ; jamais quand l'adversaire risque de perdre au temps. Mesuré sur 425 parties
# du serveur, avec cette même éval : -10 pions trois coups de suite aurait abandonné 127 des 145
# défaites, et deux parties non perdues, toutes deux contre des bots qui jouent exprès des coups perdants.
RESIGN_BOT = (1000, 3)  # (perte en cp, coups de suite)
RESIGN_HUMAN = (1500, 6)
RESIGN_THEIR_CLOCK = 30_000  # ms : en dessous, l'adversaire peut encore perdre au temps
RESIGN_TEXT = ("Bien joué ! J'abandonne.", "Well played! I resign.")


class ResignSense:
    """Quand abandonner : la position est perdue à chacun de nos `streak` derniers coups."""

    def __init__(self, against_bot: bool):
        self.loss, self.needed = RESIGN_BOT if against_bot else RESIGN_HUMAN
        self.streak = 0

    def resign(self, score: int | None, their_clock: int) -> bool:
        """À notre tour, `score` de notre point de vue (None : pas d'analyse, le compte reste)."""
        if score is None:
            return False  # jamais sans éval actuelle : l'adversaire a pu gaffer depuis
        self.streak = self.streak + 1 if score <= -self.loss else 0
        return self.streak >= self.needed and their_clock >= RESIGN_THEIR_CLOCK


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


# Tricotage : contre un bot, en partie amicale, une longue finale qui reste égale. Mesuré sur nos parties
# contre des bots (17/11) : ±0,2 pendant 20 coups au-delà du coup 60 a repéré 13 parties, 11 nulles et deux
# gagnées vers le coup 125 sur une erreur tardive du bot adverse. Proposer ou accepter la nulle ne coûte
# donc rien ; abandonner, seulement bien plus tard (coup 150), après plusieurs propositions refusées : les
# 8 nulles qui ont dépassé 150 coups auraient été écourtées, et aucune victoire perdue.
SHUFFLE_SCORE, SHUFFLE_STREAK, SHUFFLE_FROM, SHUFFLE_EVERY = 20, 20, 60, 10  # cp, nos coups, coup, coups
SHUFFLE_RESIGN_FROM, SHUFFLE_RESIGN_OFFERS = 150, 3
SHUFFLE_DEPTH = 10  # éval fraîche avant d'accepter une nulle proposée
SHUFFLE_OFFER = ("La position est égale depuis longtemps et plus rien ne bouge : je propose la nulle.",
                 "This has been dead level for a long time: I offer a draw.")
SHUFFLE_RESIGN = ("La position est nulle, mais mes propositions sont refusées : j'abandonne plutôt que de tourner "
                  "en rond. Bonne partie !",
                  "It's a dead draw and my offers were declined, so I resign rather than shuffle forever. GG!")


class ShuffleSense:
    """Contre un bot, en partie amicale : quand une longue partie égale tricote, proposer et accepter la
    nulle, puis abandonner si elle est refusée encore et encore (voir SHUFFLE_*). Jamais contre un humain
    (s'il reste, c'est son choix) ni en partie classée."""

    def __init__(self, applies: bool):
        self.applies = applies
        self.streak = 0  # nos coups de suite à ±SHUFFLE_SCORE
        self.offers = 0  # propositions faites (refusées, puisque la partie continue)
        self.offered_at = -100
        self.seen = -1  # dernier demi-coup observé : un coup rejoué après un envoi raté ne compte qu'une fois

    def observe(self, score: int | None, ply: int) -> None:
        """À chacun de nos coups, notre éval (None : coup joué sans analyse, la série n'est pas rompue)."""
        if ply == self.seen:
            return
        self.seen = ply
        if score is not None:
            self.streak = self.streak + 1 if abs(score) <= SHUFFLE_SCORE else 0

    def level(self, fullmove: int) -> bool:
        return self.applies and fullmove >= SHUFFLE_FROM and self.streak >= SHUFFLE_STREAK

    def offer(self, fullmove: int) -> bool:
        """Faut-il proposer ? (`sent` note la proposition une fois le coup réellement envoyé)"""
        return self.level(fullmove) and fullmove - self.offered_at >= SHUFFLE_EVERY

    def sent(self, fullmove: int, ours: bool) -> None:
        """Une proposition est partie avec notre coup (celle-ci, ou celle d'une position morte : un seul
        espacement pour les deux). Seules les nôtres comptent pour l'abandon."""
        self.offered_at = fullmove
        self.offers += ours

    def accept(self, fullmove: int, score: int | None) -> bool:
        """L'adversaire propose : on accepte si la partie tricote et si l'éval *actuelle* l'est encore
        (son dernier coup a pu être une gaffe)."""
        return self.level(fullmove) and score is not None and abs(score) <= SHUFFLE_SCORE

    def resign(self, fullmove: int, score: int | None) -> bool:
        """Jamais sans éval actuelle (pendule basse : coup sans analyse, un mat a pu apparaître)."""
        return (self.level(fullmove) and score is not None and abs(score) <= SHUFFLE_SCORE
                and fullmove >= SHUFFLE_RESIGN_FROM and self.offers >= SHUFFLE_RESIGN_OFFERS)


def timing_summary(timing: dict[int, dict], plies: int) -> tuple[float | None, float | None, float | None]:
    """(pendule la plus basse, plus longue réflexion, part de réflexion réduite) de nos coups parmi les
    `plies` premiers demi-coups de la partie (une mesure au-delà : un coup qui n'a jamais été joué)."""
    moves = [m for ply, m in timing.items() if ply < plies]
    if not moves:
        return None, None, None
    reduced = sum(m["effort"] is None or m["effort"] >= opponents.REDUCED_EFFORT for m in moves)
    return (min(m["clock"] for m in moves), max(m["seconds"] for m in moves), round(reduced / len(moves), 3))


class BotGame(threading.Thread):
    def __init__(self, bot: "Bot", game_id: str):
        super().__init__(daemon=True, name=f"partie-{game_id}")
        self.bot, self.id = bot, game_id
        self.over = False  # fin confirmée par Lichess (et non flux coupé ou erreur)
        self.greeted = False  # accueil envoyé (une seule fois, reconnexions comprises)
        self.timing: dict[int, dict] = {}  # demi-coup : pendule avant le coup (s), effort (None : sans analyse), durée
        self.ending: str | None = None  # fin à notre initiative ou anormale (opponents.MIGRATIONS)
        self.last_event = time.monotonic()  # dernier événement reçu du flux de la partie
        self.session = None  # (game, ours, pace, draws, resigns, shuffle) : l'état gardé entre deux connexions
        self.started = threading.Event()  # l'adversaire a joué au moins un coup
        self.ended = threading.Event()  # fil de partie terminé : plus aucune annulation
        self.armed = False  # compte à rebours lancé (par le fil de partie seul ; les nouveaux essais n'y touchent pas)
        self.watchdog = threading.Timer(NO_START, self.no_start)  # partie fJqf4pDw : 1 h bloquée après 1.e4
        self.watchdog.daemon = True

    def run(self) -> None:
        from fun_fish.analysis import Analyser
        from fun_fish.game import Game

        try:
            with Analyser(self.bot.stockfish, threads=1, hash_mb=64) as analyser:
                self.follow(analyser, Game)
        except Exception as exc:  # une partie qui plante ne doit pas arrêter le bot
            log(self.id, "erreur", repr(exc))
        finally:
            self.ended.set()
            self.watchdog.cancel()
            self.bot.finished(self.id, self.over)
            if not self.over:
                self.remember_unconfirmed()

    def follow(self, analyser, Game) -> None:
        """Joue la partie jusqu'à sa fin confirmée. Un flux coupé ou une erreur passagère de Lichess (5xx,
        429, réseau) n'arrête pas le fil : on se reconnecte, et `gameFull` redonne l'état complet. On abandonne
        après GAME_RETRY_FOR s de coupure sans le moindre événement, ou sur un refus net (4xx : partie inconnue)."""
        failing_since = None
        while True:
            try:
                self.play(analyser, Game)
                reason = "flux terminé"
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise
                reason = f"HTTP {exc.code}"
            except (OSError, ValueError) as exc:
                reason = repr(exc)
            if self.over:
                return
            now = time.monotonic()
            if failing_since is None or self.last_event > failing_since:  # première coupure depuis un événement
                failing_since = now
            if now - failing_since >= GAME_RETRY_FOR:
                log(self.id, "flux de la partie perdu, on cesse de la suivre :", reason)
                return
            log(self.id, f"flux de la partie interrompu ({reason}), reconnexion dans {GAME_RETRY} s")
            time.sleep(GAME_RETRY)

    def wait_first_move(self) -> None:
        """Lance le compte à rebours dès que c'est à l'adversaire de jouer son premier coup (une seule fois)."""
        if not self.armed:
            self.armed = True
            self.watchdog.start()

    def no_start(self) -> None:
        """L'adversaire n'a toujours pas joué son premier coup : on annule, et la place se libère."""
        if self.started.is_set() or self.ended.is_set():
            return
        log(self.id, f"pas de premier coup de l'adversaire en {NO_START} s : partie annulée")
        if not self.bot.api.post(f"/api/bot/game/{self.id}/abort", quick=True) and not self.ended.is_set():
            retry = threading.Timer(NO_START_RETRY, self.no_start)  # sinon la place resterait prise
            retry.daemon = True
            retry.start()
            self.watchdog = retry  # visible seulement une fois lancé (revue Codex : double démarrage)

    def play(self, analyser, Game) -> None:
        from fun_fish import personalities

        from fun_fish.pace import Pace

        api, answered = self.bot.api, None  # answered : nombre de coups au moment de la dernière offre traitée
        # l'état de la partie survit aux reconnexions (`follow`) : décisions et anomalies de l'export, pendule apprise
        game, ours, pace, draws, resigns, shuffle = self.session or (None, None, Pace(), DrawSense(), None, None)
        for event in api.stream(f"/api/bot/game/stream/{self.id}"):
            self.last_event = time.monotonic()
            if event.get("type") == "gameFull":
                if event.get("initialFen", "startpos") != "startpos":
                    self.over = api.post(f"/api/bot/game/{self.id}/abort")
                    return
                state = event["state"]
                if game is None:
                    ours = chess.WHITE if event["white"].get("id") == self.bot.me else chess.BLACK
                    agent = personalities.create(self.bot.personality, rng=random.Random(), **self.bot.params)
                    if not calm_clock(event.get("clock")) and hasattr(agent, "endgame_attack_depth"):
                        agent.endgame_attack_depth = 0  # 3+0 : des coups de finale plus lents ont déjà frôlé la chute
                    game = Game(agent, human_color=not ours)
                    opponent = event["black" if ours == chess.WHITE else "white"]
                    self.facts = {"opponent": opponent, "speed": event.get("speed"), "rated": event.get("rated"),
                                  "color": "white" if ours == chess.WHITE else "black",
                                  "our_rating": event["white" if ours == chess.WHITE else "black"].get("rating")}
                    self.adapt_traps(agent, opponent)
                    resigns = ResignSense(is_program(opponent))
                    shuffle = ShuffleSense(is_program(opponent) and not event.get("rated"))
                    self.session = (game, ours, pace, draws, resigns, shuffle)
                    log(self.id, "contre", opponent.get("name", "?"), opponent.get("rating", "?"),
                        "avec les", "Blancs" if ours == chess.WHITE else "Noirs")
                else:  # reconnexion : seul l'état compte, le reste est gardé (`session`)
                    log(self.id, "reprise de la partie après", len(game.board.move_stack), "demi-coups")
            elif event.get("type") == "gameState":
                state = event
            else:
                if event.get("type") == "chatLine" and event.get("username", "").lower() != self.bot.me:
                    log(self.id, "chat de", event.get("username", "?"), ":", event.get("text", ""))
                continue
            if game is None:
                continue
            if not self.greeted:
                for text in self.greeting():
                    api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text})
                self.greeted = True
            moves = state.get("moves", "").split()
            for uci in moves[len(game.board.move_stack):]:  # rattrape les coups (reconnexion comprise)
                game.board.push_uci(uci)
            if len(moves) >= (2 if ours == chess.WHITE else 1):
                self.started.set()  # l'adversaire a joué : plus d'annulation automatique
            elif game.board.turn != ours:
                self.wait_first_move()
            if state.get("status", "started") != "started":
                self.over = True
                self.save(game, state, analyser)
                return
            clock = state.get("wtime" if ours == chess.WHITE else "btime", 10**9)
            offered = state.get("bdraw" if ours == chess.WHITE else "wdraw", False)
            if offered and answered != len(moves) and clock >= 2 * LOW_CLOCK_MS:
                answered = len(moves)  # refuser, c'est simplement jouer : l'offre tombe avec notre coup
                accept = dead_draw(game.board, analyser) or (shuffle.level(game.board.fullmove_number) and shuffle.accept(
                    game.board.fullmove_number, current_score(game.board, analyser)))
                log(self.id, "nulle proposée par l'adversaire :", "acceptée" if accept else "on joue")
                if accept and api.post(f"/api/bot/game/{self.id}/draw/yes", quick=True):
                    self.ending = "nulle acceptée"
                    continue
            if game.board.turn == ours and not game.over:
                fullmove = game.board.fullmove_number  # avant notre coup
                offering = clock >= 2 * LOW_CLOCK_MS and draws.offer(game.board, dead_draw(game.board, analyser))
                increment = state.get("winc" if ours == chess.WHITE else "binc", 0)
                score = None  # notre éval, celle du choix du coup : aucune analyse en plus (elle changerait les coups)
                if clock < LOW_CLOCK_MS:
                    start = time.monotonic()
                    move = analyser.analyse(game.board, multipv=1, depth=low_clock_depth(game.board))[0].move
                    game.board.push(move)
                    self.timing[len(game.board.move_stack) - 1] = {
                        "clock": round(clock / 1000, 1), "effort": None, "seconds": round(time.monotonic() - start, 2)}
                else:
                    agent = game.personality
                    effort = pace.effort(clock / 1000, increment / 1000, game.board.fullmove_number)
                    if hasattr(agent, "effort"):
                        agent.effort = effort
                    start = time.monotonic()
                    decision = game.play_engine(analyser)
                    move = decision.move
                    pace.observe(time.monotonic() - start, effort)
                    score = max((c.score for c in decision.candidates), default=None)
                    self.timing[len(game.board.move_stack) - 1] = {
                        "clock": round(clock / 1000, 1), "effort": effort, "seconds": round(time.monotonic() - start, 2)}
                their = state.get("btime" if ours == chess.WHITE else "wtime", 10**9)
                shuffle.observe(score, len(game.board.move_stack) - 1)  # le coup prévu est déjà sur l'échiquier
                if shuffle.resign(fullmove, score) and not game.board.is_game_over(claim_draw=True):  # pas si ce coup
                    # conclut déjà la nulle (50 coups, répétition)
                    log(self.id, "abandonne (partie nulle qui tricote,", shuffle.offers, "propositions refusées)")
                    for text in SHUFFLE_RESIGN:
                        api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text}, quick=True)
                    if api.post(f"/api/bot/game/{self.id}/resign", quick=True):
                        self.ending = "tricotage"
                        self.timing.pop(len(game.board.move_stack) - 1, None)
                        game.retry()
                        continue
                shuffling = not offering and clock >= 2 * LOW_CLOCK_MS and shuffle.offer(fullmove)
                offering = offering or shuffling
                if resigns.resign(score, their):
                    log(self.id, "abandonne (position perdue depuis", resigns.streak, "coups)")
                    for text in RESIGN_TEXT:
                        api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text}, quick=True)
                    if api.post(f"/api/bot/game/{self.id}/resign", quick=True):
                        self.ending = "abandon"
                        self.timing.pop(len(game.board.move_stack) - 1, None)
                        game.retry()  # le coup prévu n'est pas joué : ni dans l'échiquier, ni dans l'export
                        continue  # la fin arrive par le flux ; sinon, on joue le coup prévu
                # la proposition de nulle voyage avec le coup (une seule requête) ; le message suit, sans le retarder
                suffix = "?offeringDraw=true" if offering else ""
                if not api.post(f"/api/bot/game/{self.id}/move/{move.uci()}{suffix}"):
                    game.board.pop()  # coup refusé : on se resynchronisera sur le prochain état (sa mesure reste :
                    # Lichess a pu le recevoir malgré la réponse perdue ; sinon, le coup rejoué la remplace)
                else:
                    if offering:
                        shuffle.sent(fullmove, shuffling)
                        draws.offered_at = fullmove
                    if not self.started.is_set():
                        self.wait_first_move()
                if offering and game.board.turn != ours:
                    log(self.id, "propose la nulle", "(partie égale qui tricote)" if shuffling else "(position bloquée)")
                    for text in SHUFFLE_OFFER if shuffling else draw_offer_text(game.board):
                        api.post(f"/api/bot/game/{self.id}/chat", {"room": "player", "text": text}, quick=True)

    def greeting(self) -> tuple[str, str]:
        """L'accueil : celui de la personnalité, ou, pour un humain déjà rencontré par ce compte, des retrouvailles
        avec le compteur (`opponents.welcome`). La base illisible ne coûte que l'accueil personnalisé."""
        opponent = getattr(self, "facts", {}).get("opponent", {})
        if opponent.get("id") and not is_program(opponent):
            try:
                past = opponents.history(self.bot.me, opponent["id"])
                again = opponents.welcome(past)
            except (sqlite3.Error, OSError) as exc:
                log(self.id, "mémoire des adversaires illisible :", repr(exc))
                again = None
            if again:
                log(self.id, f"accueil d'un habitué ({past.games} parties)")
                return again
        return GREETINGS.get(self.bot.personality, GREETING)

    def remember_unconfirmed(self, again: bool = True) -> None:
        """Fil arrêté sans fin confirmée (flux perdu, erreur) : la partie est notée quand même, d'après
        Lichess, si elle y est finie ; sinon on regarde une fois encore, UNCONFIRMED_RECHECK s plus tard."""
        if not getattr(self, "facts", None):
            return  # jamais commencée de notre côté : rien à noter
        try:
            game = self.bot.api.get(f"/game/export/{self.id}?moves=true&clocks=false&evals=false")
        except (OSError, ValueError) as exc:
            game = {}
            log(self.id, "fin de partie inconnue :", repr(exc))
        if game.get("status") in (None, "created", "started"):
            if again:
                timer = threading.Timer(UNCONFIRMED_RECHECK, self.remember_unconfirmed, (False,))
                timer.daemon = True
                timer.start()
            return
        record = opponents.from_lichess(game, self.bot.me, {})
        if record is None:
            return
        record.ending = "flux perdu"
        record.min_clock, record.longest_move, record.reduced = timing_summary(self.timing, record.plies or 0)
        try:
            opponents.record(record, how="absent")  # un autre fil l'a peut-être reprise et terminée : il prime
            log(self.id, "notée d'après Lichess :", game.get("status"), "gagnant :", game.get("winner", "aucun"))
        except (sqlite3.Error, OSError) as exc:
            log(self.id, "partie absente de la mémoire des adversaires :", repr(exc))

    def remember(self, export: dict) -> None:
        """Note la partie dans la mémoire des adversaires ; un échec ne coûte que cette ligne."""
        facts, info = getattr(self, "facts", None), export["lichess"]
        if not facts:
            return
        try:
            name, title, program, rating = opponents.player(facts["opponent"])
            their, mine = opponents.losses(export["moves"], facts["color"])
            opponents.record(opponents.GameRecord(
                self.id, self.bot.me, name, title, program, rating, facts["speed"], facts["rated"], facts["color"],
                opponents.result_for(facts["color"], info["status"], info["winner"]), info["status"],
                len(export["moves"]), time.time(), their, mine, self.ending,
                *timing_summary(self.timing, len(export["moves"]))), how="confirmed")
        except (sqlite3.Error, OSError) as exc:
            log(self.id, "partie absente de la mémoire des adversaires :", repr(exc))

    def adapt_traps(self, agent, opponent: dict) -> None:
        """Contre un bot, l'adversaire imaginé réglé d'après ses réponses passées à nos pièges (personnalités
        `adaptive_traps`, voir opponents.TRAP_LOW) ; jamais au prix de la partie."""
        self.trap_base = None
        if not getattr(agent, "adaptive_traps", False) or not is_program(opponent) or not opponent.get("id"):
            return
        self.trap_base = agent.opponent_elo
        try:
            level = opponents.trap_level(self.bot.me, opponent["id"], agent.opponent_elo)
        except (sqlite3.Error, OSError) as exc:
            log(self.id, "niveau des pièges illisible :", repr(exc))
            return
        if level != agent.opponent_elo:
            agent.opponent_elo = agent._elo_now = level
            log(self.id, "pièges : adversaire imaginé à", level, "Elo")

    def readjust_traps(self) -> None:
        """Après la partie (coups notables déjà notés) : le niveau des pièges contre ce bot, revu."""
        opponent = (getattr(self, "facts", None) or {}).get("opponent", {})
        if getattr(self, "trap_base", None) is None or not opponent.get("id"):
            return
        try:
            change = opponents.adjust_trap_level(self.bot.me, opponent["id"], self.trap_base)
        except (sqlite3.Error, OSError) as exc:
            log(self.id, "niveau des pièges non revu :", repr(exc))
            return
        if change:
            before, after, taken = change
            log(self.id, f"pièges : {opponent.get('name', opponent['id'])} en a pris {taken:.0%}, "
                         f"adversaire imaginé de {before} à {after} Elo")

    def save(self, game, state: dict, analyser) -> None:
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        export = game.debug_export(engine=analyser.engine_name)
        export["anomalies"] = getattr(game.personality, "anomalies", [])
        for entry in export["moves"]:  # pendule et effort de chacun de nos coups
            entry.update(self.timing.get(entry["ply"], {}))
        export["lichess"] = {"id": self.id, "url": f"{LICHESS}/{self.id}", "status": state.get("status"),
                             "winner": state.get("winner"), "ending": self.ending, **getattr(self, "facts", {})}
        export["chat"] = None  # lu à part : une lecture lente ne doit pas retarder la libération de la place
        path = GAMES_DIR / f"{time.strftime('%Y-%m-%d')}-{self.id}.json.gz"
        write_export(path, export)
        self.remember(export)
        try:
            opponents.note_highlights(self.id, self.bot.me, export)
        except (sqlite3.Error, OSError, ValueError, KeyError) as exc:  # jamais au prix de la fin de partie
            log(self.id, "coups notables non notés :", repr(exc))
        self.readjust_traps()
        threading.Thread(target=self.check_fairplay, args=(export,), daemon=True).start()
        log(self.id, "terminée :", state.get("status"), "gagnant :", state.get("winner", "aucun"))
        self.chat_lock = threading.Lock()  # la lecture immédiate et la relecture tardive n'écrivent pas ensemble
        self.chat_now = threading.Thread(target=self.late_chat, args=(path, export, False), daemon=True)
        self.chat_now.start()
        later = threading.Timer(CHAT_LATE, self.late_chat, (path, export))  # le « gg » vient souvent après
        later.daemon = True
        later.start()

    def check_fairplay(self, export: dict) -> None:
        """Contrôle de triche de l'humain, si la partie le demande (voir FAIRPLAY_GAP) ; jamais au prix du bot."""
        facts = getattr(self, "facts", None) or {}
        opponent = facts.get("opponent", {})
        if not opponent.get("id") or is_program(opponent):
            return
        player = opponent["id"]
        result = opponents.result_for(facts["color"], export["lichess"].get("status"), export["lichess"].get("winner"))
        gap = (facts.get("our_rating") or 0) - (opponent.get("rating") or 0)
        try:
            if not opponents.watched(player) and not (result in ("loss", "draw") and gap >= FAIRPLAY_GAP):
                return
            with FAIRPLAY_LOCK:
                game = self.bot.api.get(f"/game/export/{self.id}?moves=true&clocks=true&evals=false")
                clock = game.get("clock") or {}
                command = [str(self.bot.stockfish)]
                if os.name == "posix":
                    command = ["nice", "-n", "19", *command]
                engine = chess.engine.SimpleEngine.popen_uci(command)
                try:
                    engine.configure({"Threads": 1, "Hash": 64})
                    report = fairplay.analyse(game.get("moves", "").split(), facts["color"] != "white", engine,
                                              game.get("clocks"), clock.get("initial", 0), clock.get("increment", 0))
                finally:
                    engine.quit()
            suspect = fairplay.suspicious(report)
            strikes, blocked = opponents.note_fairplay(self.id, player, self.bot.me, bool(facts.get("rated")),
                                                       report, suspect)
            log(self.id, f"contrôle de triche de {player} :", "SUSPECTE" if suspect else "rien à signaler",
                f"({report.summary()}) ; avertissements : {strikes}" + (" ; liste noire" if blocked else ""))
        except Exception as exc:  # réseau, moteur, base : on n'en saura pas plus, la partie est déjà finie
            log(self.id, "contrôle de triche impossible :", repr(exc))

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


# Campagne : nos comptes mènent eux-mêmes leurs parties contre des bots, classées pour stabiliser leur
# classement. Fichier JSON relu à chaque recherche et à chaque défi, sans redémarrage, par exemple
# {"until": "2026-11-17T21:00:00+01:00", "rated": true, "decline_bots": true, "rd": 75}. Elle dure jusqu'à
# `until`, et, si elle est classée, tant que le compte n'est pas stable (rd ≤ `rd` dans chaque vitesse de
# MATCH_CLOCKS) : le dernier des deux. Les humains passent toujours en priorité.
@dataclass
class Campaign:
    until: float  # horodatage Unix
    rated: bool = True  # nos défis aux bots sont classés (les refus « rated » ne comptent plus)
    decline_bots: bool = True  # les défis reçus de bots sont refusés (later)
    rd: int = 75  # incertitude visée


def read_campaign() -> Campaign | None:
    """La campagne décrite par CAMPAIGN_PATH, ou None (absente ou illisible : on joue comme d'habitude)."""
    try:
        data = json.loads(CAMPAIGN_PATH.read_text())
        end = datetime.datetime.fromisoformat(data["until"])
        if end.tzinfo is None:
            raise ValueError(f"heure sans fuseau : {data['until']} (par exemple +01:00)")
        rated, decline, rd = data.get("rated", True), data.get("decline_bots", True), data.get("rd", 75)
        if not isinstance(rated, bool) or not isinstance(decline, bool) or isinstance(rd, bool) or not isinstance(rd, int):
            raise ValueError(f"rated et decline_bots : true ou false ; rd : un entier ({data})")
        return Campaign(end.timestamp(), rated, decline, rd)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log("campagne illisible, ignorée :", repr(exc))
        return None


def stable(perfs: dict, rd: int) -> bool:
    """Le classement du compte est stable dans chaque vitesse que joue la recherche d'adversaires."""
    return all(perfs.get(speed, {}).get("games", 0) and perfs.get(speed, {}).get("rd", 500) <= rd
               for speed in MATCH_CLOCKS)


def wished_bots(account: str) -> set[str]:
    """Les bots que ce compte doit encore jouer, d'après WISHLIST_PATH (relu à chaque recherche) : une liste de
    {"bot": "FetaCompli", "accounts": ["SharpDuBot", "CapaBot"], "games": 10, "since": "2026-11-18T18:00+01:00"},
    soit `games` parties (non annulées) par compte depuis `since`. Absente ou illisible : aucun."""
    try:
        wishes = json.loads(WISHLIST_PATH.read_text())
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as exc:
        log("liste de bots souhaités illisible, ignorée :", repr(exc))
        return set()
    wanted = set()
    try:
        for wish in wishes:
            since = datetime.datetime.fromisoformat(wish["since"])
            if since.tzinfo is None:
                raise ValueError(f"heure sans fuseau : {wish['since']} (par exemple +01:00)")
            bot, games = wish["bot"].lower(), wish["games"]
            if not isinstance(games, int) or isinstance(games, bool) or not isinstance(wish["accounts"], list):
                raise ValueError(f"games : un entier, accounts : une liste ({wish})")
            if account in {a.lower() for a in wish["accounts"]} and \
                    opponents.games_against(account, bot, since.timestamp()) < games:
                wanted.add(bot)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log("liste de bots souhaités illisible, ignorée :", repr(exc))
        return set()
    return wanted


def blocked_players() -> set[str]:
    """Les comptes dont on refuse les défis (triche, farming...), relus à chaque défi : sans redémarrage."""
    try:  # un octet invalide ne doit pas bloquer tous les défis : on garde les noms lisibles
        text = BLOCKED_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {name.lower() for line in text.splitlines() if (name := line.split("#", 1)[0].strip())}


def refused_players() -> set[str]:
    """Les défis refusés : le fichier `blocked`, plus la liste noire automatique (triche, voir fairplay.py)."""
    try:
        return blocked_players() | opponents.cheat_blocked()
    except (sqlite3.Error, OSError) as exc:
        log("liste noire automatique illisible :", repr(exc))
        return blocked_players()


# Contrôle de triche (fairplay.py) après une partie contre un humain : s'il est surveillé, ou s'il nous a
# battus ou tenus en nulle avec au moins FAIRPLAY_GAP points de moins. Une analyse à la fois par compte, en
# basse priorité et sur un seul fil : les parties en cours passent avant.
FAIRPLAY_GAP = 400
FAIRPLAY_LOCK = threading.Lock()


def bot_games_count() -> int:
    """Nos parties contre des bots en cours (ou sur le point de commencer), tous comptes réunis."""
    try:
        marks = list(BOT_GAMES_DIR.glob("*--*"))
    except OSError:
        return 0
    now, running = time.time(), 0
    for mark in marks:
        try:
            running += now - mark.stat().st_mtime < BOT_GAME_STALE
        except OSError:  # effacé entre-temps
            pass
    return running


def bot_game_running(capacity: int = 1) -> bool:
    """Nos comptes jouent-ils déjà `capacity` parties contre des bots (ou s'apprêtent-ils à le faire) ?"""
    return bot_games_count() >= capacity


def served_at(account: str) -> float:
    """Heure du dernier défi lancé par ce compte (0 : jamais)."""
    try:
        return (BOT_GAMES_DIR / f"servi.{account}").stat().st_mtime
    except OSError:
        return 0.0


def someone_waited_longer(account: str) -> bool:
    """Un autre de nos comptes attend une place qu'il pourrait prendre maintenant (sa capacité, écrite dans
    son marqueur, n'est pas atteinte), et son dernier défi lancé est plus ancien que le nôtre. Un compte
    sorti de campagne (une place) ne retient donc pas la deuxième place de ceux qui y sont encore."""
    now, mine, running = time.time(), served_at(account), bot_games_count()
    for mark in BOT_GAMES_DIR.glob("attente.*"):
        other = mark.name.split(".", 1)[1]
        try:
            fresh = now - mark.stat().st_mtime < MATCH_WAITING
            capacity = int(mark.read_text() or 1)
        except (OSError, ValueError):
            continue
        if other != account and fresh and running < capacity and served_at(other) < mine:
            return True
    return False


def withdraw(account: str) -> None:
    """Le compte n'attend plus la place (occupé, ou servi)."""
    with contextlib.suppress(OSError):
        (BOT_GAMES_DIR / f"attente.{account}").unlink(missing_ok=True)


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


def reserve_bot_game(account: str, game_id: str, spacing: float = 0, capacity: int = 1) -> bool:
    """Réserve une place commune (au plus `capacity` parties contre des bots à la fois), atomiquement.
    `spacing` : pour un défi lancé, au moins ce délai depuis le précédent défi lancé par l'un de nos
    comptes, et chacun son tour (le compte servi il y a le plus longtemps d'abord)."""
    try:
        with bot_games_lock():
            if spacing:
                (BOT_GAMES_DIR / f"attente.{account}").write_text(str(capacity))  # même refusé, on attend son tour
                if someone_waited_longer(account):
                    return False
            if bot_game_running(capacity):
                return False
            if spacing:
                last = BOT_GAMES_DIR / "dernier-defi"
                if last.exists() and time.time() - last.stat().st_mtime < spacing:
                    return False
                last.touch()
                (BOT_GAMES_DIR / f"servi.{account}").touch()
                withdraw(account)
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


def calm_clock(clock: dict | None) -> bool:
    """La cadence laisse-t-elle le temps de calculer plus loin en finale ? (incrément, ou 5 min de base)"""
    return not clock or clock.get("increment", 0) > 0 or clock.get("initial", 0) >= CALM_CLOCK_MS


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
        self.outgoing: tuple[str, float, str, str] | None = None  # (défi, lancé à, adversaire, cadence)
        self.early_drops: dict[str, tuple[str, str | None]] = {}  # défi : (raison du refus ou « », destinataire)
        self.declined: dict[str, float] = {}
        self.stable: tuple[int, bool, float] | None = None  # (rd visé, classement stable ?, vu à) : fin d'une campagne
        self.in_campaign = False
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
                self.bot_free_at = now + (CAMPAIGN_BOT_PAUSE if self.in_campaign else BOT_PAUSE) * (now - start)
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

    def campaign(self, ask: bool = True) -> Campaign | None:
        """La campagne en cours pour ce compte (voir `Campaign`), ou None ; le journal note son début et sa fin.
        `ask=False` (défis reçus, flux d'événements) : jamais de requête à Lichess, la stabilité connue suffit."""
        planned = read_campaign()
        running = planned is not None and (
            time.time() < planned.until or (planned.rated and not self.is_stable(planned.rd, ask)))
        if running != self.in_campaign:
            self.in_campaign = running
            log("campagne", "en cours" if running else "terminée", "(classée)" if planned and planned.rated else "")
        return planned if running else None

    def is_stable(self, rd: int, ask: bool = True) -> bool:
        """Classement stable pour ce seuil : vu il y a moins de STABLE_FRESH s (et pour ce même seuil), sinon
        redemandé à Lichess si `ask` ; injoignable ou inconnu : pas encore stable (l'échec est retenu, pour ne
        pas redemander à chaque appel)."""
        known = self.stable is not None and self.stable[0] == rd
        if ask and (not known or time.monotonic() - self.stable[2] > STABLE_FRESH):
            try:
                value = stable(self.api.get("/api/account").get("perfs", {}), rd)
            except (OSError, ValueError) as exc:
                log("classement inconnu (campagne) :", repr(exc))
                value = False
            self.stable, known = (rd, value, time.monotonic()), True
        return known and self.stable[1]

    def decline_reason(self, challenge: dict) -> str | None:
        if DRAIN_PATH.exists():
            return "later"
        challenger = challenge.get("challenger", {})
        if {challenger.get("id", ""), challenger.get("name", "").lower()} & refused_players():
            return "generic"
        if challenger.get("title") == "BOT" and not self.accept_bots and (campaign := self.campaign(ask=False)) \
                and campaign.decline_bots:
            return "later"  # nous menons nos propres parties contre des bots
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
        """Traite un défi reçu, et le note dans la mémoire des adversaires (table `challenges`)."""
        decision, reason = self.answer_challenge(c)
        try:
            opponents.challenge(self.me, c, decision, reason)
        except (sqlite3.Error, OSError) as exc:
            log("défi absent de la mémoire des adversaires :", repr(exc))

    def answer_challenge(self, c: dict) -> tuple[str, str | None]:
        """Accepte ou refuse un défi : (accepted, declined ou failed, raison). Le quota des bots n'est compté qu'une fois l'acceptation confirmée
        par Lichess : un échec (défi annulé entre-temps, réseau) ne bloque pas les bots suivants."""
        reason = self.decline_reason(c)
        who = c.get("challenger", {}).get("name", "?")
        what = describe(c)
        if reason:
            log("défi de", who, "refusé :", reason, what)
            self.api.post(f"/api/challenge/{c['id']}/decline", {"reason": reason})
            return "declined", reason
        rationed = self.rationed(c)
        if rationed and not reserve_bot_game(self.me, c["id"]):  # un autre compte vient de la prendre
            log("défi de", who, "refusé : later (un autre compte joue contre un bot)", what)
            self.api.post(f"/api/challenge/{c['id']}/decline", {"reason": "later"})
            return "declined", "later (un autre compte joue contre un bot)"
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
            return "declined", "later (notre défi à un bot est en cours)"
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
            return ("accepted", None) if ok else ("failed", None)
        now = time.monotonic()
        self.bot_games[c["id"]] = self.bot_opponents[c.get("challenger", {}).get("id", "")] = now
        self.bot_free_at = now + BOT_GAME_STALE  # au cas où la fin de partie ne serait jamais vue
        return "accepted", None

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

    def drop_outgoing(self, challenge_id: str, declined: str | None = None, to: str | None = None) -> None:
        """Notre défi est refusé (`declined` : la raison donnée par Lichess), annulé ou expiré : la place
        est rendue. Reçu avant que Lichess ait donné l'identifiant de notre défi, l'événement est gardé
        pour `seek`. Le refus est noté dans la mémoire des adversaires (`opponents.refusals`)."""
        with self.lock:
            if self.outgoing is not None and self.outgoing[0] == "" and challenge_id:
                self.early_drops[challenge_id] = (declined or "", to)  # « » : annulé, sans refus
                return
            if self.outgoing is None or self.outgoing[0] != challenge_id:
                return
            _, _, opponent, clock = self.outgoing
            if not challenge_id and not declined:  # identifiant jamais connu (réponse de création perdue) : un refus
                declined = next((r for r, dest in self.early_drops.values() if r and dest == opponent), None)  # reçu
            self.outgoing = None
            if declined and declined not in opponents.CLOCK_REASONS | {"rated", "casual"}:  # une autre cadence reste
                # possible ; « rated », « casual » : la mémoire des refus seule en décide (selon le mode du moment)
                self.declined[opponent] = time.monotonic()
        unmark_bot_games(self.me, challenge_id or "sortant")
        if declined:
            try:
                opponents.decline(opponent, declined, clock)
            except (sqlite3.Error, OSError) as exc:
                log("refus absent de la mémoire des adversaires :", repr(exc))

    def settle_outgoing(self, pending: tuple[str, float, str, str]) -> bool:
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
                and not DRAIN_PATH.exists() and now >= self.bot_free_at
                and now - self.idle_since >= (CAMPAIGN_IDLE if self.in_campaign else MATCH_IDLE))

    def pick(self, candidates: list[str]) -> str:
        """Tirage d'un adversaire, pondéré par l'intérêt de nos parties passées (`opponents.Profile`) : un bot
        qui joue comme Stockfish revient rarement. Sans mémoire lisible, tirage uniforme."""
        try:
            profiles = opponents.bot_profiles()
        except (sqlite3.Error, OSError) as exc:
            log("mémoire des adversaires illisible, tirage uniforme :", repr(exc))
            profiles = {}
        profile = {name: profiles.get(name, opponents.Profile()) for name in candidates}
        opponent = random.choices(candidates, [profile[name].interest for name in candidates])[0]
        self.picked = f"{profile[opponent].kind}, parmi {len(candidates)}"  # écrit au journal avec le défi
        return opponent

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
            ready = self.ready_to_seek(now)
        if not ready:
            withdraw(self.me)  # occupé : on ne retient pas la place des autres
            return None
        perfs = self.api.get("/api/account").get("perfs", {})
        planned = read_campaign()
        if planned is not None:
            self.stable = (planned.rd, stable(perfs, planned.rd), time.monotonic())
        campaign = self.campaign()
        rated = BOT_RATED or bool(campaign and campaign.rated)
        table = CAMPAIGN_CLOCKS if rated and campaign and stable(perfs, campaign.rd) else MATCH_CLOCKS
        # la vitesse la plus incertaine d'abord ; sans adversaire possible, la suivante
        speeds = sorted(table, key=lambda k: (perfs.get(k, {}).get("rd", 500), random.random()), reverse=True)
        fast = random.random() < FAST_SHARE
        if fast:  # une amicale en 1+1, hors classement et hors bots souhaités
            rated, table, speeds = False, FAST_CLOCKS, list(FAST_CLOCKS)
        speed, rating = speeds[0], 0

        def gap(bot: dict) -> int:
            return bot.get("perfs", {}).get(speed, {}).get("rating", -10**4) - rating

        try:
            refused = opponents.refusals(ignore={"rated"} if rated else {"casual"})  # refus du mode d'avant
        except (sqlite3.Error, OSError) as exc:
            log("mémoire des refus illisible :", repr(exc))
            refused = {}

        def clocks(name: str) -> list[tuple[int, int]]:
            """Les cadences de cette vitesse que ce bot n'a pas refusées récemment."""
            no = refused.get(name, opponents.Refusal()).clocks
            return [c for c in table[speed] if f"{c[0]}+{c[1]}" not in no]

        def fits(bot: dict) -> bool:
            name = bot.get("id", "")
            if refused.get(name, opponents.Refusal()).blocked or not clocks(name):
                return False
            last_played, last_declined = self.bot_opponents.get(name), self.declined.get(name)
            return (name not in OUR_BOTS | {self.me} and not bot.get("disabled") and not bot.get("tosViolation")
                    and abs(gap(bot)) <= MATCH_RANGE
                    and (last_played is None or now - last_played >= BOT_SAME_OPPONENT)
                    and (last_declined is None or now - last_declined >= MATCH_DECLINED)
                    and name not in blocked_players())
        online = list(self.api.lines("/api/bot/online?nb=200"))
        wanted = wished_bots(self.me) if online and not fast else set()
        try:  # un bot souhaité est défié en amical, même en campagne classée, sauf s'il demande du classé
            rated_only = opponents.wants_rated() & wanted if wanted else set()
            refused_casual = opponents.refusals(ignore={"casual"}) if wanted and rated else refused
            refused_rated = refused if rated else opponents.refusals(ignore={"rated"}) if rated_only else {}
        except (sqlite3.Error, OSError) as exc:
            log("mémoire des refus illisible :", repr(exc))
            rated_only, refused_casual, refused_rated = set(), refused, {}

        def wish_fits(bot: dict) -> bool:
            """Un bot souhaité (`wished_bots`) : mêmes règles (jamais l'un des nôtres), sans écart de classement
            maximal, et espacé de WISH_SPACING seulement."""
            name = bot.get("id", "")
            if (refused_rated if name in rated_only else refused_casual).get(name, opponents.Refusal()).blocked:
                return False
            last_played, last_declined = self.bot_opponents.get(name), self.declined.get(name)
            return (name in wanted and name not in OUR_BOTS | {self.me} and bool(clocks(name))
                    and not bot.get("disabled") and not bot.get("tosViolation")
                    and (last_played is None or now - last_played >= WISH_SPACING)
                    and (last_declined is None or now - last_declined >= MATCH_DECLINED)
                    and name not in blocked_players())
        opponent = None
        for speed in speeds:  # un bot souhaité d'abord, dans la vitesse la plus incertaine où il joue
            wishes = [b["id"] for b in online if wish_fits(b)]
            if wishes:
                rating = reference_rating(perfs, speed)  # pour le journal
                opponent = random.choice(wishes)
                rated = opponent in rated_only
                self.picked = "souhaité"
                break
        else:
            for speed in speeds:
                rating = reference_rating(perfs, speed)
                candidates = [(b["id"], gap(b)) for b in online if fits(b)]
                if candidates:
                    break
            else:
                log(f"recherche d'adversaire ({'/'.join(speeds)}) : aucun bot disponible")
                withdraw(self.me)  # rien à jouer : on ne retient pas le tour des autres
                return None
        opponent = opponent or self.pick(band(candidates))
        if fast:
            self.picked = f"amicale rapide, {self.picked}"
        now = time.monotonic()
        with self.lock:  # l'état a pu changer pendant les lectures (humain accepté, vidange) : on revérifie
            ready = self.ready_to_seek(now)
            if ready:
                limit, increment = random.choice(clocks(opponent))
                self.outgoing, self.early_drops = ("", now, opponent, f"{limit}+{increment}"), {}  # dès lors, humains et bots attendent
        if not ready:
            withdraw(self.me)
            return None
        if not reserve_bot_game(self.me, "sortant", spacing=MATCH_SPACING,
                                capacity=CAMPAIGN_BOT_GAMES if campaign else 1):
            with self.lock:
                self.outgoing = None
            return None
        try:
            created = self.api.create(f"/api/challenge/{opponent}", {
                "rated": str(rated).lower(), "clock.limit": limit, "clock.increment": increment, "color": "random",
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
            self.outgoing = (game_id, now, opponent, f"{limit}+{increment}")
            early = self.early_drops.pop(game_id, None)
        if early is not None:  # refusé ou annulé avant même la réponse de Lichess
            self.drop_outgoing(game_id, declined=early[0] or None)
            return None
        log(f"défi lancé à {opponent} ({limit // 60}+{increment} {speed}, notre classement {rating} ; "
            f"{getattr(self, 'picked', '?')})")
        return game_id

    def watch_forever(self) -> None:
        """Chaque minute : `watch`."""
        while True:
            time.sleep(random.uniform(MATCH_CHECK / 2, MATCH_CHECK * 1.5))
            try:
                self.watch()
            except Exception as exc:  # jamais au prix du bot
                log("surveillance :", repr(exc))

    def watch(self) -> None:
        """La fin éventuelle d'une campagne (stabilité redemandée ici, hors du flux des défis, même sans
        recherche d'adversaires), puis les acceptations sans gameStart et la recherche si elle est active."""
        self.campaign()
        self.seek() if self.matchmaking else self.reconcile_accepting()

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
                        ours = c.get("challenger", {}).get("id", self.me) == self.me  # pas nos refus de défis reçus
                        reason = (c.get("declineReasonKey") or "generic").lower() \
                            if kind == "challengeDeclined" and ours else None
                        self.drop_outgoing(c.get("id", ""), declined=reason, to=c.get("destUser", {}).get("id"))
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
