"""The typed-models reference page must stay in sync with ``pyfsr.models.__all__``.

`docs/source/reference-models.md` is generated (`scripts/gen_models_reference.py`).
It exists because the model classes live in private submodules that autoapi does not
page, so ``pyfsr.models.X`` has no doc target unless documented under its public
name. When it drifts, the failure is invisible in the worst way: a new model's
``:class:`~pyfsr.models.X``` xrefs simply resolve to nothing, and the strict
``-W -n`` docs build only catches it for *some* role types (a `py:class` miss was
masked for years; only a `py:attr` miss surfaced it).

So pin it here instead of trusting the docs build to notice.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pyfsr.models as models

PAGE = Path(__file__).resolve().parents[2] / "docs" / "source" / "reference-models.md"


def _documented() -> set[str]:
    """Class names the page carries an ``autoclass`` directive for."""
    text = PAGE.read_text(encoding="utf-8")
    return set(re.findall(r"^\.\. autoclass:: pyfsr\.models\.(\w+)$", text, re.M))


def _public_classes() -> set[str]:
    """Public names in ``__all__`` that are actually classes (autoclass-able).

    Non-classes are excluded by design: ``Actor`` is a union alias, ``RecordIRI`` /
    ``PicklistIRI`` are type aliases, ``MODEL_REGISTRY`` is a dict and ``model_for``
    a function — ``autoclass`` cannot document any of them.
    """
    return {n for n in models.__all__ if inspect.isclass(getattr(models, n, None))}


def test_page_documents_every_public_model():
    missing = _public_classes() - _documented()
    assert not missing, (
        f"{len(missing)} public model(s) missing from {PAGE.name}: {sorted(missing)}. "
        "Run `python scripts/gen_models_reference.py`. Until then, every "
        "`:class:`~pyfsr.models.X`` xref to them silently resolves to nothing."
    )


def test_page_documents_nothing_that_is_not_public():
    extra = _documented() - _public_classes()
    assert not extra, (
        f"{PAGE.name} documents name(s) not in pyfsr.models.__all__: {sorted(extra)}. "
        "Either re-export them or regenerate the page — autoclass on a missing "
        "attribute fails the strict docs build."
    )
