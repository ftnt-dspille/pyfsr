"""The ghost connector, and the install guard that avoids it (opt-in: pytest -m integration).

Builds a tiny connector whose one operation returns a marker, installs it, then
reinstalls the same version with a different marker. Observed on a live appliance:
after a same-version reinstall most calls kept returning the OLD marker for
minutes (an uninstall plus fresh install did not clear it either), while a new
version answered with the new code on the first call. ``republish()`` recycles the
workers. The connector is uninstalled afterwards.
"""

import collections
import json
import time

import pytest

from pyfsr.api.connectors import pack_connector

pytestmark = pytest.mark.integration

NAME = f"pyfsr-ghost-{int(time.time()) % 100000}"


def _source(tmp_path, version, marker):
    d = tmp_path / f"{version}-{marker}" / NAME
    d.mkdir(parents=True)
    (d / "info.json").write_text(
        json.dumps(
            {
                "name": NAME,
                "label": "pyfsr Ghost Probe",
                "version": version,
                "category": "Utilities",
                "description": "Returns a fixed marker.",
                "publisher": "pyfsr",
                "cs_approved": False,
                "configuration": {"fields": []},
                "operations": [
                    {
                        "operation": "marker",
                        "title": "Marker",
                        "description": "Return the marker.",
                        "parameters": [],
                        "enabled": True,
                        "output_schema": {"marker": ""},
                    }
                ],
            }
        )
    )
    (d / "connector.py").write_text(
        "from connectors.core.connector import Connector\n"
        "from .operations import operations\n\n\n"
        "class Ghost(Connector):\n"
        "    def execute(self, config, operation, params, **kwargs):\n"
        "        return operations[operation](config, params)\n\n"
        "    def check_health(self, config):\n"
        "        return True\n"
    )
    (d / "operations.py").write_text(
        f"def marker(config, params):\n    return {{'marker': {marker!r}}}\n\n\noperations = {{'marker': marker}}\n"
    )
    return pack_connector(str(d), output=str(d.parent / f"{NAME}.tgz"), validate=False)


def _sample(client, version, n=20):
    seen = collections.Counter()
    for _ in range(n):
        seen[client.connectors.execute(NAME, "marker", version=version).get("data", {}).get("marker")] += 1
    return seen


def test_same_version_reinstall_is_bumped_and_serves_new_code(client, tmp_path):
    try:
        client.connectors.install_from_file(_source(tmp_path, "1.0.0", "A"), wait=True)
        client.connectors.ensure_configured(NAME, {}, config_name="Default", default=True)
        assert _sample(client, "1.0.0", 5) == {"A": 5}

        out = client.connectors.install_from_file(_source(tmp_path, "1.0.0", "B"), replace=True, wait=True)
        assert out["bumped"] == "1.0.1"
        client.connectors.clear_cache()
        assert client.connectors.resolve_version(NAME) == "1.0.1"
        assert _sample(client, "1.0.1") == {"B": 20}
    finally:
        for v in ("1.0.1", "1.0.0"):
            try:
                client.connectors.uninstall(NAME, version=v)
            except Exception:  # noqa: BLE001 - one of them may not exist
                pass
