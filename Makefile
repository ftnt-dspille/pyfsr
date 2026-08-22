.DEFAULT_GOAL := help
.PHONY: help lint format test typecheck check docs docs-doctest docs-examples \
	docs-check release release-check

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

# The docs gates. Kept out of `check` on purpose: a full Sphinx build is
# minutes where the rest of `check` is seconds, and AutoAPI alone is ~70% of
# that. Run `docs-check` before touching anything public-facing.
#
# `-W -n` is the gate pyproject.toml pins the Sphinx floor for: `-n` (nitpicky)
# turns an unresolvable cross-reference into a warning and `-W` turns every
# warning into an error, so a typo'd `:class:` fails the build instead of
# rendering as plain text nobody notices.
docs: ## Build the HTML docs under the nitpicky -W -n gate
	$(RUN) sphinx-build -b html -W -n docs/source docs/build/html

docs-doctest: ## Run the doctests embedded in docs/source
	DOCS_SKIP_AUTOAPI=1 $(RUN) sphinx-build -b doctest docs/source docs/build/doctest

docs-examples: ## Fail if a file's {doctest} count dropped below its baseline
	$(RUN) python scripts/check_doc_examples.py --check-floor

docs-check: docs docs-doctest docs-examples ## Every docs gate


release-check: ## Release preflight only -- changes nothing
	@test -n "$(VERSION)" || { echo "usage: make release-check VERSION=0.18.8" >&2; exit 2; }
	@scripts/release.sh "$(VERSION)" --check

release: ## Preflight, tag, GitHub release, then wait until installable
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=0.18.8" >&2; exit 2; }
	@scripts/release.sh "$(VERSION)"
