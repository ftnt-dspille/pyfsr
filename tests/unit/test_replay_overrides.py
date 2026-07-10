"""The ``demo_client(overrides=...)`` scoping mechanism.

The replay harness is a stateless global fixture table, so a doctest that needs a
stateful view (a module staged but not yet published) uses a per-session
``overrides`` overlay. These tests guard the two properties that make it safe:
the overlay takes effect for its own session, and it never leaks into the shared
table (so ``pending_changes() == []`` stays true everywhere else).
"""

from __future__ import annotations

import pytest

pytest.importorskip("pyfsr._testing.replay_http")

from pyfsr._testing.client_captures import pending_create_overlay
from pyfsr._testing.replay_http import demo_client


def test_baseline_has_no_pending_changes():
    assert demo_client().modules_admin.pending_changes() == []


def test_overlay_stages_a_created_module():
    admin = demo_client(overrides=pending_create_overlay("crew")).modules_admin
    changes = admin.pending_changes()
    assert [(c.module, c.change) for c in changes] == [("crew", "created")]


def test_overlay_supports_multiple_modules():
    admin = demo_client(overrides=pending_create_overlay(["crew", "heists"])).modules_admin
    changes = admin.pending_changes()
    assert [(c.module, c.change) for c in changes] == [
        ("crew", "created"),
        ("heists", "created"),
    ]


def test_overlay_does_not_leak_into_the_global_table():
    # Use an overlay, then a fresh baseline client must still read empty — the
    # module-global _FIXTURES table was never mutated.
    demo_client(overrides=pending_create_overlay("crew")).modules_admin.pending_changes()
    assert demo_client().modules_admin.pending_changes() == []
