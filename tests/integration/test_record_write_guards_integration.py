"""What the box does with a write key the module lacks (opt-in: pytest -m integration).

Raw POSTs show FortiSOAR answers 200 and drops the key; through pyfsr the same
write is refused before sending. One alert is created and deleted.
"""

import pytest

from pyfsr.exceptions import ValidationError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("key", ["nosuchfield", "descriptoin"])
def test_the_box_drops_an_unknown_key_silently(client, key):
    r = client.post("/api/3/alerts", {"name": "pyfsr write-guard probe", key: "x"})
    try:
        assert key not in client.get(r["@id"])
    finally:
        client.delete(r["@id"])


def test_pyfsr_refuses_it_and_verify_write_passes_a_good_write(client):
    rs = client.records("alerts")
    with pytest.raises(ValidationError, match="did you mean 'description'"):
        rs.create({"name": "pyfsr write-guard probe", "descriptoin": "x"}, raw=True)
    sent = {"name": "pyfsr write-guard probe", "description": "x", "recordTags": ["pyfsr-probe"]}
    rec = rs.create(sent, raw=True)
    try:
        assert rs.verify_write(rec["@id"], sent, rec) == {"verified": True}
    finally:
        client.delete(rec["@id"])
