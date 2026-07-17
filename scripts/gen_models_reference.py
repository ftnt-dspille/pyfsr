"""Regenerate docs/source/reference-models.md from ``pyfsr.models.__all__``.

The model classes live in private submodules (``_playbooks.py``, ``_generated.py``,
...) that autoapi does not page, so ``pyfsr.models.X`` has no doc target unless we
document it explicitly under its public name. Without that page every
``:class:`~pyfsr.models.X``` xref in the codebase is silently dead (they used to be
masked by a `nitpick_ignore_regex` entry rather than fixed).

Run after adding/removing a model:

    python scripts/gen_models_reference.py

`tests/unit/test_models_reference_page.py` fails if the page drifts from __all__.
"""

import collections
import inspect

import pyfsr.models as m

GROUPS = {
    "_generated": "Records (module entities)",
    "_integration": "Integrations & connectors",
    "_playbooks": "Playbooks & runs",
    "_modules_admin": "Module administration",
    "_export": "Export / import",
    "_system": "System & platform",
    "_ai": "AI & investigations",
    "_agents": "Agents",
    "_ai_agent_package": "AI agent packages",
    "_rules": "Rules",
    "_schedules": "Schedules",
    "_widgets": "Widgets",
    "_app_config": "App configuration",
    "base": "Base classes",
    "types": "Types",
}
by_mod = collections.defaultdict(list)
skipped = []
for name in sorted(m.__all__):
    obj = getattr(m, name, None)
    if not inspect.isclass(obj):
        skipped.append(name)  # enums/aliases/constants -> not autoclass-able
        continue
    home = getattr(obj, "__module__", "").split(".")[-1]
    by_mod[home].append(name)

lines = [
    "# Typed models",
    "",
    "Every typed model re-exported from `pyfsr.models` — the shapes returned by",
    "the client APIs and the validated argument bundles accepted by the write",
    "verbs.",
    "",
    "```{note}",
    "This page is generated from `pyfsr.models.__all__`. The classes themselves",
    "live in private submodules (`_playbooks.py`, `_generated.py`, …), which",
    "autoapi does not page — so documenting them here under their **public**",
    "name is what gives `pyfsr.models.X` a resolvable target. Without it every",
    "`{class}`~pyfsr.models.X`` cross-reference in the docs is silently dead.",
    "```",
    "",
]
for mod in list(GROUPS) + [k for k in sorted(by_mod) if k not in GROUPS]:
    names = by_mod.get(mod)
    if not names:
        continue
    lines += [f"## {GROUPS.get(mod, mod)}", ""]
    for n in names:
        lines += [
            "```{eval-rst}",
            f".. autoclass:: pyfsr.models.{n}",
            "   :members:",
            "   :undoc-members:",
            "   :show-inheritance:",
            "```",
            "",
        ]
print("classes:", sum(len(v) for v in by_mod.values()), "| non-class __all__ entries skipped:", len(skipped))
print("skipped:", skipped[:10])
open("docs/source/reference-models.md", "w").write("\n".join(lines) + "\n")
