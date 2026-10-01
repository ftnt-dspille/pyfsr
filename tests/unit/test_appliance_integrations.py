"""Unit tests for ``pyfsr appliance integrations`` (the connector runtime's pip index).

Drives a fake transport that keeps the file and its immutable flag in memory,
so the tests check what ends up on disk and the order of the privileged steps:
backup, unlock, write, relock.
"""

from __future__ import annotations

import pytest

from pyfsr.cli.appliance import integrations
from pyfsr.cli.appliance.transport import CommandResult, Transport, TransportError

MIRROR_ONLY = (
    "[global]\n"
    "index-url = https://mirror.example.com/deps/simple/\n"
    "prefix = /opt/cyops/configs/integrations/packages/integrations_env/conn_pkgs\n"
)


class FakeBox(Transport):
    target = "fake"

    def __init__(self, text: str = MIRROR_ONLY, *, immutable: bool = True, fail_write: bool = False) -> None:
        self.files = {integrations.PIP_CONF: text}
        self.immutable = immutable
        self.fail_write = fail_write
        self.commands: list[tuple[list[str], bool, str | None]] = []

    def _res(self, argv, rc=0, out="", err=""):
        return CommandResult(argv=argv, returncode=rc, stdout=out, stderr=err)

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        self.commands.append((list(argv), sudo, input_text))
        path = argv[-1]
        if argv[0] == "cat":
            return self._res(argv, out=self.files[path])
        if argv[0] == "lsattr":
            flags = "----i---------" if self.immutable else "--------------"
            return self._res(argv, out=f"{flags} {path}\n")
        if argv[0] == "cp":
            self.files[argv[-1]] = self.files[argv[-2]]
            return self._res(argv)
        if argv[0] == "chattr":
            self.immutable = argv[1] == "+i"
            return self._res(argv)
        if argv[:2] == ["sh", "-c"]:
            if self.immutable:
                return self._res(argv, 1, err="Operation not permitted")
            if self.fail_write:
                return self._res(argv, 1, err="disk full")
            self.files[path] = argv[4]
            return self._res(argv)
        raise AssertionError(f"unexpected command {argv}")

    def verbs(self) -> list[str]:
        return [" ".join(a[:2]) if a[0] in ("chattr", "sh") else a[0] for a, _, _ in self.commands]


def test_reads_the_index_and_the_immutable_flag():
    idx = integrations.pip_index(FakeBox())
    assert idx.index_url == "https://mirror.example.com/deps/simple/"
    assert idx.extra_index_urls == []
    assert idx.immutable is True


def test_adds_pypi_unlocking_and_relocking_the_file():
    box = FakeBox()
    r = integrations.ensure_extra_index(box, yes=True)
    assert r.changed and r.ok
    assert r.after.extra_index_urls == [integrations.PYPI_SIMPLE]
    assert r.after.index_url == r.before.index_url
    assert box.immutable is True
    assert box.files[f"{integrations.PIP_CONF}.bak"] == MIRROR_ONLY
    assert box.verbs() == ["cat", "lsattr", "cp", "chattr -i", "sh -c", "chattr +i", "cat", "lsattr"]


def test_a_mutable_file_is_written_without_touching_the_flag():
    box = FakeBox(immutable=False)
    r = integrations.ensure_extra_index(box, yes=True)
    assert r.ok and box.immutable is False
    assert not any(v.startswith("chattr") for v in box.verbs())


def test_a_failed_write_still_relocks_the_file():
    box = FakeBox(fail_write=True)
    with pytest.raises(TransportError, match="disk full"):
        integrations.ensure_extra_index(box, yes=True)
    assert box.immutable is True
    assert box.files[integrations.PIP_CONF] == MIRROR_ONLY


def test_already_listed_is_a_no_op_that_needs_no_confirmation():
    box = FakeBox(MIRROR_ONLY + f"extra-index-url = {integrations.PYPI_SIMPLE}\n")
    r = integrations.ensure_extra_index(box)
    assert r.changed is False and r.ok
    assert box.verbs() == ["cat", "lsattr"]


def test_refuses_without_yes_and_changes_nothing():
    box = FakeBox()
    with pytest.raises(PermissionError, match="confirmation"):
        integrations.ensure_extra_index(box)
    assert box.verbs() == ["cat", "lsattr"]


def test_content_never_goes_on_stdin():
    # The transports put the sudo password on stdin; with passwordless sudo it
    # would be written into the file.
    box = FakeBox()
    integrations.ensure_extra_index(box, yes=True)
    assert all(stdin is None for _, _, stdin in box.commands)


def test_an_existing_extra_index_line_is_extended():
    text = MIRROR_ONLY + "extra-index-url = https://other.example.com/simple/\n"
    out = integrations.add_extra_index(text, integrations.PYPI_SIMPLE)
    idx = integrations.parse(out)
    assert idx.extra_index_urls == ["https://other.example.com/simple/", integrations.PYPI_SIMPLE]
    assert out.count("extra-index-url") == 1


def test_the_new_line_stays_inside_global():
    text = MIRROR_ONLY + "\n[install]\nno-compile = true\n"
    out = integrations.add_extra_index(text, integrations.PYPI_SIMPLE)
    assert integrations.parse(out).extra_index_urls == [integrations.PYPI_SIMPLE]
    assert out.index("extra-index-url") < out.index("[install]")


def test_a_file_without_sections_gets_a_global_header():
    out = integrations.add_extra_index("index-url = https://mirror.example.com/simple/\n", integrations.PYPI_SIMPLE)
    assert out.startswith("[global]\n")
    assert integrations.parse(out).extra_index_urls == [integrations.PYPI_SIMPLE]


def test_appliance_facade_exposes_the_namespace():
    from pyfsr.appliance import Appliance
    from pyfsr.cli.appliance.facts import Facts

    box = FakeBox()
    app = Appliance(_facts=Facts(box))
    assert app.integrations.pip_index().immutable is True
