"""Unit tests for the ``pyfsr appliance content-hub sync`` verb.

Drives a recording transport (no live appliance / ssh / sudo). Asserts the sync
runs ``csadm package content-hub sync --force`` with ``sudo=True``, that the
``--yes`` gate refuses without confirmation, and that csadm's unreliable exit
code (0 even on a logged error) is folded with the failure-text check.
"""

from __future__ import annotations

import pytest

from pyfsr._testing.replay import ReplayTransport
from pyfsr.cli.appliance import content_hub
from pyfsr.cli.appliance.transport import CommandResult


class _RecordingTransport(ReplayTransport):
    """ReplayTransport that returns a canned ``csadm ... content-hub sync`` output.

    ReplayTransport.run records (argv, env, sudo) and calls _dispatch; we only
    need to answer the sync command, so override _dispatch for it.
    """

    def __init__(self, stdout: str = "Content Hub sync complete.\n", returncode: int = 0) -> None:
        super().__init__()
        self._sync_stdout = stdout
        self._sync_rc = returncode

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        # still record the call so tests can assert on argv/sudo
        self.commands.append((argv, env, sudo))
        if argv[:4] == ["csadm", "package", "content-hub", "sync"]:
            return CommandResult(argv=argv, returncode=self._sync_rc, stdout=self._sync_stdout, stderr="")
        return super().run(argv, input_text=input_text, env=env, timeout=timeout, sudo=sudo)


def _last_argv(t: _RecordingTransport) -> list[str]:
    return t.commands[-1][0]


def _last_sudo(t: _RecordingTransport) -> bool:
    return t.commands[-1][2]


def test_sync_forced_runs_csadm_with_sudo():
    t = _RecordingTransport()
    r = content_hub.sync(t, force=True, yes=True)
    assert r.ok is True
    assert _last_argv(t) == ["csadm", "package", "content-hub", "sync", "--force"]
    assert _last_sudo(t) is True
    assert r.force is True


def test_sync_scheduled_omits_force_flag():
    t = _RecordingTransport()
    r = content_hub.sync(t, force=False, yes=True)
    assert r.ok is True
    assert _last_argv(t) == ["csadm", "package", "content-hub", "sync"]
    assert r.force is False


def test_sync_refuses_without_yes():
    t = _RecordingTransport()
    # forced (default) and scheduled both gate on yes
    with pytest.raises(PermissionError, match="confirmation"):
        content_hub.sync(t, force=True, yes=False)
    with pytest.raises(PermissionError, match="confirmation"):
        content_hub.sync(t, force=False, yes=False)
    # and never ran anything
    assert t.commands == []


def test_sync_folds_failure_text_into_ok():
    # csadm exits 0 but logs an error (unreachable mirror / untrusted cert) ���
    # the exit code alone would lie, so ok must go False on a failure line.
    t = _RecordingTransport(stdout="ERROR: could not reach content-hub host\n", returncode=0)
    r = content_hub.sync(t, yes=True)
    assert r.ok is False
    assert "could not reach" in r.output


def test_sync_failure_returncode_also_fails():
    t = _RecordingTransport(stdout="", returncode=1)
    r = content_hub.sync(t, yes=True)
    assert r.ok is False


def test_sync_success_output_preserved():
    out = "Syncing content-hub...\nFetched 931 entries.\nContent Hub sync complete."
    t = _RecordingTransport(stdout=out)
    r = content_hub.sync(t, yes=True)
    assert r.ok is True
    assert r.output == out
