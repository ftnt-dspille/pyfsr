.DEFAULT_GOAL := help
.PHONY: help lint format test typecheck check release release-check

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  release/release-check take VERSION, e.g. make release VERSION=0.18.8"

lint: ## ruff check (same gate as CI)
	ruff check .

format: ## ruff format --check (same gate as CI)
	ruff format --check .

typecheck: ## mypy over the typed surface
	mypy

test: ## pytest with coverage
	pytest tests/ --cov=pyfsr --cov-report=term-missing

check: lint format typecheck test ## Everything CI runs, locally

release-check: ## Release preflight only -- changes nothing
	@test -n "$(VERSION)" || { echo "usage: make release-check VERSION=0.18.8" >&2; exit 2; }
	@scripts/release.sh "$(VERSION)" --check

release: ## Preflight, tag, GitHub release, then wait until installable
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=0.18.8" >&2; exit 2; }
	@scripts/release.sh "$(VERSION)"
