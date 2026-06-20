"""Live e2e validation of the connector ``Result`` return-format question.

Packs the ``result-probe`` sample connector (tests/resources/sample_connector),
uploads it, configures it, health-checks it, then runs three operations that
return the *same* logical payload three different ways:

    return_bare_dict          -> plain dict
    return_result_set_data    -> Result().set_status/set_message/set_data(...)
    return_result_set_result  -> Result().set_result(status, message)  [no data]

The goal is to observe — against a real appliance — whether wrapping a return in
``Result`` changes the shape that comes back from ``/api/integration/execute/``
(the ``{operation, status, message, data}`` envelope), and where the payload
lands. Findings are printed; run with ``-s`` to read them.

Opt-in: ``pytest -m integration tests/integration/test_result_probe_integration.py -s``
Requires a live appliance (FSR_* env or examples/config.toml) whose self-agent
can install a custom connector.
"""

from pathlib import Path

import pytest

from pyfsr import pack_connector

CONNECTOR = "result-probe"
VERSION = "1.0.0"
SOURCE = Path(__file__).parent.parent / "resources" / "sample_connector" / "result-probe"

pytestmark = pytest.mark.integration


def test_pack_connector_layout(tmp_path):
    """Unit-ish: the bundle has one top-level dir and excludes bytecode. Runs offline."""
    import tarfile

    out = pack_connector(str(SOURCE), output=str(tmp_path / "probe.tgz"))
    with tarfile.open(out) as tar:
        names = tar.getnames()
    tops = {n.split("/")[0] for n in names}
    assert tops == {CONNECTOR}, f"expected single top-level dir, got {tops}"
    assert f"{CONNECTOR}/info.json" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


@pytest.fixture(scope="module")
def installed(client):
    """Upload the probe connector and yield the live client. Leaves it installed."""
    resp = client.connectors.install_from_dir(str(SOURCE), replace=True, wait=True, timeout=180)
    print(f"\n[install] {resp.get('status', resp)}")
    return client


@pytest.fixture(scope="module")
def configured(installed):
    client = installed
    client.connectors.create_configuration(
        CONNECTOR,
        {"marker": "probe-e2e"},
        name="probe",
        version=VERSION,
        default=True,
    )
    health = client.connectors.healthcheck(CONNECTOR)
    print(f"[health] {health}")
    return client


def _run(client, operation, params=None):
    env = client.connectors.execute(CONNECTOR, operation, params=params or {})
    print(f"\n[{operation}] envelope keys = {sorted(env)}")
    print(f"  status  = {env.get('status')!r}")
    print(f"  message = {env.get('message')!r}")
    print(f"  data    = {env.get('data')!r}")
    return env


def test_result_return_formats(configured):
    """Run all three styles and compare the envelopes the appliance returns."""
    client = configured

    bare = _run(client, "return_bare_dict", {"echo": "x"})
    setd = _run(client, "return_result_set_data", {"echo": "x"})
    setr = _run(client, "return_result_set_result")

    # All three should come back in the standard execute envelope.
    for env in (bare, setd, setr):
        assert "status" in env and "data" in env

    # Core question: does Result.set_data change where the payload lands vs a
    # bare dict? If the platform normalizes both, env['data'] should be equal.
    bare_data = bare.get("data") or {}
    setd_data = setd.get("data") or {}
    print("\n[compare] bare_dict.data == set_data.data ?", bare_data == setd_data)
    print("[compare] set_data carried message ?", bool(setd.get("message")))
    print("[compare] set_result.data (no data set) =", setr.get("data"))

    # Bare dict must surface its keys under data.
    assert bare_data.get("style") == "bare_dict"
    assert bare_data.get("marker") == "probe-e2e"

    # Whatever the appliance does with Result, record it as the assertion of
    # record for this run: set_data's payload should also reach data.* if the
    # guide's claim holds. (Left as the key observation, not a hard gate, since
    # confirming-or-refuting it is the point of the probe.)
    if setd_data.get("style") == "result_set_data":
        print("[result] CONFIRMED: Result.set_data lands under data.* like a bare dict")
    else:
        print(f"[result] DIVERGENCE: Result.set_data did NOT normalize to data.* — got {setd_data!r}")
