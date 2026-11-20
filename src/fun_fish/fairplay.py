"""Parties suspectes : un adversaire humain a-t-il joué avec l'aide d'un moteur ?

Signaux mesurés hors ouverture, hors positions jouées (mat annoncé, ou plus de DECIDED cp) et hors coups
évidents (seul coup légal, ou meilleur coup très au-dessus du deuxième) :
- la part des coups à moins de NEAR cp du premier choix de Stockfish (`near`) ; le premier choix strict
  (`match`) est donné pour information : les moteurs eux-mêmes n'y dépassent guère 60 % ;
- la perte moyenne par coup (`acpl`, en cp) ;
- le rythme : un joueur qui recopie un moteur met toujours à peu près le même temps (`cv`, écart type
  rapporté à la moyenne des temps de réflexion). Le rapport des temps sur coups évidents et difficiles
  (`obvious_ratio`) est donné pour information : il ne sépare pas les humains des moteurs.
Une partie est suspecte si précision et rythme concordent (voir `suspicious`). Seuils calibrés sur nos
parties passées (voir ONBOARDING.md).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import chess
import chess.engine

DEPTH = 16
OPENING = 6  # coups du joueur ignorés au début (théorie)
DECIDED = 800  # cp : au-delà, la partie est jouée, tout coup gagne
OBVIOUS = 150  # cp : écart entre le meilleur coup et le deuxième qui rend le meilleur évident
NEAR = 10  # cp : un coup à moins de NEAR du meilleur vaut le meilleur
MIN_MOVES = 8  # coups comptés (ni évidents, ni position jouée) pour juger
MIN_TIMES = 12  # temps de réflexion mesurés pour juger du rythme
LOW_CLOCK = 0.1  # part du temps initial : en dessous, on joue vite par force, le rythme ne dit rien
MOVE_TIME = 3  # s : plafond d'une analyse (Stockfish dépasse parfois de loin la profondeur dans les mats)
NEAR_SHARE, ACPL, CV = 0.6, 25, 0.45  # seuils de `suspicious` (recalibrés le 20/11/2026, voir ONBOARDING.md)


@dataclass
class Report:
    moves: int = 0  # coups comptés
    match: float = 0.0
    near: float = 0.0
    acpl: float = 0.0
    cv: float | None = None
    obvious_ratio: float | None = None
    obvious: int = 0  # coups évidents (pour le rythme)
    detail: list = field(default_factory=list, repr=False)  # par coup : (demi-coup, meilleure éval, deuxième, perte, premier choix, temps)

    def summary(self) -> str:
        def fmt(x):
            return "?" if x is None else f"{x:.2f}"
        return (f"{self.moves} coups, {self.near:.0%} proches du meilleur ({self.match:.0%} premier choix), perte {self.acpl:.0f} cp, "
                f"rythme cv {fmt(self.cv)}, évidents/difficiles {fmt(self.obvious_ratio)} ({self.obvious} évidents)")


def think_times(clocks: list[int], increment: int) -> list[float | None]:
    """Temps de réflexion de chaque demi-coup (s), d'après les pendules de l'export Lichess (centièmes,
    après chaque demi-coup). Les deux premiers demi-coups n'ont pas de pendule de départ fiable : None."""
    return [None if i < 2 else (clocks[i - 2] - clocks[i]) / 100 + increment for i in range(len(clocks))]


def analyse(moves: list[str], side: chess.Color, engine: chess.engine.SimpleEngine, clocks: list[int] | None = None,
            initial: int = 0, increment: int = 0, depth: int = DEPTH) -> Report:
    """Les signaux de `side` dans une partie (coups en SAN depuis la position initiale)."""
    board, limit = chess.Board(), chess.engine.Limit(depth=depth, time=MOVE_TIME)
    times = think_times(clocks, increment) if clocks else []
    losses, matches, hard_times, easy_times, all_times, detail = [], [], [], [], [], []
    for ply, san in enumerate(moves):
        move = board.parse_san(san)
        if board.turn != side or ply // 2 < OPENING:
            board.push(move)
            continue
        legal = board.legal_moves.count()
        info = engine.analyse(board, limit, multipv=2)
        best = info[0]["score"].pov(side)
        decided = best.is_mate() or abs(best.score()) > DECIDED
        second = info[1]["score"].pov(side).score(mate_score=10**4) if len(info) > 1 else None
        obvious = legal == 1 or (second is not None and best.score(mate_score=10**4) - second >= OBVIOUS)
        top = move == info[0]["pv"][0]
        loss = None
        if not decided and not obvious:
            if top:
                loss = 0
            else:
                board.push(move)
                after = engine.analyse(board, limit)["score"].pov(side).score(mate_score=10**4)
                board.pop()
                loss = max(0, best.score() - after)
            losses.append(min(loss, DECIDED))
            matches.append(top)
        spent = times[ply] if ply < len(times) else None
        detail.append((ply, best.score(mate_score=10**4), second, loss, top, spent))
        if spent is not None and initial and clocks[ply - 2] >= LOW_CLOCK * initial * 100:  # pendule avant le coup
            all_times.append(spent)
            (easy_times if obvious else hard_times).append(spent)
        board.push(move)
    report = Report(moves=len(matches), obvious=len(easy_times), detail=detail)
    if matches:
        report.match, report.acpl = sum(matches) / len(matches), statistics.fmean(losses)
        report.near = sum(loss <= NEAR for loss in losses) / len(losses)
    if len(all_times) >= MIN_TIMES and statistics.fmean(all_times) > 0:
        report.cv = statistics.pstdev(all_times) / statistics.fmean(all_times)
    if len(easy_times) >= 3 and hard_times and statistics.median(hard_times) > 0:
        report.obvious_ratio = statistics.median(easy_times) / statistics.median(hard_times)
    return report


def suspicious(report: Report) -> bool:
    """Assez de coups, une précision de moteur et un rythme de métronome. Sans pendule, la précision seule
    ne suffit pas : de bons humains l'atteignent (calibrage : aucun humain sauf un copiste avéré)."""
    return (report.moves >= MIN_MOVES and report.near >= NEAR_SHARE and report.acpl <= ACPL
            and report.cv is not None and report.cv <= CV)
