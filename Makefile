.DEFAULT_GOAL := help
PYTHON_VERSION := 3.13

help: ## Show command list
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: deps hooks ## Install dependencies and git hooks

deps: ## Install dependencies
	uv sync

deps-up: ## Upgrade dependencies
	uv sync --upgrade

hooks: ## Install git hooks
	uv run pre-commit install

fmt: ## Format source files
	uv run ruff format .

lint: ## Run Ruff lint checks
	uv run ruff check .

typecheck: ## Run type checks
	uv run ty check .

test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=src --cov-report=term-missing

build: ## Build Python package artifacts
	uv build

check: ## Run all project checks
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check .
	uv run pytest

docker-build: ## Build the Docker image locally
	docker build -t dokployer .

docker-run: ## Show Docker CLI help in the image
	docker run --rm -i dokployer dokployer --help

clean: ## Remove caches and build artifacts
	rm -rf .coverage .pytest_cache .ruff_cache .venv .uv-cache build dist htmlcov
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
