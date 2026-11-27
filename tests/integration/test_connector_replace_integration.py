"""A rejectable replace install is refused and leaves the connector alone (opt-in: pytest -m integration).

A ``$replace=true`` upload removes the active connector and its configurations before
the importer validates the bundle. This builds a throwaway connector, then attempts a
replace with a bundle that breaks the importer's 1000-character operation-description
cap. The local pre-check must refuse it, so the installed version and its configuration
survive. The throwaway connector is uninstalled afterwards.
"""

import io
import json
import tarfile
import time

import pytest

from pyfsr.api.connectors import ConnectorPackageError

pytestmark = pytest.mark.integration

NAME = f"pyfsr-replace-probe-{int(time.time()) % 100000}"


def _bundle(tmp_path, version, description):
    tgz = tmp_path / f"{NAME}-{version.replace('.', '_')}.tgz"
    info = {
        "name": NAME,
        "label": "pyfsr Replace Probe",
        "version": version,
        "category": "Utilities",
        "description": "Throwaway replace probe.",
        "publisher": "pyfsr",
        "cs_approved": False,
        "configuration": {"fields": []},
        "operations": [
            {
                "operation": "health_check",
                "title": "Health check",
                "description": description,
                "parameters": [],
                "enabled": True,
                "output_schema": {"ok": ""},
            }
        ],
    }
    files = {
        "info.json": json.dumps(info).encode(),
        "connector.py": (
            b"from connectors.core.connector import Connector\n"
            b"from .operations import operations\n\n\n"
            b"class Probe(Connector):\n"
            b"    def execute(self, config, operation, params, **kwargs):\n"
            b"        return operations[operation](config, params)\n\n"
            b"    def check_health(self, config):\n"
            b"        return True\n"
        ),
        "operations.py": b"operations = {'health_check': lambda config, params: {'ok': True}}\n",
    }
    with tarfile.open(tgz, "w:gz") as tar:
        for fname, data in files.items():
            ti = tarfile.TarInfo(f"{NAME}/{fname}")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return str(tgz)


def test_rejectable_replace_leaves_the_installed_connector_alone(client, tmp_path):
    try:
        client.connectors.install_from_file(_bundle(tmp_path, "1.0.0", "Fine."), wait=True)
        client.connectors.create_configuration(NAME, {}, name="Probe", version="1.0.0", default=True)
        assert client.connectors.resolve_version(NAME) == "1.0.0"
        configs_before = [c.config_id for c in client.connectors.configurations(NAME)]
        assert len(configs_before) == 1

        bad = _bundle(tmp_path, "1.0.1", "x" * 1100)
        with pytest.raises(ConnectorPackageError, match="1000"):
            client.connectors.install_from_file(bad, replace=True, wait=True, on_existing_version="allow")

        client.connectors.clear_cache()
        assert client.connectors.resolve_version(NAME) == "1.0.0"
        assert [c.config_id for c in client.connectors.configurations(NAME)] == configs_before
    finally:
        for v in ("1.0.1", "1.0.0"):
            try:
                client.connectors.uninstall(NAME, version=v)
            except Exception:  # noqa: BLE001 - one of them may not exist
                pass
