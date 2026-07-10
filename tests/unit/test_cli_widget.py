"""Unit tests for the ``pyfsr widget`` CLI command handlers.

Uses a mock client (no live network). Verifies argument wiring, name->uuid
resolution, and WidgetError handling for each subcommand.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

from pyfsr.cli import widget
from pyfsr.exceptions import WidgetError

UUID = "12345678-1234-1234-1234-123456789abc"


def _record(**kw):
    base = dict(uuid=UUID, name="MyWidget", version="1.0.0", draft=False, installed=True, published=True)
    base.update(kw)
    return SimpleNamespace(**base)


class MockWidgets:
    def __init__(self, *, get_result="__record__", raise_on=None):
        self.calls = []
        self._get_result = _record() if get_result == "__record__" else get_result
        self._raise_on = raise_on or set()

    def _maybe_raise(self, op):
        if op in self._raise_on:
            raise WidgetError(f"{op} failed")

    def list(self, installed=None, name=None):
        self.calls.append(("list", installed, name))
        return [_record()]

    def upload(self, path, replace=True):
        self.calls.append(("upload", path, replace))
        self._maybe_raise("upload")
        return _record(draft=True)

    def publish(self, uuid, replace=True, go_live=True):
        self.calls.append(("publish", uuid, replace, go_live))
        self._maybe_raise("publish")
        return _record()

    def deploy(self, path, replace=True, timeout=60.0):
        self.calls.append(("deploy", path, replace, timeout))
        self._maybe_raise("deploy")
        return _record()

    def get(self, name):
        self.calls.append(("get", name))
        return self._get_result

    def export(self, uuid, dest, development=False):
        self.calls.append(("export", uuid, dest, development))
        return dest

    def remove(self, uuid):
        self.calls.append(("remove", uuid))


class MockClient:
    def __init__(self, widgets):
        self.base_url = "https://fortisoar.example.com"
        self.widgets = widgets


def _run(handler, widgets, **arg_kw):
    args = argparse.Namespace(fmt="table", **arg_kw)
    with patch("pyfsr.cli.widget._make_client", return_value=MockClient(widgets)):
        return handler(args)


def test_looks_like_uuid():
    assert widget._looks_like_uuid(UUID) is True
    assert widget._looks_like_uuid("MyWidget") is False


def test_cmd_list():
    w = MockWidgets()
    # _output.render binds file=sys.stdout at import time, so stdout capture is
    # unreliable here; assert the client call wiring instead.
    assert _run(widget.cmd_list, w, installed=True, name="foo") == 0
    assert w.calls[0] == ("list", True, "foo")


def test_cmd_upload_success(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_upload, w, path="w.tgz", no_replace=False) == 0
    assert w.calls[-1] == ("upload", "w.tgz", True)  # replace = not no_replace
    assert "uploaded" in capfd.readouterr().out


def test_cmd_upload_widget_error(capfd):
    w = MockWidgets(raise_on={"upload"})
    assert _run(widget.cmd_upload, w, path="w.tgz", no_replace=False) == 1
    assert "error:" in capfd.readouterr().err


def test_cmd_publish_by_uuid(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_publish, w, uuid=UUID, as_draft=False, no_replace=False) == 0
    # uuid used directly, no name resolution
    assert not any(c[0] == "get" for c in w.calls)
    assert ("publish", UUID, True, True) in w.calls


def test_cmd_publish_resolves_name(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_publish, w, uuid="MyWidget", as_draft=True, no_replace=True) == 0
    assert ("get", "MyWidget") in w.calls
    # resolved uuid, replace False, go_live False (as_draft=True)
    assert ("publish", UUID, False, False) in w.calls


def test_cmd_publish_name_not_found(capfd):
    w = MockWidgets(get_result=None)
    assert _run(widget.cmd_publish, w, uuid="Ghost", as_draft=False, no_replace=False) == 1
    assert "no widget found" in capfd.readouterr().err


def test_cmd_publish_widget_error(capfd):
    w = MockWidgets(raise_on={"publish"})
    assert _run(widget.cmd_publish, w, uuid=UUID, as_draft=False, no_replace=False) == 1
    assert "error:" in capfd.readouterr().err


def test_cmd_deploy_success(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_deploy, w, path="w.tgz", no_replace=False, timeout=30.0) == 0
    assert ("deploy", "w.tgz", True, 30.0) in w.calls
    assert "deployed" in capfd.readouterr().out


def test_cmd_deploy_widget_error(capfd):
    w = MockWidgets(raise_on={"deploy"})
    assert _run(widget.cmd_deploy, w, path="w.tgz", no_replace=False, timeout=30.0) == 1


def test_cmd_export_by_uuid(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_export, w, uuid=UUID, dest="out.tgz", development=True) == 0
    assert ("export", UUID, "out.tgz", True) in w.calls
    assert "out.tgz" in capfd.readouterr().out


def test_cmd_export_resolves_name(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_export, w, uuid="MyWidget", dest="out.tgz", development=False) == 0
    assert ("get", "MyWidget") in w.calls


def test_cmd_export_name_not_found(capfd):
    w = MockWidgets(get_result=None)
    assert _run(widget.cmd_export, w, uuid="Ghost", dest="out.tgz", development=False) == 1
    assert "no widget found" in capfd.readouterr().err


def test_cmd_rm(capfd):
    w = MockWidgets()
    assert _run(widget.cmd_rm, w, uuid=UUID) == 0
    assert ("remove", UUID) in w.calls
    assert "removed" in capfd.readouterr().out


def test_build_subparser_wires_all_commands():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    widget.build_subparser(sub)
    # each subcommand parses and sets a func default
    for argv, expected in [
        (["list"], widget.cmd_list),
        (["upload", "w.tgz"], widget.cmd_upload),
        (["publish", UUID], widget.cmd_publish),
        (["deploy", "w.tgz"], widget.cmd_deploy),
        (["export", UUID, "out.tgz"], widget.cmd_export),
        (["rm", UUID], widget.cmd_rm),
    ]:
        ns = parser.parse_args(argv)
        assert ns.func is expected
