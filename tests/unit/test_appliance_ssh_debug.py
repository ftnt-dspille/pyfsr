"""SSH failures say why, debug logging is opt-in and masks secrets, doctor stops at the first bad step."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from pyfsr.cli.appliance import doctor
from pyfsr.cli.appliance.transport import (
    CommandResult,
    SSHTransport,
    TransportError,
    debug_from_env,
    diagnose_failure,
    make_transport,
)


@pytest.mark.parametrize(
    ("code", "stderr", "sudo", "want"),
    [
        (255, "ssh: connect to host h port 22: Connection refused", False, "nothing is listening"),
        (255, "ssh: Could not resolve hostname nope", False, "does not resolve"),
        (255, "csadmin@h: Permission denied (publickey,password).", False, "login rejected"),
        (255, "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", False, "host key"),
        (255, "kex_exchange_identification: weird", False, "exit 255"),
        (1, "Sorry, try again.\nsudo: 1 incorrect password attempt", True, "sudo password rejected"),
        (1, "csadmin is not in the sudoers file.", True, "not allowed to use sudo"),
        (1, "psql: relation does not exist", True, None),
        (2, "grep: no such file", False, None),
        (0, "", True, None),
    ],
)
def test_failures_get_a_plain_reason(code, stderr, sudo, want):
    got = diagnose_failure(code, stderr, ssh=True, sudo=sudo)
    assert (got is None) if want is None else (want in got)


def test_check_puts_the_reason_first():
    r = CommandResult(argv=["ssh", "x"], returncode=255, stdout="", stderr="boom", diagnosis="host unreachable")
    with pytest.raises(TransportError, match=r"^host unreachable\n"):
        r.check()


def _fake_run(monkeypatch, *, code=0, stderr=""):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=code, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_env_values_are_masked_in_the_result_and_the_debug_log(monkeypatch, capsys):
    _fake_run(monkeypatch, code=1, stderr="Sorry, try again.")
    t = SSHTransport(host="fsr.example.com", password="pw", debug=True)
    r = t.run(["psql", "-c", "select 1"], env={"PGPASSWORD": "s3cr3t-value"}, sudo=True)
    err = capsys.readouterr().err
    assert "s3cr3t-value" not in " ".join(r.argv) and "s3cr3t-value" not in err and "pw" not in err.split()
    assert "[pyfsr ssh]" in err and "sudo password rejected" in err and "LogLevel=VERBOSE" in err


def test_debug_is_off_by_default(monkeypatch, capsys):
    monkeypatch.delenv("PYFSR_SSH_DEBUG", raising=False)
    seen = _fake_run(monkeypatch)
    t = make_transport(host="fsr.example.com")
    t.run(["true"])
    assert not t.debug and capsys.readouterr().err == "" and "LogLevel=ERROR" in seen["cmd"]


def test_debug_env_switch(monkeypatch):
    assert debug_from_env({"PYFSR_SSH_DEBUG": "1"}) and not debug_from_env({"PYFSR_SSH_DEBUG": "0"})
    monkeypatch.setenv("PYFSR_SSH_DEBUG", "yes")
    assert make_transport(host="fsr.example.com").debug


def test_instance_profile_can_turn_debug_on(tmp_path):
    from pyfsr.instances import _appliance_spec_from_table

    env = tmp_path / "box.env"
    env.write_text("PYFSR_APPLIANCE_HOST=fsr.example.com\nPYFSR_SSH_DEBUG=1\n")
    spec = _appliance_spec_from_table({"env_file": str(env)}, default_host=None, base_dir=tmp_path, source="t")
    assert spec is not None and spec.debug


class _Box(SSHTransport):
    """Answers each command from a table; the TCP step is patched separately."""

    answers: dict = {}

    def run(self, argv, *, sudo=False, **kw):
        code, err = self.answers.get((tuple(argv), sudo), (0, ""))
        return CommandResult(
            argv=list(argv),
            returncode=code,
            stdout="",
            stderr=err,
            diagnosis=diagnose_failure(code, err, ssh=True, sudo=sudo),
        )


@pytest.fixture
def reachable(monkeypatch):
    monkeypatch.setattr(doctor, "_reach_step", lambda t, timeout: doctor.Step(name="reach host", status="ok"))


def test_doctor_passes_every_step(reachable):
    report = doctor.run(_Box(host="fsr.example.com", password="pw"))
    assert report.ok and [s.name for s in report.steps][-1] == "csadm"


def test_doctor_stops_at_a_rejected_sudo_password(reachable):
    box = _Box(host="fsr.example.com", password="pw")
    box.answers = {(("true",), True): (1, "Sorry, try again.\nsudo: 1 incorrect password attempt")}
    report = doctor.run(box)
    bad = report.first_failure
    assert bad is not None and bad.name == "sudo" and "sudo password rejected" in bad.detail
    assert [s.status for s in report.steps if s.name == "csadm"] == ["skip"]
    assert "stopped at: sudo" in report.render()


def test_doctor_reports_a_refused_port(monkeypatch):
    def refuse(*a, **k):
        raise ConnectionRefusedError

    monkeypatch.setattr(doctor.socket, "create_connection", refuse)
    report = doctor.run(_Box(host="fsr.example.com", port=2222))
    assert report.first_failure.name == "reach host" and "2222 refused" in report.first_failure.detail


def test_registered_secrets_and_passwords_never_reach_the_debug_log(monkeypatch, capsys):
    _fake_run(monkeypatch, code=7, stderr="auth failed for elastic:0123456789abcdef with pw <your-password>")
    t = SSHTransport(host="fsr.example.com", password="<your-password>", debug=True)
    t.add_secret("0123456789abcdef")
    r = t.run(["curl", "-u", "elastic:0123456789abcdef", "http://localhost:9200/"])
    err = capsys.readouterr().err
    for secret in ("0123456789abcdef", "<your-password>"):
        assert secret not in err and secret not in " ".join(r.argv)


def test_es_credentials_go_on_stdin_not_argv(monkeypatch):
    from pyfsr.cli.appliance import es

    seen = {}

    class T(SSHTransport):
        def run(self, argv, **kw):
            seen.update(argv=argv, input=kw.get("input_text"))
            return CommandResult(argv=argv, returncode=0, stdout="{}", stderr="")

    facts = SimpleNamespace(transport=T(host="fsr.example.com"), device_uuid=lambda: "0123456789abcdef")
    es._curl_es(facts, "/_cluster/health")
    assert "0123456789abcdef" not in " ".join(seen["argv"]) and "0123456789abcdef" in seen["input"]
