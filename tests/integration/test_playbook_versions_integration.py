"""Live e2e for the playbook snapshot ("Versions" tab) lifecycle.

Closes the gap flagged by [[playbook-revisions-api-followup]]: the snapshot
methods were written from the **editor JS bundle trace**, and every existing test
(`tests/unit/test_playbook_versions.py`, 19 of them) asserts against a
`_VersionsFakeClient` whose responses we wrote ourselves. So the wire shapes were
inferred from JavaScript and had never been confirmed against an appliance — the
unit suite would stay green even if the real API disagreed. This is the first
test that runs the loop against a live box.

The loop, end to end:

    run -> snapshot v1 -> edit the playbook -> snapshot v2 -> list_versions
        -> run (output DIFFERS) -> diff_versions -> restore v1
        -> run (output REVERTS)

**Why the output probe is a created record, not a `set_variable`:** runtime
`set_variable` values are only persisted into the retrievable run record when
*global workflow debug logging* is on (see
`test_do_until_manual_input_integration.py`), so reading one would couple this
test to an appliance-wide setting. The demo playbook instead CREATES an alert
carrying the marker, which is observable from the record itself.

The demo is also self-contained (it does not read `vars.input.records`), so it
needs no record selected at trigger time.

Live-verified on 8.0.0: the full loop passes (ALPHA -> BRAVO -> ALPHA), and both
documented quirks held — `create_version` does NOT echo the `json` blob, and
`list_versions` came back newest-first.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

YAML_PATH = Path(__file__).resolve().parents[2] / "examples" / "playbooks" / "version_lifecycle_demo.yaml"
PB_NAME = "Stamp Marker"
EMIT_STEP = "Emit"
EMITTED_ALERT = "ZZ VLD emitted"
V1_MARKER = "ALPHA"  # the marker as the YAML ships it
V2_MARKER = "BRAVO"  # what we edit it to


@pytest.fixture()
def deployed_demo(client):
    """Deploy the demo collection; delete it + every alert it emitted on teardown."""
    created = client.workflow_collections.import_from_yaml(str(YAML_PATH), replace=True)
    coll_uuid = created[0]["uuid"]
    try:
        yield coll_uuid
    finally:
        client.workflow_collections.delete(coll_uuid)
        for alert in client.get("/api/3/alerts", params={"$limit": 200}).get("hydra:member", []):
            if (alert.get("name") or "") == EMITTED_ALERT:
                client.delete(f"/api/3/alerts/{alert['uuid']}")


def _run_and_read_marker(client) -> str | None:
    """Run the playbook; return the marker on the alert THIS run created.

    Snapshots the pre-existing emitted alerts so a leftover from an earlier run
    can never be mistaken for this one's output.
    """
    before = {
        a["uuid"]
        for a in client.get("/api/3/alerts", params={"$limit": 200}).get("hydra:member", [])
        if (a.get("name") or "") == EMITTED_ALERT
    }
    client.playbooks.trigger(PB_NAME, follow=True, timeout=120)
    time.sleep(3)  # the create_record commit trails the run's terminal status
    for a in client.get("/api/3/alerts", params={"$limit": 200}).get("hydra:member", []):
        if (a.get("name") or "") == EMITTED_ALERT and a["uuid"] not in before:
            return a.get("description")
    return None


def _set_marker(client, marker: str) -> None:
    """Edit the Emit step's marker in place — the one-field 'author edits it' half."""
    uuid = client.playbooks.resolve_iri(PB_NAME).rsplit("/", 1)[-1]
    wf = client.playbooks.get_definition(uuid, relationships=True).to_dict(by_alias=True)
    for step in wf["steps"]:
        if step["name"] == EMIT_STEP:
            step["arguments"]["resource"]["description"] = marker
    client.playbooks.update(uuid, steps=wf["steps"])


def test_snapshot_lifecycle_run_edit_snapshot_list_restore(client, deployed_demo):
    """The whole loop: snapshot -> edit -> snapshot -> run (differs) -> restore (reverts)."""
    # 1. The playbook as shipped emits V1_MARKER.
    first = _run_and_read_marker(client)
    assert first == V1_MARKER, f"demo playbook should emit {V1_MARKER}, got {first!r}"

    # 2. Freeze it. The server does NOT echo the snapshot blob on create — pyfsr
    #    surfaces that as a typed error rather than a silent None.
    v1 = client.playbooks.create_version(PB_NAME, note="v1-alpha")
    assert v1.uuid
    assert v1.note == "v1-alpha"
    assert v1.autosave is False, "a caller-supplied snapshot is not an editor autosave"
    with pytest.raises(ValueError, match="does not echo"):
        v1.parsed_json()
    assert client.playbooks.get_version(v1.uuid).parsed_json()["steps"], "re-fetch loads the blob"

    # 3. Author edits the playbook, then freezes again.
    _set_marker(client, V2_MARKER)
    v2 = client.playbooks.create_version(PB_NAME, note="v2-bravo")

    # 4. Both snapshots are listed. Assert by note, not by position: pyfsr sends
    #    no sort param, so ordering is the server's default, not a pyfsr promise.
    listed = client.playbooks.list_versions(PB_NAME)
    by_note = {v.note: v for v in listed}
    assert {"v1-alpha", "v2-bravo"} <= set(by_note), f"listed: {[v.note for v in listed]}"
    assert by_note["v1-alpha"].uuid == v1.uuid
    assert by_note["v2-bravo"].uuid == v2.uuid

    # 5. The edit is real: the SAME playbook now emits a different marker.
    second = _run_and_read_marker(client)
    assert second == V2_MARKER
    assert second != first, "the edited playbook must produce different output"

    # 6. diff_versions localises the change to the edited step's arguments.
    delta = client.playbooks.diff_versions(v1, v2)
    assert not delta.added and not delta.removed, "only an in-place edit; no steps added/removed"
    changed = [d for d in delta.changed if d.field == "arguments"]
    assert changed, f"expected an arguments delta, got {delta.changed!r}"
    assert changed[0].from_value["resource"]["description"] == V1_MARKER
    assert changed[0].to_value["resource"]["description"] == V2_MARKER

    # 7. Roll back to the older snapshot — and prove it by BEHAVIOUR, not just
    #    by the definition: the playbook emits the original marker again.
    client.playbooks.restore_version(PB_NAME, v1.uuid)
    third = _run_and_read_marker(client)
    assert third == V1_MARKER, f"restore should revert output to {V1_MARKER}, got {third!r}"
    assert third == first


def test_delete_version_removes_it_from_the_listing(client, deployed_demo):
    """`delete_version` drops one snapshot and leaves the others intact."""
    keep = client.playbooks.create_version(PB_NAME, note="keep-me")
    drop = client.playbooks.create_version(PB_NAME, note="drop-me")
    assert {"keep-me", "drop-me"} <= {v.note for v in client.playbooks.list_versions(PB_NAME)}

    client.playbooks.delete_version(drop.uuid)

    remaining = {v.note for v in client.playbooks.list_versions(PB_NAME)}
    assert "drop-me" not in remaining
    assert "keep-me" in remaining, "deleting one snapshot must not disturb the others"
    client.playbooks.delete_version(keep.uuid)
