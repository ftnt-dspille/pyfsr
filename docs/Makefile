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

# Clean build directory
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

# Run doctests
doctest:
	$(SPHINXBUILD) -b doctest "$(SOURCEDIR)" "$(BUILDDIR)/doctest" $(SPHINXOPTS)
	@echo "Testing of doctests in the sources finished, look at the " \
	      "results in $(BUILDDIR)/doctest/output.txt."

# Validate unenforced code examples in the guides. Two layers, both run here:
#   1. check_doc_examples.py -- static lint: pyfsr symbols + CLI flags named in
#      plain fenced ```python / ```sh blocks still exist (catches namespace/flag
#      drift that `make doctest` can't see, since it only runs {doctest}
#      directives).
#   2. exec_cli_examples.py -- really run the offline `pyfsr playbook` commands
#      the guides teach, against library fixtures, asserting exit codes + output
#      (proves the commands actually work, not just that their flags exist).
check-examples:
	PYTHONPATH=$(CURDIR)/../src:$$PYTHONPATH python $(CURDIR)/../scripts/check_doc_examples.py
	PYTHONPATH=$(CURDIR)/../src:$$PYTHONPATH python $(CURDIR)/../scripts/exec_cli_examples.py
	@echo "Doc-example validation complete."

# Generate coverage report
coverage:
	$(SPHINXBUILD) -b coverage "$(SOURCEDIR)" "$(BUILDDIR)/coverage" $(SPHINXOPTS)
	@echo "Testing of coverage in the sources finished, look at the " \
	      "results in $(BUILDDIR)/coverage/python.txt."

# Default target
.DEFAULT_GOAL := html
