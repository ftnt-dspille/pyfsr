"""Live field-type matrix for ModulesAdminAPI builders (opt-in: pytest -m integration).

Builds one throwaway module holding every scalar display type, made with pyfsr's
own builders and their defaults, publishes it once, then:

- writes a valid value to each field and reads it back (a builder default that
  makes the platform refuse a valid value is a pyfsr bug), and
- records what the platform does at the edges (length limits, required, wrong
  types, malformed values) so the model-side guards can match it.

Slow: one publish plus a role grant. The module is deleted afterwards.
"""

import time

import pytest

pytestmark = pytest.mark.integration

# A fresh name per run: deleting and recreating one name races the delete's publish.
MODULE = f"pyfsr_fieldprobe_{int(time.time()) % 100000}"
ROLE = "Full App Permissions"

# name -> (builder, builder kwargs, a valid value, expected read-back or ... to skip)
VALID = {
    "fText": ("text_field", {}, "hello", "hello"),
    "fArea": ("text_field", {"area": True}, "two\nlines", "two\nlines"),
    "fRich": ("text_field", {"rich": True}, "<p>x</p>", ...),
    "fEmail": ("email_field", {}, "a@example.com", "a@example.com"),
    "fUrl": ("url_field", {}, "https://example.com", "https://example.com"),
    "fPhone": ("phone_field", {}, "+15555550100", "+15555550100"),
    "fHash": ("filehash_field", {}, "d41d8cd98f00b204e9800998ecf8427e", "d41d8cd98f00b204e9800998ecf8427e"),
    "fIpv4": ("ipv4_field", {}, "198.51.100.23", "198.51.100.23"),
    "fIpv6": ("ipv6_field", {}, "2001:db8::1", "2001:db8::1"),
    "fDomain": ("domain_field", {}, "example.com", "example.com"),
    "fInt": ("integer_field", {}, 5, 5),
    "fDec": ("decimal_field", {}, 1.5, 1.5),
    "fDate": ("datetime_field", {}, 1773446400, 1773446400),
    "fBool": ("checkbox_field", {}, True, True),
    "fJson": ("json_field", {}, {"a": 1}, {"a": 1}),
    "fObj": ("object_field", {}, {"a": 1}, {"a": 1}),
}

# Edge fields: built with explicit options the matrix below exercises.
EDGE_FIELDS = {
    "eShort": ("text_field", {"maxlength": 5}),
    "eReq": ("text_field", {"required": True}),
}

# (case, field, value) -- what the platform does is recorded, not assumed.
EDGES = [
    ("text over maxlength", "eShort", "abcdef"),
    ("text at maxlength", "eShort", "abcde"),
    ("integer above int32", "fInt", 3_000_000_000),
    ("integer as digit string", "fInt", "5"),
    ("integer as float", "fInt", 1.5),
    ("datetime ISO string", "fDate", "2026-03-14T00:00:00+00:00"),
    ("datetime epoch millis", "fDate", 1773446400000),
    ("datetime digit string", "fDate", "1773446400"),
    ("checkbox as string", "fBool", "yes"),
    ("email malformed", "fEmail", "not-an-email"),
    ("ipv4 malformed", "fIpv4", "999.1.1.1"),
    ("url malformed", "fUrl", "not a url"),
    ("decimal as string", "fDec", "1.5"),
    ("json as string", "fJson", "not json"),
]


@pytest.fixture(scope="module")
def probe(client):
    ma = client.modules_admin
    fields = [getattr(ma, b)(n, **kw) for n, (b, kw, _, _) in VALID.items()]
    fields += [getattr(ma, b)(n, **kw) for n, (b, kw) in EDGE_FIELDS.items()]
    ma.create_module(MODULE, label="pyfsr Field Probe", fields=fields, grant_to=ROLE)
    ma.publish(timeout=900)  # applies the deferred grant once the module is live
    # Permissions can lag the grant; the client re-authenticates on 403 by itself.
    for _ in range(30):
        try:
            r = client.post(f"/api/3/{MODULE}", {"eReq": "x"})
            client.delete(r["@id"])
            break
        except Exception:  # noqa: BLE001 - Access Denied until the grant lands
            time.sleep(10)
    yield client
    ma.delete_module(MODULE)


def _write(client, data):
    """POST then GET; return (ok, read-back record or error text)."""
    try:
        r = client.post(f"/api/3/{MODULE}", {"eReq": "x", **data})
    except Exception as exc:  # noqa: BLE001 - the refusal is the observation
        return False, str(exc).splitlines()[0][:160]
    got = client.get(r["@id"])
    client.delete(r["@id"])
    return True, got


@pytest.mark.parametrize("name", list(VALID))
def test_builder_default_accepts_a_valid_value(probe, name):
    _, _, value, expect = VALID[name]
    ok, got = _write(probe, {name: value})
    assert ok, f"{name}: a valid value was refused: {got}"
    if expect is not ...:
        assert got.get(name) == expect, f"{name}: wrote {value!r}, read back {got.get(name)!r}"


def test_required_field_missing_is_refused(probe):
    try:
        r = probe.post(f"/api/3/{MODULE}", {"fText": "no eReq"})
    except Exception:  # noqa: BLE001
        return
    probe.delete(r["@id"])
    pytest.fail("a record without the required field was accepted")


def test_edge_matrix(probe, capsys):
    rows = []
    for case, field, value in EDGES:
        ok, got = _write(probe, {field: value})
        rows.append((case, value, "stored " + repr(got.get(field)) if ok else "refused: " + got))
    with capsys.disabled():
        print("\nfield edge matrix:")
        for case, value, outcome in rows:
            print(f"  {case:28} {value!r:32} -> {outcome}")
