"""Execute + output-match the ``>>>`` doctests in curated ``src/`` module docstrings.

``make doctest`` (Sphinx) only runs *explicit* ``{doctest}`` directives in the
guide docs — it does **not** collect ``>>>`` blocks from Python docstrings
(``conf.py`` sets ``doctest_test_doctest_blocks = ""``). That is deliberate for
the many *illustrative* docstring snippets that assume a live client and are not
runnable. But it also means a *real* return-shape example added to a docstring
(the kind the autoapi reference renders) is never enforced — it can drift
silently.

This test closes that gap for a **whitelist** of modules: it runs each module's
docstring ``>>>`` examples through the stdlib ``doctest`` runner with
``demo_client`` / ``demo_box`` / ``Query`` in scope, and fails on any mismatch.
Modules are opted in individually so the existing illustrative ``>>>`` snippets
elsewhere (which reference undefined live clients) stay untouched — every
example in a whitelisted module must either run green or carry
``# doctest: +SKIP``.

To add a module: ensure every ``>>>`` block in it is runnable (via the
``demo_*`` fixtures) or ``+SKIP``, then add it to ``WHITELIST``.
"""

from __future__ import annotations

import doctest
import importlib
import io

import pytest

from pyfsr import Query
from pyfsr._testing import demo_box, demo_client

# Globals available to every docstring example (mirrors conf.py
# `doctest_global_setup`). `demo_client`/`demo_box` build offline replay objects;
# `Query` is the DSL entry point used in the querying examples.
_GLOBS = {"demo_client": demo_client, "demo_box": demo_box, "Query": Query}

# Modules whose docstrings are executed + output-matched here. Each must be
# doctest-clean: every `>>>` example runs green or is `# doctest: +SKIP`.
# api.picklists + api.modules_admin were added once their read-only calls had
# captured fixtures in src/pyfsr/_testing/client_captures.py (staging/published
# schema envelopes + picklist bulk calls).
WHITELIST = [
    "pyfsr.config",
    "pyfsr.query",
    "pyfsr.records",
    "pyfsr.api.connectors",
    "pyfsr.api.alerts",
    "pyfsr.api.audit",
    "pyfsr.api.picklists",
    "pyfsr.api.system",
    "pyfsr.api.taxii",
    "pyfsr.api.auth_config",
    "pyfsr.api.search",
    "pyfsr.api.modules_admin",
    "pyfsr.api.widgets",
    "pyfsr.api.user_settings",
    "pyfsr.api.view_templates",
    "pyfsr.api.feeds",
    "pyfsr.api.api_users",
    "pyfsr.api.api_keys",
    "pyfsr.api.manual_input",
    "pyfsr.api.attachments",
    "pyfsr.api.solution_packs",
    "pyfsr.api.import_config",
    "pyfsr.api.playbooks",
    "pyfsr.pagination",
]


@pytest.mark.parametrize("modname", WHITELIST, ids=lambda m: m)
def test_docstring_doctests(modname: str) -> None:
    """Run a module's docstring ``>>>`` examples; assert none fail."""
    mod = importlib.import_module(modname)
    finder = doctest.DocTestFinder(recurse=True)
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS)
    buf = io.StringIO()
    for test in finder.find(mod, name=modname, extraglobs=dict(_GLOBS)):
        runner.run(test, out=buf.write, clear_globs=False)
    assert runner.failures == 0, (
        f"{modname}: {runner.failures} of {runner.tries} docstring doctests failed\n{buf.getvalue()}"
    )
