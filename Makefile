.PHONY: setup dev build run test check

setup:
	uv sync --locked
	npm --prefix frontend ci

dev:
	bash scripts/dev.sh

build:
	npm --prefix frontend run build

run: build
	uv run uvicorn dreamer.main:app --app-dir backend --host 127.0.0.1 --port 8000

test:
	uv run pytest -q

check: test build
	uv run ruff format --check backend
