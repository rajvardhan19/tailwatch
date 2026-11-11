.PHONY: install check test
install:
	uv sync --locked
check:
	uv run --locked ruff check src tests migrations scripts
	uv run --locked ruff format --check src tests migrations scripts
	uv run --locked mypy
test:
	uv run --locked pytest tests/unit
