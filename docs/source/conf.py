import os
import sys
from datetime import date
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Add the src directory to the system path
sys.path.insert(0, os.path.abspath("../../src"))

# Build fsr_playbooks' pydantic models NOW, before sphinx reads any document.
#
# reference-models.md documents 130 pyfsr pydantic models, and autodoc resolves
# them at READ time -- before any doctest executes. That inspection perturbs the
# state pydantic uses to resolve a STRING annotation (these modules use
# `from __future__ import annotations`), so a LATER `import fsr_playbooks` -- the
# one inside guides/playbook-authoring.md's `compile_playbook_yaml` doctest --
# died with "PydanticSchemaGenerationError: The type annotation for
# `__pydantic_extra__` must be `dict[str, ...]`" (fsr_playbooks builds
# `extra="allow"` models: compiler/typed_args/steps/set_variable.py ArgListEntry).
#
# conf.py runs before autodoc, so importing here builds those schemas while the
# state is still clean; the doctest's import then hits sys.modules and is a no-op.
try:  # optional dep: docs still build without the playbook compiler installed
    import fsr_playbooks.compiler  # noqa: F401
except Exception:  # pragma: no cover
    pass

# -- Project information -----------------------------------------------------
project = "pyfsr"
author = "Dylan Spille"
copyright = f"{date.today().year}, {author}"

# Single source of truth: derive the version from the installed package
# (hatch-vcs sets it from the git tag), so the docs never drift from a
# hardcoded number. Falls back gracefully if the package isn't installed.
try:
    release = _pkg_version("pyfsr")
except PackageNotFoundError:  # not installed (e.g. bare checkout)
    release = "0.0.0+unknown"
# Clean X.Y.Z for the header: strip any hatch-vcs dev/local suffix
# (e.g. "0.4.2.dev9+gbdfbcab0b.d20260616" -> "0.4.2") so the patch number
# shows without the long, noisy build metadata overflowing the brand.
version = ".".join(release.split("+")[0].split(".")[:3])

# DOCS_SKIP_AUTOAPI=1 drops the AutoAPI tree from the build. Only `make doctest`
# sets it: AutoAPI parses every module under src/pyfsr and is ~70% of that
# build's wall clock, yet contributes ZERO doctests. `>>>` blocks in docstrings
# aren't collected (doctest_test_doctest_blocks = "" below) and no docstring
# carries an explicit ``.. doctest::`` directive — tests/unit/test_docstring_doctests.py
# is what covers docstring examples. The doctest count is identical either way;
# if that ever stops being true, --check-floor fails and this gate is the reason.
_skip_autoapi = os.environ.get("DOCS_SKIP_AUTOAPI")

# -- Extensions --------------------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",  # execute doctest blocks via `make doctest`
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    *([] if _skip_autoapi else ["autoapi.extension"]),
    "myst_parser",  # author guides in Markdown (.md) alongside .rst
    "sphinx_design",  # grid cards / tabs on the landing page
    "sphinx_copybutton",  # one-click copy on code blocks
]

# Suppress the Python-domain "more than one target found for cross-reference"
# ambiguity only (Sphinx emits it as ``type='ref', subtype='python'``). This
# fires when two attributes share a name that also appears as a bare builtin
# in a ``type[...]`` annotation — e.g. ``ModuleField.type`` and
# ``LicenseDetails.type`` both shadowing the builtin ``type`` used in
# ``model_for() -> type[BaseRecord]``. Missing-reference warnings are emitted
# with a *role* subtype (``ref.meth`` / ``ref.class`` / ...), so they are NOT
# masked here — the nitpicky ``-n`` gate still catches unresolved xrefs.
suppress_warnings = ["ref.python"]

# index.md's toctree lists the reference pages excluded from doctest builds
# (see exclude_patterns); silence that one warning only in that mode, so the
# nitpicky `-W` html build keeps reporting every real toctree break.
if _skip_autoapi:
    suppress_warnings += ["toc.not_readable"]

# Author guides in Markdown; keep .rst working for the AutoAPI output.
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

# MyST niceties: colon-fences (for sphinx-design directives) and smart links.
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

# `make doctest` — every doctest block runs with these names pre-imported, so
# guide examples stay focused on the API rather than boilerplate imports.
# `demo_box()` builds a healthy Appliance over a replay transport seeded with
# verified-live captures, so appliance return-example doctests run offline.
# `demo_client()` builds a FortiSOAR client over a replay REST session seeded
# with recorded /api/3 captures, so API-guide return-example doctests run offline.
doctest_global_setup = "from pyfsr import Query\nfrom pyfsr._testing import demo_box, demo_client, demo_client_jwt"

# Only execute *explicit* doctest directives (```{doctest} / .. doctest::). The
# many illustrative `>>>` snippets in API docstrings reference live clients and
# undefined names — they document shape, not runnable code — so don't collect
# them. As guide/docstring examples are converted to real doctests, they opt in
# via the directive and start getting verified by CI.
doctest_test_doctest_blocks = ""

# -- Napoleon (docstring style) ----------------------------------------------
# pyfsr docstrings use Google style (``Args:`` / ``Returns:`` / ``Raises:``).
# Pin the config explicitly rather than relying on defaults so the rendered
# signatures stay stable: NumPy parsing off, params rendered as a typed field
# list, and ``Returns`` kept untyped-rtype-free for cleaner output.
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_use_ivar = True
napoleon_include_init_with_doc = False

# SPHINX_OFFLINE=1 drops third-party inventories (requests, pydantic) that
# fail when a VPN/proxy does SSL inspection, turning network errors into -W
# build failures. The Python stdlib inventory (docs.python.org) survives VPN.
# CI runs without this var and fetches all three.
_offline = os.environ.get("SPHINX_OFFLINE")
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    **(
        {}
        if _offline
        else {
            "requests": ("https://docs.python-requests.org/en/latest/", None),
            "pydantic": ("https://docs.pydantic.dev/latest/", None),
        }
    ),
}

# Cross-references autoapi emits that have no resolvable target (base classes /
# internal modules pulled in by the nitpicky `-n` build). Listed here so the
# warnings-as-errors (`-W`) docs build stays green without weakening it elsewhere.
nitpick_ignore = [
    ("py:class", "FortiSOAR"),
    ("py:class", "pyfsr.FortiSOAR"),
    ("py:class", "pyfsr.models.BaseRecord"),
    # Unqualified forms autodoc emits from bare annotations: `Actor` is a union
    # alias (not a class) and `ConfigSchema` is internal to _integration.
    ("py:class", "Actor"),
    ("py:class", "ConfigSchema"),
    # ApiResult is re-exported from pyfsr.models but documented at its canonical
    # private module; the "Bases:" xref from public subclasses (FreshnessReport,
    # FreshnessProbe) resolves at runtime but not under `-n`.
    ("py:class", "pyfsr.models.ApiResult"),
    ("py:obj", "pyfsr.models.ApiResult"),
    ("py:obj", "pyfsr.models._generated"),
    ("py:mod", "pyfsr.models._generated"),
    ("py:class", "pydantic.main.BaseModel"),
    ("py:class", "pydantic.BaseModel"),
    ("py:obj", "pydantic.BaseModel"),
    # requests.Response: resolved via intersphinx when online; ignored offline
    # (SPHINX_OFFLINE=1 skips the requests inventory to avoid VPN SSL failures).
    ("py:class", "requests.Response"),
    # argparse._SubParsersAction is a private stdlib type not exported by the
    # Python intersphinx inventory.
    ("py:class", "argparse._SubParsersAction"),
    # Typing artifacts autoapi can't resolve under `-n`: the `...` in
    # ``Callable[..., Any]`` / ``tuple[str, ...]`` renders as an Ellipsis xref,
    # and HydraPage's xref doesn't resolve from projection's autosummary context.
    ("py:class", "Ellipsis"),
    ("py:class", "HydraPage"),
    ("py:class", "pyfsr.pagination.HydraPage"),
    # The optional MCP server's return type lives in the third-party mcp SDK,
    # which isn't part of the docs intersphinx set.
    ("py:class", "mcp.server.lowlevel.Server"),
    ("py:class", "Server"),
    # Bare TypeVar in generic signatures (RecordSet[T], HydraPage[T]) — autoapi
    # emits the TypeVar name as an xref with no resolvable target under `-n`.
    ("py:class", "T"),
    # Short re-export / nested-class names autoapi renders from annotations but
    # documents under their canonical (longer) path.
    ("py:class", "Facts"),
    ("py:class", "Transport"),
    ("py:class", "QueryBody"),
    ("py:class", "FileRecord"),
    # Module-level type aliases (``Callable[...]``) autoapi renders as xrefs from
    # function signatures; they have no class target under `-n` (sphinx <9).
    ("py:class", "StepMatcher"),
    ("py:class", "PlaybookPredicate"),
    ("py:class", "SurfaceFn"),
    # SolutionPackBuilder is defined in api.export_config but used as a param
    # type in api.solution_packs.create(); autoapi's per-page context there
    # can't resolve the bare cross-module class name (it resolves at runtime).
    ("py:class", "SolutionPackBuilder"),
    # Bare method names in cross-module docstring xrefs (e.g. :meth:`runs`)
    # that resolve at runtime but not in autoapi's per-page context.
    ("py:meth", "run"),
    ("py:meth", "runs"),
    ("py:meth", "get"),
    ("py:meth", "pyfsr.api.modules_admin.ModulesAdminAPI._wait_for_publish"),
    # AgentPackage lives in the private _ai_agent_package module; autoapi's
    # per-page context for pack_agent()'s docstring doesn't resolve the
    # cross-module method xref, though it's valid (and resolves) at runtime.
    ("py:meth", "pyfsr.models.AgentPackage.validate_consistency"),
    # Private shared base for the tasks/incidents CRUD shortcuts; autoapi skips
    # the underscore-prefixed module so the "Bases:" xref has no target.
    ("py:obj", "pyfsr.api._record_module.RecordModuleAPI"),
    # Private Protocol used as a client-typing annotation across authoring.py;
    # autoapi skips underscore-prefixed members so the signature xref has no page.
    ("py:class", "_AuthoringClient"),
    # Private helper referenced from a public function's docstring in
    # playbook_library.py; autoapi skips it (underscore prefix), no target page.
    ("py:func", "_build_fixture_catalog_db"),
    # PlaybookVersion is documented under the private _playbooks module; bare
    # class refs in docstrings resolve at runtime but not under -n.
    ("py:class", "PlaybookVersion"),
    ("py:meth", "PlaybookVersion.parsed_json"),
    # Appliance is documented at pyfsr.appliance.Appliance but the bare
    # pyfsr.Appliance form (used in the cli/appliance module docstring) has
    # no autoapi target under -n.
    ("py:class", "pyfsr.Appliance"),
]

# Type annotations autoapi renders as xrefs that resolve at runtime but not
# under `-n`: model classes are documented at their canonical module path, so
# the package-root / private-module annotation forms have no target here.
nitpick_ignore_regex = [
    # NOTE: `(r"py:class", r"pyfsr\.models[\._].*")` used to live here, masking
    # EVERY xref into pyfsr.models. The models are re-exported from private
    # submodules that autoapi does not page, so all ~52 `:class:`~pyfsr.models.X``
    # refs in our docstrings were silently dead. `reference-models.md` now
    # documents them under their public names, so those refs resolve for real and
    # the mask is gone. Only the private module paths still need one (below).
    (r"py:mod", r"pyfsr\.models\._.*"),
    (r"py:(class|func)", r"pyfsr\.cli\..*"),
    # Sphinx's Python domain splits a subscripted annotation on the comma and
    # then tries to xref each fragment: `dict[str, Any]` becomes a lookup for the
    # literal `dict[str`. A real class name can never contain a bracket, so any
    # target with one is a parser artifact of rendering pydantic field
    # annotations (`:undoc-members:` on reference-models.md), NOT a broken link.
    (r"py:class", r".*\[.*"),
    # Private model internals: the union alias's home (`pyfsr.models._system.Actor`),
    # internal classes referenced from public annotations (`_integration.ConfigSchema`),
    # and private bases surfaced by `:show-inheritance:` (`_playbooks._RequestModel`).
    # Public model names resolve for real via reference-models.md.
    (r"py:class", r"pyfsr\.models\._.*"),
    # `pyfsr.models.Actor` is a union ALIAS (User | Appliance | ApiKey), not a
    # class, so it has no target under its public name either.
    (r"py:class", r"pyfsr\.models\.Actor"),
]

# -- AutoAPI configuration ---------------------------------------------------
autoapi_type = "python"
autoapi_dirs = ["../../src/pyfsr"]
# `pyfsr.resources` is a data-only package (ships the bundled OpenAPI spec); it
# has no public Python API, so AutoAPI would emit an all-but-empty page. Skip it.
# `pyfsr._testing` is the doctest/test harness (replay transport + fixtures), not
# a feature of the appliance API — it backs the doctested return examples but
# shouldn't surface as a top-level API page.
# `pyfsr/playbook_library.py` indexes the in-repo `examples/playbooks/library/`
# corpus for the `pyfsr playbook examples` CLI; it's repo-only (never packaged),
# so it has no public API surface for installed-package users to reference.
autoapi_ignore = ["*/resources/*", "*/_testing/*", "*/playbook_library.py"]
autoapi_keep_files = True
# Drop AutoAPI's own top-level toctree entry; we surface it under our
# "API Reference" section instead, so there's a single, unambiguous nav path.
autoapi_add_toctree_entry = False
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
    # "imported-members" dropped: it re-documented package-root re-exports
    # (HydraPage, Query, ...) in both pyfsr and their home modules, producing
    # "duplicate object description" warnings that fail the `-W` build.
]

# -- HTML output -------------------------------------------------------------
html_theme = "furo"
templates_path = ["_templates"]
html_static_path = ["_static"]
html_css_files = ["custom.css"]

html_title = f"pyfsr {version}"

# Furo theme tuning: brand colors (FortiSOAR-ish red/slate), GitHub link,
# and an edit-friendly footer. Light + dark variants both defined.
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "light_css_variables": {
        "color-brand-primary": "#c8102e",
        "color-brand-content": "#c8102e",
    },
    "dark_css_variables": {
        "color-brand-primary": "#ff5a6e",
        "color-brand-content": "#ff5a6e",
    },
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/ftnt-dspille/pyfsr",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" '
                'stroke-width="0" viewBox="0 0 16 16"><path fill-rule="evenodd" '
                'd="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 '
                "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13"
                "-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66"
                ".07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15"
                "-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 "
                "1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 "
                "1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 "
                '1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z">'
                "</path></svg>"
            ),
            "class": "",
        },
    ],
}

# Don't try to copy the prompt characters (>>> / $) when using the copy button.
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

# Exclude patterns
# `autoapi/pyfsr/index` is AutoAPI's package-landing page; its auto-generated
# "Submodules" toctree duplicates every module into a second parallel nav tree
# (sidebar showed each module twice). reference.md curates the canonical flat
# list, so drop the landing page rather than surface its competing tree.
exclude_patterns = ["build", "autoapi/pyfsr/index.rst"]

# With AutoAPI off (doctest builds), the pages that toctree its output would
# emit "toctree contains reference to nonexisting document" warnings, so drop
# the stale generated tree and the two curated pages that index it. Neither
# holds a doctest, so nothing is lost from the run.
if _skip_autoapi:
    exclude_patterns += ["autoapi/**", "reference.md", "reference-advanced.md"]
