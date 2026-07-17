# Minimal makefile for Sphinx documentation
SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build
SPHINXPROJ    = pyfsr

.PHONY: help clean html linkcheck doctest coverage check-examples

# Help command
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# Full reset: drops the rendered output, sphinx's doctree cache (it lives in
# $(BUILDDIR)/html/.doctrees) and the generated AutoAPI rst. Rebuilding all
# three costs ~55s, vs ~15s for a plain incremental `make html` -- so only
# clean when you actually want a from-scratch build. Editing a guide or a
# docstring does NOT need it; sphinx tracks those and rebuilds what changed.
clean:
	rm -rf $(BUILDDIR)/*
	rm -rf $(SOURCEDIR)/api/*.rst
	rm -rf $(SOURCEDIR)/autoapi/*
	@echo "Cleaned build directory and generated API docs"

# Build HTML documentation
html:
	$(SPHINXBUILD) -b html "$(SOURCEDIR)" "$(BUILDDIR)/html" $(SPHINXOPTS)
	@echo "Build finished. The HTML pages are in $(BUILDDIR)/html."

# Check external links
linkcheck:
	$(SPHINXBUILD) -b linkcheck "$(SOURCEDIR)" "$(BUILDDIR)/linkcheck" $(SPHINXOPTS)
	@echo "Link check complete; look for any errors in the above output " \
	      "or in $(BUILDDIR)/linkcheck/output.txt."

# Run doctests. DOCS_SKIP_AUTOAPI=1 drops the AutoAPI tree: it is ~70% of this
# build's time and contributes no doctests (see conf.py). Docstring examples are
# covered by tests/unit/test_docstring_doctests.py, not by this builder.
doctest:
	DOCS_SKIP_AUTOAPI=1 $(SPHINXBUILD) -b doctest "$(SOURCEDIR)" "$(BUILDDIR)/doctest" $(SPHINXOPTS)
	@echo "Testing of doctests in the sources finished, look at the " \
	      "results in $(BUILDDIR)/doctest/output.txt."

# Validate unenforced code examples in the guides. Four checks run here:
#   1. check_doc_examples.py -- static lint: pyfsr symbols + CLI flags named in
#      plain fenced ```python / ```sh blocks still exist (catches namespace/flag
#      drift that `make doctest` can't see, since it only runs {doctest}
#      directives).
#   2. check_doc_examples.py --check-floor -- anti-regression gate: fails if any
#      docs/source file's {doctest} block count drops below the baseline in
#      doctest_counts.baseline.json, so a {doctest} can't quietly become a plain
#      {code-block}. Bump the baseline with `--update-floor` when intentional.
#   3. exec_cli_examples.py -- really run the offline `pyfsr playbook` commands
#      the guides teach, against library fixtures, asserting exit codes + output
#      (proves the commands actually work, not just that their flags exist).
#   4. check_doc_examples.py --coverage -- advisory per-file block-count report
#      (exit 0); shows where {doctest} coverage exists vs. plain-block gaps.
check-examples:
	PYTHONPATH=$(CURDIR)/../src:$$PYTHONPATH python $(CURDIR)/../scripts/check_doc_examples.py
	PYTHONPATH=$(CURDIR)/../src:$$PYTHONPATH python $(CURDIR)/../scripts/check_doc_examples.py --check-floor
	PYTHONPATH=$(CURDIR)/../src:$$PYTHONPATH python $(CURDIR)/../scripts/exec_cli_examples.py
	PYTHONPATH=$(CURDIR)/../src:$$PYTHONPATH python $(CURDIR)/../scripts/check_doc_examples.py --coverage
	@echo "Doc-example validation complete."

# Generate coverage report
coverage:
	$(SPHINXBUILD) -b coverage "$(SOURCEDIR)" "$(BUILDDIR)/coverage" $(SPHINXOPTS)
	@echo "Testing of coverage in the sources finished, look at the " \
	      "results in $(BUILDDIR)/coverage/python.txt."

# Default target
.DEFAULT_GOAL := html
