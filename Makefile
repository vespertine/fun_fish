# Raccourcis de développement (Linux, macOS). Sous Windows : voir le README.
PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
PORT ?= 8765

.PHONY: help install dev sim-install serve run test clean

help: ## Affiche cette aide
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-12s %s\n", $$1, $$2}'

$(BIN)/fun-fish:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q -e '.[dev]'

install: $(BIN)/fun-fish ## Crée le venv et installe le projet (avec les outils de test)

dev: install ## Alias de install

sim-install: install ## Ajoute Maia-2 et PyTorch CPU (simulations, opponent_elo)
	$(BIN)/pip install -q "torch==2.8.*" --index-url https://download.pytorch.org/whl/cpu
	$(BIN)/pip install -q -e '.[dev,sim]'

serve: install ## Démarre le serveur sans ouvrir le navigateur (PORT=8765)
	$(BIN)/fun-fish play --port $(PORT) --no-browser

run: install ## Démarre le serveur et ouvre le navigateur (PORT=8765)
	$(BIN)/fun-fish play --port $(PORT)

test: install ## Lance les tests
	$(BIN)/pytest -q

clean: ## Supprime le venv et les caches (pas le cache Stockfish/Maia)
	rm -rf $(VENV) .pytest_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
