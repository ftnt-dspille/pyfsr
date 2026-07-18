"""Test/doctest support: verified-live fixtures + a replay transport.

This package ships with pyfsr so that **guide and docstring examples can run
offline** under ``make doctest``. It is *not* a feature of the appliance API —
it is the harness that lets examples show real return shapes without a live box.

Public surface (all a doctest needs)::

    from pyfsr._testing import demo_box, demo_client
    box = demo_box()          # healthy Appliance over a ReplayTransport
    client = demo_client()   # FortiSOAR over a replay REST session

The captures backing the appliance replay live in
:mod:`pyfsr._testing.appliance_captures` (real stdout from a lab appliance,
frozen with provenance); the REST captures live in
:mod:`pyfsr._testing.client_captures`. See those modules' docstrings for the
refresh-on-version-bump workflow.
"""

from __future__ import annotations

from .appliance_captures import CAPTURE_DATE, CAPTURE_HOST, CAPTURE_VERSION
from .replay import ReplayTransport, demo_box
from .replay_http import ReplaySession, demo_client, demo_client_jwt

__all__ = [
    "ReplayTransport",
    "demo_box",
    "demo_client",
    "demo_client_jwt",
    "ReplaySession",
    "CAPTURE_HOST",
    "CAPTURE_VERSION",
    "CAPTURE_DATE",
]
