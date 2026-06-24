"""Connector-config preflight (``pyfsr.playbook_lint``, AGENT_DX D3).

``connector_refs`` is offline-pure (fixture compiled envelope); a tiny fake
client exercises ``check_connector_configs`` end-to-end without a live SOAR.
"""

from __future__ import annotations

from pyfsr.models._integration import ConnectorConfigSummary, InstalledConnector
from pyfsr.playbook_lint import (
    STEP_CONNECTOR,
    STEP_SEND_EMAIL,
    check_connector_configs,
    connector_refs,
)


def _step(uuid: str, *, name: str, args: dict | None = None, iri: bool = True) -> dict:
    st = f"/api/3/workflow_step_types/{uuid}" if iri else uuid
    return {"name": name, "stepType": st, "arguments": args or {}}


def _envelope(*steps: dict) -> dict:
    return {"data": [{"name": "coll", "workflows": [{"name": "pb1", "steps": list(steps)}]}]}


# --- connector_refs (offline) -------------------------------------------------
def test_finds_connector_and_smtp_steps_skips_others():
    env = _envelope(
        _step(STEP_CONNECTOR, name="VT lookup", args={"connector": "virustotal", "operation": "ip", "version": "3.0"}),
        _step(STEP_SEND_EMAIL, name="Notify", args={"connector": "smtp", "config": "cfg-1"}),
        _step("2597053c-e718-44b4-8394-4d40fe26d357", name="Create record", args={"connector": "nope"}),
    )
    refs = connector_refs(env)
    assert [r.connector for r in refs] == ["virustotal", "smtp"]
    assert refs[0].operation == "ip"
    assert refs[1].config == "cfg-1"
    assert refs[0].workflow == "pb1" and refs[0].step == "VT lookup"


def test_tolerates_bare_uuid_and_dict_step_type():
    env = _envelope(
        _step(STEP_CONNECTOR, name="bare", args={"connector": "a"}, iri=False),
        {"name": "dict", "stepType": {"uuid": STEP_CONNECTOR}, "arguments": {"connector": "b"}},
    )
    assert [r.connector for r in connector_refs(env)] == ["a", "b"]


def test_connector_step_without_connector_arg_skipped():
    env = _envelope(_step(STEP_CONNECTOR, name="empty", args={"operation": "x"}))
    assert connector_refs(env) == []


def test_accepts_collections_alias_and_empty():
    assert connector_refs({}) == []
    env = {"collections": [{"name": "c", "workflows": [{"name": "w", "steps": []}]}]}
    assert connector_refs(env) == []


# --- check_connector_configs (fake client) ------------------------------------
class _FakeConnectors:
    def __init__(self, installed: list[InstalledConnector]):
        self._installed = installed

    def list_configured(self, *, refresh: bool = False):
        return self._installed


class _FakeClient:
    def __init__(self, installed):
        self.connectors = _FakeConnectors(installed)


def _installed(name: str, configs: list[ConnectorConfigSummary] | None = None) -> InstalledConnector:
    return InstalledConnector.model_validate({"name": name, "configuration": configs or []})


def _ref_env(connector: str, *, config: str | None = None):
    args = {"connector": connector}
    if config:
        args["config"] = config
    return connector_refs(_envelope(_step(STEP_CONNECTOR, name="s", args=args)))


def test_not_installed():
    findings = check_connector_configs(_FakeClient([]), _ref_env("virustotal"))
    assert [f.code for f in findings] == ["not-installed"]
    assert "default_config" in findings[0].fix_hint


def test_installed_no_config():
    client = _FakeClient([_installed("virustotal")])
    findings = check_connector_configs(client, _ref_env("virustotal"))
    assert [f.code for f in findings] == ["no-config"]


def test_pinned_config_missing():
    client = _FakeClient([_installed("smtp", [ConnectorConfigSummary(config_id="other", name="x")])])
    findings = check_connector_configs(client, _ref_env("smtp", config="cfg-1"))
    assert [f.code for f in findings] == ["config-missing"]


def test_all_clear():
    client = _FakeClient([_installed("smtp", [ConnectorConfigSummary(config_id="cfg-1", name="x", default=True)])])
    assert check_connector_configs(client, _ref_env("smtp", config="cfg-1")) == []
    # and with no pinned config, any configuration satisfies it
    assert check_connector_configs(client, _ref_env("smtp")) == []


def test_dedup_repeated_connector():
    env = connector_refs(
        _envelope(
            _step(STEP_CONNECTOR, name="a", args={"connector": "virustotal"}),
            _step(STEP_CONNECTOR, name="b", args={"connector": "virustotal"}),
        )
    )
    findings = check_connector_configs(_FakeClient([]), env)
    assert len(findings) == 1


# --- CLI exit codes -----------------------------------------------------------
def _compiled(env, ok=True):
    from pyfsr.authoring import CompiledPlaybook

    return CompiledPlaybook(fsr_json=env, errors=[], ok=ok)


def _run_lint(monkeypatch, env, installed):
    import argparse

    from pyfsr.cli import playbook as pbcli

    monkeypatch.setattr(pbcli, "_compile", lambda args: _compiled(env))
    monkeypatch.setattr(pbcli, "_make_client", lambda args: _FakeClient(installed))
    return pbcli.cmd_lint(argparse.Namespace(file="x.yaml"))


def test_cli_lint_exit_2_on_warning(monkeypatch, capsys):
    env = _envelope(_step(STEP_CONNECTOR, name="s", args={"connector": "virustotal"}))
    assert _run_lint(monkeypatch, env, installed=[]) == 2


def test_cli_lint_exit_0_when_clean(monkeypatch, capsys):
    env = _envelope(_step(STEP_CONNECTOR, name="s", args={"connector": "smtp"}))
    installed = [_installed("smtp", [ConnectorConfigSummary(config_id="c", name="x", default=True)])]
    assert _run_lint(monkeypatch, env, installed) == 0


def test_cli_lint_exit_1_on_compile_failure(monkeypatch, capsys):
    import argparse

    from pyfsr.cli import playbook as pbcli

    monkeypatch.setattr(pbcli, "_compile", lambda args: _compiled(None, ok=False))
    assert pbcli.cmd_lint(argparse.Namespace(file="x.yaml")) == 1
