# ============================================================
# InsightMesh AI — Makefile
# ============================================================
# Common developer commands. Works on macOS/Linux and Windows under
# Git Bash or WSL. For native Windows PowerShell, see commands in README.
# ============================================================

.PHONY: help install install-dev install-spacy backend frontend run dev \
        test test-slow test-all coverage lint format typecheck \
        docker-up docker-down docker-build docker-logs \
        clean clean-cache clean-history db-stats reset-db

# Default target — print help
help:
	@echo "InsightMesh AI — common commands"
	@echo ""
	@echo "  make install         Install Python deps"
	@echo "  make install-dev     Install Python deps + dev tools (pytest, ruff, etc.)"
	@echo "  make install-spacy   Download the spaCy English model"
	@echo ""
	@echo "  make backend         Run FastAPI server (uvicorn --reload)"
	@echo "  make frontend        Run Vite dev server"
	@echo "  make dev             Run both backend & frontend (needs 2 terminals)"
	@echo ""
	@echo "  make test            Run fast test suite"
	@echo "  make test-slow       Run only slow/integration tests"
	@echo "  make test-all        Run everything"
	@echo "  make coverage        Generate coverage report"
	@echo ""
	@echo "  make lint            ruff + eslint"
	@echo "  make format          black + prettier-style fixes"
	@echo "  make typecheck       mypy"
	@echo ""
	@echo "  make docker-up       docker compose up (build + start)"
	@echo "  make docker-down     Stop docker compose stack"
	@echo "  make docker-logs     Tail logs"
	@echo ""
	@echo "  make clean           Clean build artifacts"
	@echo "  make clean-cache     Clear in-process caches (calls API)"
	@echo "  make db-stats        Show run-history stats"

# -------------------- Setup --------------------

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt
	pip install pytest pytest-asyncio pytest-cov ruff black mypy

install-spacy:
	python -m spacy download en_core_web_sm

# -------------------- Run --------------------

backend:
	uvicorn backend.main:app --reload --port 8000

frontend:
	cd insightmesh-fe && npm run dev

# Convenience: print instructions for running both
dev:
	@echo "Open two terminals and run 'make backend' in one, 'make frontend' in the other."
	@echo "Or use 'make docker-up' to run both via docker compose."

# -------------------- Tests --------------------

test:
	pytest -v

test-slow:
	pytest -v -m slow

test-all:
	pytest -v -m ""

coverage:
	pytest --cov=backend --cov-report=term-missing --cov-report=html
	@echo "HTML report at htmlcov/index.html"

# -------------------- Code quality --------------------

lint:
	@echo "→ ruff (Python)"
	-ruff check backend tests
	@echo "→ eslint (frontend)"
	-cd insightmesh-fe && npm run lint

format:
	@echo "→ black"
	-black backend tests
	@echo "→ ruff --fix"
	-ruff check backend tests --fix

typecheck:
	-mypy backend

# -------------------- Docker --------------------

docker-up:
	docker compose up --build -d
	@echo "Frontend: http://localhost:5173 | Backend: http://localhost:8000"

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-logs:
	docker compose logs -f --tail=200

# -------------------- Utilities --------------------

clean:
	@echo "Cleaning Python caches…"
	-find . -type d -name "__pycache__" -prune -exec rm -rf {} \;
	-find . -type d -name ".pytest_cache" -prune -exec rm -rf {} \;
	-find . -type d -name ".ruff_cache" -prune -exec rm -rf {} \;
	-find . -type d -name ".mypy_cache" -prune -exec rm -rf {} \;
	-find . -type d -name "*.egg-info" -prune -exec rm -rf {} \;
	-rm -rf htmlcov .coverage
	@echo "Cleaning frontend build…"
	-rm -rf insightmesh-fe/dist insightmesh-fe/.vite
	@echo "Done."

clean-cache:
	@curl -fsS -X POST http://localhost:8000/api/insightmesh/history/cache/clear || \
		echo "(backend not running — start it first)"

db-stats:
	@curl -fsS http://localhost:8000/api/insightmesh/history/stats | \
		python -m json.tool || echo "(backend not running)"

reset-db:
	@echo "Deleting run-history DB…"
	-rm -f backend/data/insightmesh.db backend/data/insightmesh.db-shm backend/data/insightmesh.db-wal
	@echo "Restart the backend to recreate it."
