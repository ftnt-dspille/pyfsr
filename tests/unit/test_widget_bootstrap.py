"""The widget bootstrap guard: a widget can UPLOAD cleanly yet render as raw
template text on the box because its controller is not in the DevCtrl form the
platform publish derives the live Ctrl from, or a bundled UMD lib aborts the AMD
loader. Both shipped a broken widget to a box through import_config.import_file.

Covers the pure detector (:func:`widget_bootstrap_problems`), the tgz reader, and
that the Export validator surfaces the findings on the import path -- so removing
the detector (or the exports wiring) turns the "fires" assertions red. The pre-existing
``widget_controller_problems`` deliberately allows either form, so it does NOT catch
this; that gap is asserted here too.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from test_exports import build_export, codes

from pyfsr.widget_dev import (
    to_published_form,
    widget_bootstrap_problems,
    widget_bootstrap_problems_from_tgz,
    widget_controller_problems,
)


# name "myw" + version "2.0.0" => myw200DevCtrl / editMyw200DevCtrl.
def _CTRL(reg):
    return f"angular.module('cybersponse').controller('{reg}', ['$scope', function(s){{}}]);\n"


_UMD_ACTIVE = (
    '!function(e,t){"object"==typeof exports&&"undefined"!=typeof module?t(exports):'
    '"function"==typeof define&&define.amd?define(["exports"],t):t((e=e||self).lib={})}(this,function(e){});\n'
)
_UMD_NEUTRAL = (
    '!function(e,t){"object"==typeof exports&&"undefined"!=typeof module?t(exports):'
    "t((e=e||self).lib={})}(this,function(e){});\n"
)


def _widget_tgz(view_reg: str, edit_reg: str, lib: str, *, name: str = "myw", version: str = "2.0.0") -> bytes:
    members = {
        f"{name}-{version}/info.json": json.dumps({"name": name, "version": version}).encode(),
        f"{name}-{version}/view.controller.js": _CTRL(view_reg).encode(),
        f"{name}-{version}/edit.controller.js": _CTRL(edit_reg).encode(),
        f"{name}-{version}/widgetAssets/lib/x.js": lib.encode(),
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, blob in members.items():
            entry = tarfile.TarInfo(path)
            entry.size = len(blob)
            tf.addfile(entry, io.BytesIO(blob))
    return buf.getvalue()


def _members(view_reg: str, edit_reg: str, lib: str) -> dict[str, str]:
    return {
        "myw-2.0.0/view.controller.js": _CTRL(view_reg),
        "myw-2.0.0/edit.controller.js": _CTRL(edit_reg),
        "myw-2.0.0/widgetAssets/lib/x.js": lib,
    }


# ---- pure detector ---------------------------------------------------------


def test_flags_shipped_ctrl_form_and_active_umd():
    probs = widget_bootstrap_problems("myw", "2.0.0", _members("myw200Ctrl", "editMyw200DevCtrl", _UMD_ACTIVE))
    assert {c for c, _ in probs} == {"controller_form", "umd_anonymous_define"}


def test_flags_edit_controller_form():
    probs = widget_bootstrap_problems("myw", "2.0.0", _members("myw200DevCtrl", "editMyw200Ctrl", _UMD_NEUTRAL))
    assert [c for c, _ in probs] == ["edit_controller_form"]


def test_silent_on_devctrl_form_and_neutralized_umd():
    probs = widget_bootstrap_problems("myw", "2.0.0", _members("myw200DevCtrl", "editMyw200DevCtrl", _UMD_NEUTRAL))
    assert probs == []


def test_published_form_is_accepted_when_development_is_false():
    probs = widget_bootstrap_problems(
        "myw", "2.0.0", _members("myw200Ctrl", "editMyw200Ctrl", _UMD_NEUTRAL), development=False
    )
    assert probs == []


def test_wrong_form_for_the_development_flag_is_rejected():
    # A package that says it is published but registers DevCtrl names controllers
    # the box never looks up; and the reverse for a development package.
    probs = widget_bootstrap_problems(
        "myw", "2.0.0", _members("myw200DevCtrl", "editMyw200DevCtrl", _UMD_NEUTRAL), development=False
    )
    assert {c for c, _ in probs} == {"controller_form", "edit_controller_form"}
    probs = widget_bootstrap_problems(
        "myw", "2.0.0", _members("myw200Ctrl", "editMyw200Ctrl", _UMD_NEUTRAL), development=True
    )
    assert {c for c, _ in probs} == {"controller_form", "edit_controller_form"}


def test_to_published_form_rewrites_a_dev_package_into_one_that_validates(tmp_path: Path):
    src = tmp_path / "myw-2.0.0.tgz"
    src.write_bytes(_widget_tgz("myw200DevCtrl", "editMyw200DevCtrl", _UMD_NEUTRAL))
    # a development package: info.json carries no/true development flag, DevCtrl form
    assert widget_bootstrap_problems_from_tgz(src) == []
    out = to_published_form(src, tmp_path / "pub.tgz")
    with tarfile.open(out) as tf:
        info = json.loads(tf.extractfile("myw-2.0.0/info.json").read())
        view = tf.extractfile("myw-2.0.0/view.controller.js").read().decode()
        lib = tf.extractfile("myw-2.0.0/widgetAssets/lib/x.js").read().decode()
    assert info["development"] is False and info["name"] == "myw"
    assert "myw200Ctrl" in view and "DevCtrl" not in view
    assert lib == _UMD_NEUTRAL  # untouched
    assert widget_bootstrap_problems_from_tgz(out) == []  # accepted as development: false
    # and the converted package is NOT valid as a development one
    assert {c for c, _ in widget_bootstrap_problems("myw", "2.0.0", _members("myw200Ctrl", "editMyw200Ctrl", ""), True)}


def test_from_tgz_reads_name_version_and_flags(tmp_path: Path):
    tgz = tmp_path / "myw-2.0.0.tgz"
    tgz.write_bytes(_widget_tgz("myw200Ctrl", "editMyw200DevCtrl", _UMD_ACTIVE))
    probs = widget_bootstrap_problems_from_tgz(tgz)
    assert {c for c, _ in probs} == {"controller_form", "umd_anonymous_define"}


def test_controller_problems_does_not_catch_the_form_gap(tmp_path: Path):
    # The pre-existing dir checker accepts either Ctrl or DevCtrl: it is silent on
    # the exact defect widget_bootstrap_problems exists to catch.
    d = tmp_path / "myw"
    d.mkdir()
    (d / "info.json").write_text(json.dumps({"name": "myw", "version": "2.0.0"}))
    (d / "view.controller.js").write_text(_CTRL("myw200Ctrl"))
    (d / "edit.controller.js").write_text(_CTRL("editMyw200Ctrl"))
    assert widget_controller_problems(d) == []  # the gap this guard closes


# ---- exports / import-path wiring ------------------------------------------

WIDGET_DATA = [
    {
        "name": "myw",
        "label": "My Widget",
        "version": "2.0.0",
        "category": ["utilities"],
        "install_mode": "tgz",
        "installer_path": "myw-2.0.0.tgz",
    }
]


def _widget_overrides(view_reg: str, edit_reg: str, lib: str) -> dict:
    return {
        "widgets__data_dot_json": json.dumps(WIDGET_DATA).encode(),
        "widgets__myw-2_dot_0_dot_0_dot_tgz": _widget_tgz(view_reg, edit_reg, lib),
    }


def test_export_problems_surface_bootstrap_findings(tmp_path: Path):
    path = build_export(tmp_path, **_widget_overrides("myw200Ctrl", "editMyw200DevCtrl", _UMD_ACTIVE))
    found = codes(path)
    assert "widget.controller_form" in found
    assert "widget.umd_anonymous_define" in found


def test_export_problems_clean_for_good_widget(tmp_path: Path):
    path = build_export(tmp_path, **_widget_overrides("myw200DevCtrl", "editMyw200DevCtrl", _UMD_NEUTRAL))
    found = codes(path)
    assert not any(c.startswith("widget.controller_form") or c == "widget.umd_anonymous_define" for c in found)
