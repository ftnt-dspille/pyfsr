"""Unit tests for AuditAPI.execution_context — the A3 cross-referencing method."""

from pyfsr.api.audit import AuditAPI


class _FakePlaybooks:
    """Minimal stand-in for ``client.playbooks`` with ``get_execution``."""

    def __init__(self, run_detail: dict):
        self._run_detail = run_detail

    def get_execution(self, pk, step_detail=False):
        from pyfsr.models._playbooks import RunSummary

        return RunSummary.model_validate(self._run_detail)


class _FakeClient:
    """Fake client with a ``playbooks`` attr and a ``post`` for audit activities."""

    def __init__(self, run_detail: dict, audit_pages: list[dict]):
        self.playbooks = _FakePlaybooks(run_detail)
        self._audit_pages = audit_pages
        self._page_idx = 0

    def post(self, endpoint, data=None, params=None, **kw):
        if "activities/count" in endpoint:
            total = sum(len(p.get("content", [])) for p in self._audit_pages)
            return {"total": total}
        if "activities" in endpoint:
            page = (data or {}).get("page", 0)
            if page < len(self._audit_pages):
                return self._audit_pages[page]
            return {"number": page, "content": []}
        return {}


# A run detail with env.record_iri — the key field execution_context extracts.
_RUN_DETAIL = {
    "@id": "/wf/api/workflows/100/",
    "@type": "Workflow",
    "name": "Extract Indicators (Alerts)",
    "status": "finished",
    "pk": "100",
    "created": "2026-07-26T13:11:00Z",
    "modified": "2026-07-26T13:11:06Z",
    "env": {
        "record_iri": "/api/3/alerts/abc12300-0000-0000-0000-000000000001",
        "resource": "alerts",
        "input": {"params": {}, "records": []},
    },
    "steps": [],
    "result": {},
}

# Audit items for that record — two within the window, one before.
# Run window: 2026-07-26T13:11:00Z to 13:11:06Z; with ±120s buffer →
#   13:09:00 to 13:13:06. Epoch ms for 2026-07-26T13:11:00Z ≈ 1785071460000.
_AUDIT_PAGE_0 = {
    "number": 0,
    "content": [
        {
            "id": 1,
            "operation": "Create",
            "transactionDate": 1785071100000,  # ~13:05:00 — clearly before the window
            "user": "Playbook",
            "playbookName": "Ingest Alert",
            "entityType": "alerts",
            "entityUuid": "abc12300-0000-0000-0000-000000000001",
            "title": "Alert created",
        },
        {
            "id": 2,
            "operation": "Link",
            "transactionDate": 1785071461000,  # ~13:11:01 — within the run window
            "user": "Playbook",
            "playbookName": "Extract Indicators (Alerts)",
            "entityType": "alerts",
            "entityUuid": "abc12300-0000-0000-0000-000000000001",
            "title": "Indicator linked",
        },
        {
            "id": 3,
            "operation": "Comment",
            "transactionDate": 1785071462000,  # ~13:11:02 — within the run window
            "user": "Playbook",
            "playbookName": "> Link ATT&CK Technique",
            "entityType": "alerts",
            "entityUuid": "abc12300-0000-0000-0000-000000000001",
            "title": "ATT&CK comment added",
        },
        {
            "id": 4,
            "operation": "Trigger",
            "transactionDate": 1785071463000,  # ~13:11:03 — within the run window
            "user": "Playbook",
            "playbookName": "> Link ATT&CK Technique",
            "entityType": "alerts",
            "entityUuid": "abc12300-0000-0000-0000-000000000001",
            "title": "Triggered by create",
        },
    ],
}


def _make_client():
    return _FakeClient(_RUN_DETAIL, [_AUDIT_PAGE_0])


def test_execution_context_extracts_record_uuid():
    ctx = AuditAPI(_make_client()).execution_context("100")
    assert ctx.record_uuid == "abc12300-0000-0000-0000-000000000001"
    assert ctx.record_iri == "/api/3/alerts/abc12300-0000-0000-0000-000000000001"
    assert ctx.entity_type == "alerts"


def test_execution_context_run_fields():
    ctx = AuditAPI(_make_client()).execution_context("100")
    assert ctx.run_pk == "100"
    assert ctx.run_name == "Extract Indicators (Alerts)"
    assert ctx.run_status == "finished"
    assert ctx.run_created == "2026-07-26T13:11:00Z"
    assert ctx.run_modified == "2026-07-26T13:11:06Z"


def test_execution_context_concurrent_changes():
    ctx = AuditAPI(_make_client()).execution_context("100", window_seconds=120)
    # Items 2, 3, 4 are within the window (run 13:11:00-13:11:06 ± 120s)
    # Item 1 is before the window
    assert len(ctx.concurrent_changes) == 3
    ops = [c.operation for c in ctx.concurrent_changes]
    assert "Link" in ops
    assert "Comment" in ops
    assert "Trigger" in ops


def test_execution_context_before_changes():
    ctx = AuditAPI(_make_client()).execution_context("100", window_seconds=120)
    # Item 1 (Create) is before the window
    assert len(ctx.before_changes) == 1
    assert ctx.before_changes[0].operation == "Create"
    assert ctx.before_changes[0].playbook_name == "Ingest Alert"


def test_execution_context_concurrent_runs_from_trigger_ops():
    ctx = AuditAPI(_make_client()).execution_context("100", window_seconds=120)
    # The Trigger audit entry marks another playbook ("> Link ATT&CK Technique")
    # being triggered on this record during the run
    assert len(ctx.concurrent_runs) == 1
    assert ctx.concurrent_runs[0]["name"] == "> Link ATT&CK Technique"
    assert ctx.concurrent_runs[0]["source"] == "audit"


def test_execution_context_other_playbooks():
    ctx = AuditAPI(_make_client()).execution_context("100", window_seconds=120)
    assert "> Link ATT&CK Technique" in ctx.other_playbooks
    assert "Extract Indicators (Alerts)" not in ctx.other_playbooks


def test_execution_context_summary():
    ctx = AuditAPI(_make_client()).execution_context("100", window_seconds=120)
    s = ctx.summary()
    assert "Extract Indicators (Alerts)" in s
    assert "finished" in s
    assert "concurrent changes" in s
    assert "Link ATT&CK" in s


def test_execution_context_no_record_iri():
    """A run without record_iri returns empty context but doesn't crash."""
    run = {**_RUN_DETAIL, "env": {}}
    client = _FakeClient(run, [])
    ctx = AuditAPI(client).execution_context("100")
    assert ctx.record_uuid is None
    assert ctx.concurrent_changes == []
    assert ctx.before_changes == []
