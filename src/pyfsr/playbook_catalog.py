"""Step-type catalog for playbook authoring -- the data behind ``pyfsr playbook
steps`` / ``step-help``.

The catalog itself lives in the compiler package (:mod:`fsr_playbooks.catalog`),
which owns every data source it composes (the resolver's friendly->canonical
table, the typed-arg schemas, the packaged ``fsr_reference.db``, and the
decompiler). This module is a thin re-export so existing pyfsr callers and the
``pyfsr playbook steps`` / ``step-help`` CLI keep importing from
``pyfsr.playbook_catalog`` -- and so the ``pyfsr[playbooks]`` extra's
missing-dependency error surfaces with a pyfsr-flavored message.

The foundational playbook library index (``list_library`` / ``library_manifest``
/ ``library_show``) is a separate, pyfsr-repo-specific concern -- see
:mod:`pyfsr.playbook_library`.
"""

from __future__ import annotations

from .authoring import _load_compiler

# Importing the compiler surfaces the friendly "install pyfsr[playbooks]" error
# when the optional extra is absent, before we reach into its submodules.
_load_compiler()

from fsr_playbooks.catalog import (  # noqa: E402 -- gated on the extra check above
    StepHelp,
    StepTypeInfo,
    list_step_types,
    step_help,
)

__all__ = ["StepHelp", "StepTypeInfo", "list_step_types", "step_help"]
