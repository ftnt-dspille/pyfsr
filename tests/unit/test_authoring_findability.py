"""Findability guard (PLAYBOOK_AUTHORING_DX_PLAN.md 0d).

The whole authoring-DX effort is about *discoverability*: a helper that exists
but isn't reachable is as good as missing. This test fails when a shipped
authoring helper is not referenced from the three channels an agent reaches it
through:

  1. the ``pyfsr playbook`` CLI index (the enumerable entry point);
  2. a "see also" pointer on its natural sibling's docstring (the method an
     agent is already holding);
  3. the authoring guide.

If you add an authoring helper, add it to ``_HELPERS`` below **and** to each
referenced surface — that is the point of the guard. If you intentionally drop a
channel for one helper, set it to ``None`` with a comment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PB_CLI = _ROOT / "src" / "pyfsr" / "cli" / "playbook.py"
_CLI_MAIN = _ROOT / "src" / "pyfsr" / "cli" / "__main__.py"
_MANUAL_INPUT = _ROOT / "src" / "pyfsr" / "api" / "manual_input.py"
_PLAYBOOKS = _ROOT / "src" / "pyfsr" / "api" / "playbooks.py"
_GUIDE = _ROOT / "docs" / "source" / "guides" / "playbook-authoring.md"


# helper token -> (index file, sibling-docstring file, guide file)
# Each value is a file the token must appear in. None = channel N/A for this one.
_HELPERS: dict[str, tuple[Path | None, Path | None, Path | None]] = {
    # CLI discovery commands: enumerated in the group help (cli/__main__.py),
    # described in the playbook CLI module, and shown in the guide.
    "steps": (_CLI_MAIN, _PB_CLI, _GUIDE),
    "step-help": (_CLI_MAIN, _PB_CLI, _GUIDE),
    # manual_input.answer: pointed to from the group index epilog, cross-linked
    # from its siblings (list/resume) in the same module, and in the guide.
    "manual_input.answer": (_CLI_MAIN, None, _GUIDE),
    "answer": (None, _MANUAL_INPUT, None),  # see-also on list/resume
    # run-tree / step-status runtime helpers: see-also on trigger/run_env, guide.
    "run_tree": (None, _PLAYBOOKS, _GUIDE),
    "step_status": (None, _PLAYBOOKS, _GUIDE),
}

_CHANNEL_NAMES = ("pyfsr playbook index", "sibling docstring", "authoring guide")


@pytest.mark.parametrize("token,channels", _HELPERS.items())
def test_authoring_helper_is_discoverable(token, channels):
    for channel_name, path in zip(_CHANNEL_NAMES, channels):
        if path is None:
            continue
        text = path.read_text(encoding="utf-8")
        assert token in text, (
            f"authoring helper {token!r} is not referenced from its "
            f"{channel_name} ({path.relative_to(_ROOT)}). Either reference it "
            f"there or update tests/unit/test_authoring_findability.py."
        )
