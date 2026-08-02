.DEFAULT_GOAL := help
.PHONY: help lint format test typecheck check release release-check

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  release/release-check take VERSION, e.g. make release VERSION=0.18.8"

# Run every gate through the project env, never a bare tool name. A bare
# `pytest`/`mypy` resolves off $$PATH, so whichever interpreter comes first
# runs the gate -- and one without the `test` extra fails ~38 tests with
# `ModuleNotFoundError: No module named 'mcp'` and dies on
# `unrecognized arguments: --cov`. That reads as "the suite is broken" when the
# suite is green in the env that actually has the deps (2944 passed), which is
# a costly thing to believe. Same class of trap as a gate matching zero files:
# the failure describes the environment, not the code. `uv run` pins it to
# .venv and syncs it when stale.
RUN := uv run

lint: ## ruff check (same gate as CI)
	$(RUN) ruff check .

format: ## ruff format --check (same gate as CI)
	$(RUN) ruff format --check .

typecheck: ## mypy over the typed surface
	$(RUN) mypy

test: ## pytest with coverage
	$(RUN) pytest tests/ --cov=pyfsr --cov-report=term-missing

check: lint format typecheck test ## Everything CI runs, locally

release-check: ## Release preflight only -- changes nothing
	@test -n "$(VERSION)" || { echo "usage: make release-check VERSION=0.18.8" >&2; exit 2; }
	@scripts/release.sh "$(VERSION)" --check

release: ## Preflight, tag, GitHub release, then wait until installable
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=0.18.8" >&2; exit 2; }
	@scripts/release.sh "$(VERSION)"
