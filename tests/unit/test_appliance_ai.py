"""Unit tests for ``appliance.ai`` (the per-agent MCP tool allow-list in memory.yaml).

A fake transport keeps the agent directory and file in memory, so the tests check what
ends up on disk, that nothing is ever removed, and that a no-op touches nothing.
"""

from __future__ import annotations

import pytest

from pyfsr.cli.appliance import ai
from pyfsr.cli.appliance.transport import CommandResult, Transport, TransportError

SOC = "bcd2462e-ad81-4307-b9b6-664e74219c10"
FSM = "1219e755-debb-4145-b788-ff7d4147b92b"
SIEM = (
    "allowed_tools:\n"
    '  "2e541107-0b55-4623-b297-dfac495a863e": [] # FortiSOAR Module Management\n'
    f'  "{SOC}": ["hunt_ioc_siem"] # SOC Framework'
)


class FakeBox(Transport):
    target = "fake"

    def __init__(self, text: str = SIEM, dirs=("siem_1_0_0",)) -> None:
        self.dirs = [f"{ai.AGENTS_DIR}/{d}" for d in dirs]
        self.files = {f"{d}/config/memory.yaml": text for d in self.dirs}
        self.commands: list[list[str]] = []

    def _res(self, argv, rc=0, out="", err=""):
        return CommandResult(argv=argv, returncode=rc, stdout=out, stderr=err)

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.commands.append(list(argv))
        if argv[:2] == ["sh", "-c"] and argv[2].startswith("ls -d"):
            want = f"{argv[4]}/{argv[5]}_"
            return self._res(argv, out="\n".join(d for d in self.dirs if d.startswith(want)))
        if argv[0] == "cat":
            return self._res(argv, out=self.files[argv[1]])
        if argv[:2] == ["sh", "-c"] and "cp -p" in argv[2]:
            if argv[5] not in self.files:
                self.files[argv[5]] = self.files[argv[4]]
            return self._res(argv)
        if argv[:2] == ["sh", "-c"] and argv[2].startswith("printf"):
            self.files[argv[5]] = argv[4]
            return self._res(argv)
        if argv[0] == "systemctl":
            return self._res(argv)
        raise AssertionError(f"unexpected command {argv}")


def test_parse_reads_server_to_tools():
    assert ai.parse(SIEM)[SOC] == ["hunt_ioc_siem"]
    assert ai.parse(SIEM)["2e541107-0b55-4623-b297-dfac495a863e"] == []


def test_add_new_server_appends_and_keeps_the_rest():
    out = ai.add_tools(SIEM, FSM, ["get_incident_by_id"], comment="FortiSIEM")
    got = ai.parse(out)
    assert got[FSM] == ["get_incident_by_id"] and got[SOC] == ["hunt_ioc_siem"]
    assert "# SOC Framework" in out and out.endswith("# FortiSIEM\n")


def test_add_to_existing_server_unions_and_keeps_comment():
    out = ai.add_tools(SIEM, SOC, ["hunt_ioc_siem", "get_asset"])
    assert ai.parse(out)[SOC] == ["hunt_ioc_siem", "get_asset"]
    assert "# SOC Framework" in out


def test_allow_writes_backup_once_and_reads_back():
    box = FakeBox()
    path = f"{ai.AGENTS_DIR}/siem_1_0_0/config/memory.yaml"
    change = ai.allow_mcp_tools(box, "siem", FSM, ["a", "b"], yes=True)
    assert change.changed and change.ok and change.added == ["a", "b"]
    assert ai.parse(box.files[path])[FSM] == ["a", "b"]
    assert box.files[path + ".orig"] == SIEM
    ai.allow_mcp_tools(box, "siem", FSM, ["c"], yes=True)
    assert box.files[path + ".orig"] == SIEM  # the original is kept, not overwritten


def test_noop_needs_no_confirmation_and_writes_nothing():
    box = FakeBox()
    change = ai.allow_mcp_tools(box, "siem", SOC, ["hunt_ioc_siem"])
    assert not change.changed and "no change" in str(change)
    assert not any(c[:2] == ["sh", "-c"] and "printf" in c[2] for c in box.commands)


def test_refuses_without_yes():
    box = FakeBox()
    with pytest.raises(PermissionError):
        ai.allow_mcp_tools(box, "siem", FSM, ["a"])
    assert box.files[f"{ai.AGENTS_DIR}/siem_1_0_0/config/memory.yaml"] == SIEM


def test_restart_runs_after_the_write():
    box = FakeBox()
    change = ai.allow_mcp_tools(box, "siem", FSM, ["a"], restart=True, yes=True)
    assert change.restarted and box.commands[-1][:2] == ["systemctl", "restart"]


def test_agent_dir_picks_newest_version_and_rejects_unknown():
    box = FakeBox(dirs=("siem_1_0_0", "siem_1_0_1", "siem_hello_1_0_0"))
    assert ai.agent_dir(box, "siem").endswith("siem_1_0_1")
    with pytest.raises(TransportError):
        ai.agent_dir(box, "nope")


def test_agent_name_with_shell_characters_is_not_interpolated():
    box = FakeBox()
    with pytest.raises(TransportError):
        ai.agent_dir(box, "siem; rm -rf /")
