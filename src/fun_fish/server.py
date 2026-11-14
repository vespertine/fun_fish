"""Serveur HTTP local : sert l'interface et pilote la partie."""

from __future__ import annotations

import random
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Literal

import chess
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fun_fish import personalities
from fun_fish.analysis import Analyser
from fun_fish.game import Game, GameError

STATIC_DIR = Path(__file__).parent / "static"


class NewGameIn(BaseModel):
    personality: str = personalities.DEFAULT_PERSONALITY
    color: Literal["white", "black", "random"] = "white"
    params: dict[str, float | str] = {}  # str : nom d'un arbre d'ouverture (theory_book)


class PersonalityIn(BaseModel):
    personality: str
    params: dict[str, float | str] = {}  # str : nom d'un arbre d'ouverture (theory_book)


def _personality(key: str, params: dict[str, float | str]):
    try:
        return personalities.create(key, **params)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


class MoveIn(BaseModel):
    uci: str


def create_app(analyser: Analyser, *, on_startup: Callable[[], None] | None = None, debug: bool = False) -> FastAPI:
    """`debug` : toutes les personnalités, leurs réglages et leur raisonnement. Sinon, mode jeu :
    seulement les personnalités publiques, avec leurs réglages par défaut."""
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if on_startup:
            on_startup()
        yield

    app = FastAPI(title="fun_fish", lifespan=lifespan)
    lock = threading.Lock()
    first = personalities.DEFAULT_PERSONALITY if debug else personalities.PUBLIC_PERSONALITIES[0]
    session = {"game": Game(personalities.create(first))}

    def chosen(key: str, params: dict[str, float | str]):
        if debug:
            return _personality(key, params)
        if key not in personalities.PUBLIC_PERSONALITIES:
            raise HTTPException(400, f"Personnalité indisponible en mode jeu : {key}")
        return _personality(key, {})  # mode jeu : les réglages retenus, sans retouche

    def game() -> Game:
        return session["game"]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/info")
    def info() -> dict:
        catalogue = personalities.catalogue()
        if not debug:
            public = personalities.PUBLIC_PERSONALITIES
            catalogue = sorted((p for p in catalogue if p["key"] in public), key=lambda p: public.index(p["key"]))
        return {"engine": getattr(analyser, "engine_name", "?"), "personalities": catalogue,
                "mode": "debug" if debug else "play"}

    @app.get("/api/debug-export")
    def debug_export() -> dict:
        with lock:
            return game().debug_export(getattr(analyser, "engine_name", "?"))

    @app.get("/api/state")
    def state() -> dict:
        with lock:
            return game().state()

    @app.post("/api/new")
    def new_game(body: NewGameIn) -> dict:
        personality = chosen(body.personality, body.params)
        color = random.choice(["white", "black"]) if body.color == "random" else body.color
        with lock:
            session["game"] = Game(personality, chess.WHITE if color == "white" else chess.BLACK)
            return game().state()

    @app.post("/api/move")
    def move(body: MoveIn) -> dict:
        with lock:
            try:
                game().play_human(body.uci)
            except GameError as exc:
                raise HTTPException(400, str(exc)) from None
            return game().state()

    @app.post("/api/engine-move")
    def engine_move() -> dict:
        with lock:
            try:
                game().play_engine(analyser)
            except GameError as exc:
                raise HTTPException(409, str(exc)) from None
            return game().state()

    @app.post("/api/personality")
    def set_personality(body: PersonalityIn) -> dict:
        """Change d'adversaire ou de réglages sans recommencer la partie."""
        personality = chosen(body.personality, body.params)
        with lock:
            game().personality = personality
            return game().state()

    @app.post("/api/retry")
    def retry() -> dict:
        with lock:
            try:
                game().retry()
            except GameError as exc:
                raise HTTPException(409, str(exc)) from None
            return game().state()

    @app.post("/api/undo")
    def undo() -> dict:
        with lock:
            game().undo()
            return game().state()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
