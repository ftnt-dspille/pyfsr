"""When a schedule's fire becomes visible (opt-in: pytest -m integration).

An every-minute schedule: its run row appears within the minute, while beat's
``total_run_count`` follows about a minute later, so ``audit`` counts runs as
firing. A schedule whose first fire is not due yet is ``pending``, not a problem.
Uses an existing workflow only as the schedule's target; the every-minute one
fires it, so point ``FSR_SCHEDULE_PROBE_WORKFLOW`` at a no-op playbook IRI.
"""

import os
import time

import pytest

pytestmark = pytest.mark.integration
WF = os.environ.get("FSR_SCHEDULE_PROBE_WORKFLOW")
NAME = f"pyfsr schedule probe {int(time.time()) % 100000}"


@pytest.mark.skipif(not WF, reason="set FSR_SCHEDULE_PROBE_WORKFLOW to a no-op playbook IRI")
def test_a_new_schedule_is_pending_until_due_then_firing(client):
    s = client.schedules
    s.create(NAME, WF, "* * * * *")
    try:
        first = s.audit(NAME, run_limit=3)
        assert first["pending"] is True and first["problems"] == []
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and not (a := s.audit(NAME, run_limit=3))["firing"]:
            time.sleep(10)
        assert a["firing"] and a["problems"] == [], a
    finally:
        s.delete(NAME)
