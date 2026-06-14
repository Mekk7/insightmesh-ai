# =============================================================
# InsightMesh AI -- PowerShell dev helper (Windows-friendly)
# =============================================================
# Usage:
#   .\dev.ps1 help
#   .\dev.ps1 install
#   .\dev.ps1 backend
#   .\dev.ps1 frontend
#   .\dev.ps1 test
#   .\dev.ps1 docker-up
# =============================================================

param(
    [Parameter(Position = 0)]
    [string]$Command = "help"
)

function Show-Help {
    Write-Host "InsightMesh AI -- common commands" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  install         Install Python deps"
    Write-Host "  install-dev     Install Python deps + dev tools"
    Write-Host "  install-spacy   Download the spaCy English model"
    Write-Host ""
    Write-Host "  backend         Run FastAPI server"
    Write-Host "  frontend        Run Vite dev server"
    Write-Host ""
    Write-Host "  test            Run fast test suite"
    Write-Host "  test-slow       Run only slow/integration tests"
    Write-Host "  test-all        Run everything"
    Write-Host "  coverage        Generate coverage report"
    Write-Host ""
    Write-Host "  lint            Lint Python + frontend"
    Write-Host "  format          black + ruff --fix"
    Write-Host ""
    Write-Host "  docker-up       docker compose up --build -d"
    Write-Host "  docker-down     docker compose down"
    Write-Host "  docker-logs     docker compose logs -f"
    Write-Host ""
    Write-Host "  clean           Remove __pycache__, .pytest_cache, dist/"
}

switch ($Command.ToLower()) {
    "help"          { Show-Help }
    "install"       { pip install -r requirements.txt }
    "install-dev"   {
        pip install -r requirements.txt
        pip install pytest pytest-asyncio pytest-cov ruff black mypy
    }
    "install-spacy" { python -m spacy download en_core_web_sm }

    "backend"       { uvicorn backend.main:app --reload --port 8000 }
    "frontend"      { Set-Location insightmesh-fe; npm run dev }

    "test"          { pytest -v }
    "test-slow"     { pytest -v -m slow }
    "test-all"      { pytest -v -m "" }
    "coverage"      {
        pytest --cov=backend --cov-report=term-missing --cov-report=html
        Write-Host "HTML report at htmlcov/index.html" -ForegroundColor Green
    }

    "lint" {
        Write-Host "ruff..." -ForegroundColor Cyan
        ruff check backend tests
        Write-Host "eslint..." -ForegroundColor Cyan
        Push-Location insightmesh-fe
        npm run lint
        Pop-Location
    }
    "format" {
        Write-Host "black..." -ForegroundColor Cyan
        black backend tests
        Write-Host "ruff --fix..." -ForegroundColor Cyan
        ruff check backend tests --fix
    }

    "docker-up"     {
        docker compose up --build -d
        Write-Host "Frontend: http://localhost:5173 | Backend: http://localhost:8000" -ForegroundColor Green
    }
    "docker-down"   { docker compose down }
    "docker-logs"   { docker compose logs -f --tail=200 }

    "clean" {
        Get-ChildItem -Path . -Include __pycache__,.pytest_cache,.ruff_cache,.mypy_cache,htmlcov,*.egg-info -Recurse -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .coverage, insightmesh-fe\dist, insightmesh-fe\.vite
        Write-Host "Cleaned." -ForegroundColor Green
    }

    "reset-db" {
        Remove-Item -Force -ErrorAction SilentlyContinue backend\data\insightmesh.db, backend\data\insightmesh.db-shm, backend\data\insightmesh.db-wal
        Write-Host "DB deleted. Restart backend to recreate." -ForegroundColor Yellow
    }

    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Show-Help
    }
}
