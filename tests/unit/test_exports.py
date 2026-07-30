"""Structural validation of Export Wizard bundles (:mod:`pyfsr.exports`).

Two halves. First, a synthetic-but-faithful export is built in memory and must
validate clean -- the layout mirrors what an 8.0.0 appliance actually writes
(``export_<uuid>/`` root, ``contents`` keys that differ from directory names,
``data.json`` per installer category, a real gzip tarball for the bundled
connector). Then every check is mutation-tested: the export is broken one way at
a time and the specific finding code must appear. A validator nobody has watched
fire is a validator that does nothing.
"""

from __future__ import annotations

import copy
import io
import json
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pyfsr.exports import (
    Export,
    ExportError,
    ExportKind,
    ExportValidationError,
    Severity,
)

ROOT = "export_11111111-2222-3333-4444-555555555555/"

PACK_INFO: dict[str, Any] = {
    "name": "demoPack",
    "version": "1.0.0",
    "fsrMinCompatibility": "8.0.0",
    "type": "solutionpack",
    "local": True,
    "label": "Demo Pack",
    "dependencies": [{"name": "sOARFramework", "type": "solutionpack", "label": "SOAR Framework", "minVersion": None}],
    "publisher": "Custom",
    "certified": "false",
    "description": "A pack for tests.",
    "category": [],
    "iconLarge": "fsr-icon-large.png",
    "postInstallConfig": None,
    "date": "2026-01-01T00:00:00+00:00",
    "contents": {
        "playbooks": [{"name": "Demo Collection"}],
        "connectors": [
            {"name": "Code Runner", "apiName": "code-runner", "version": "1.0.0"},
            {"name": "Jira", "apiName": "jira"},
        ],
        "picklistNames": [{"name": "Severity"}],
    },
}

CONNECTOR_DATA = [
    {
        "name": "code-runner",
        "label": "Code Runner",
        "version": "1.0.0",
        "category": ["utilities"],
        "description": "",
        "publisher": "Custom",
        "operation_roles": [],
        "configurations": [],
        "dataImports": [],
        "install_mode": "tgz",
        "installer_path": "code-runner_1.0.0.tgz",
    },
    {
        "name": "jira",
        "label": "Jira",
        "category": ["ticketing"],
        "description": "",
        "publisher": "Fortinet",
        "operation_roles": [],
        "configurations": [],
        "dataImports": [],
        "install_mode": "rpm",
    },
]

PLAYBOOK = {
    "name": "Do A Thing",
    "steps": [
        {"name": "Start", "arguments": {}},
        {"name": "Call Jira", "arguments": {"connector": "jira", "operation": "create_ticket"}},
        {"name": "Utility", "arguments": {"connector": "cyops_utilities", "operation": "x"}},
        {"name": "Dynamic", "arguments": {"connector": "{{vars.item}}", "operation": "x"}},
    ],
}


def _tarball(name: str, version: str) -> bytes:
    """A minimal but real connector tarball, complete with its own info.json."""
    buf = io.BytesIO()
    manifest = json.dumps({"name": name, "version": version, "label": name}).encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        entry = tarfile.TarInfo(f"{name}/info.json")
        entry.size = len(manifest)
        tf.addfile(entry, io.BytesIO(manifest))
    return buf.getvalue()


def build_export(tmp_path: Path, *, info: dict[str, Any] | None = None, **overrides: Any) -> Path:
    """Write a valid pack to ``tmp_path``; ``overrides`` mutate members by name.

    A member set to ``None`` is dropped; anything else replaces its bytes.
    """
    members: dict[str, bytes] = {
        "info.json": json.dumps(info if info is not None else PACK_INFO, indent=1).encode(),
        "playbooks/Demo Collection/collection.metadata.json": json.dumps({"name": "Demo Collection"}).encode(),
        "playbooks/Demo Collection/Do A Thing.json": json.dumps(PLAYBOOK).encode(),
        "connectors/data.json": json.dumps(CONNECTOR_DATA, indent=1).encode(),
        "connectors/code-runner_1.0.0.tgz": _tarball("code-runner", "1.0.0"),
        "picklists/data.json": json.dumps([{"name": "Severity"}]).encode(),
        "images/fsr-icon-large.png": b"\x89PNG\r\n\x1a\n",
    }
    for key, value in overrides.items():
        member = key.replace("__", "/").replace("_dot_", ".")
        if value is None:
            members.pop(member, None)
        else:
            members[member] = value

    dest = tmp_path / "demoPack-1.0.0.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in members.items():
            z.writestr(ROOT + name, blob)
    return dest


def codes(path: Path, **kw: Any) -> list[str]:
    with Export.open(path) as exp:
        return [f.code for f in exp.problems(**kw)]


def blocking(path: Path, **kw: Any) -> list[str]:
    with Export.open(path) as exp:
        return [f.code for f in exp.problems(**kw) if f.severity is not Severity.INFO]


def info_with(**changes: Any) -> dict[str, Any]:
    out = copy.deepcopy(PACK_INFO)
    out.update(changes)
    return out


def data_json(mutate: Callable[[list[dict[str, Any]]], None]) -> bytes:
    rows = copy.deepcopy(CONNECTOR_DATA)
    mutate(rows)
    return json.dumps(rows, indent=1).encode()


# --------------------------------------------------------------------------- shape


def test_clean_pack_has_no_blocking_findings(tmp_path: Path) -> None:
    assert blocking(build_export(tmp_path)) == []


def test_pack_is_recognised_by_its_identity(tmp_path: Path) -> None:
    with Export.open(build_export(tmp_path)) as exp:
        assert exp.kind is ExportKind.SOLUTION_PACK
        assert (exp.name, exp.version, exp.label) == ("demoPack", "1.0.0", "Demo Pack")
        assert exp.min_compatibility == "8.0.0"


def test_bundle_without_identity_is_a_config_export(tmp_path: Path) -> None:
    """No name/version means no pack identity -- it can only go through the import wizard."""
    info = {
        "fsrMinCompatibility": "8.0.0",
        "date": "2026-01-01T00:00:00+00:00",
        "exported_from": "soar",
        "exported_by": "CS Admin",
        "contents": PACK_INFO["contents"],
    }
    path = build_export(tmp_path, info=info)
    with Export.open(path) as exp:
        assert exp.kind is ExportKind.CONFIG_EXPORT
    assert "kind.config_export" in codes(path)


def test_contents_keys_map_to_their_real_directory_names(tmp_path: Path) -> None:
    """``picklistNames`` lives in ``picklists/`` -- the names deliberately differ."""
    assert blocking(build_export(tmp_path)) == []
    path = build_export(tmp_path, picklists__data_dot_json=None)
    assert "contents.dir_missing" in codes(path)


def test_installers_are_enumerated_with_their_resolved_member(tmp_path: Path) -> None:
    with Export.open(build_export(tmp_path)) as exp:
        by_name = {i.name: i for i in exp.installers()}
    assert by_name["code-runner"].install_mode == "tgz"
    assert by_name["code-runner"].member == "connectors/code-runner_1.0.0.tgz"
    assert by_name["jira"].install_mode == "rpm"
    assert by_name["jira"].member is None


def test_external_connectors_excludes_builtins_and_jinja(tmp_path: Path) -> None:
    """jira is declared, cyops_utilities is a builtin, {{vars.item}} is dynamic."""
    with Export.open(build_export(tmp_path)) as exp:
        assert exp.external_connectors() == set()


def test_external_connectors_reports_a_genuine_requirement(tmp_path: Path) -> None:
    playbook = copy.deepcopy(PLAYBOOK)
    playbook["steps"][1]["arguments"]["connector"] = "virustotal"
    path = build_export(
        tmp_path,
        **{"playbooks__Demo Collection__Do A Thing_dot_json": json.dumps(playbook).encode()},
    )
    with Export.open(path) as exp:
        assert exp.external_connectors() == {"virustotal"}
    assert "connectors.external_requirement" in codes(path)


# --------------------------------------------------------------------------- open()


def test_open_rejects_a_non_zip(tmp_path: Path) -> None:
    bad = tmp_path / "nope.zip"
    bad.write_bytes(b"definitely not a zip")
    with pytest.raises(ExportError, match="not a readable zip"):
        Export.open(bad)


def test_open_rejects_a_zip_without_info_json(tmp_path: Path) -> None:
    bad = tmp_path / "empty.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("something.txt", b"hi")
    with pytest.raises(ExportError, match="no info.json"):
        Export.open(bad)


def test_open_rejects_multiple_export_roots(tmp_path: Path) -> None:
    bad = tmp_path / "double.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("export_a/info.json", b"{}")
        z.writestr("export_b/info.json", b"{}")
    with pytest.raises(ExportError, match="export roots"):
        Export.open(bad)


# --------------------------------------------------------- mutations: one per check

MUTATIONS: list[tuple[str, str, dict[str, Any]]] = [
    # identity
    ("type dropped", "pack.type_missing", {"info": {k: v for k, v in PACK_INFO.items() if k != "type"}}),
    ("version garbage", "pack.version_invalid", {"info": info_with(version="not-a-version")}),
    ("certified as bool", "pack.certified_type", {"info": info_with(certified=True)}),
    # contents <-> files
    (
        "declared collection absent",
        "playbooks.collection_missing",
        {
            "playbooks__Demo Collection__Do A Thing_dot_json": None,
            "playbooks__Demo Collection__collection_dot_metadata_dot_json": None,
        },
    ),
    (
        "collection undeclared",
        "playbooks.collection_undeclared",
        {"info": info_with(contents={"playbooks": [{"name": "Other"}]})},
    ),
    ("contents not a container", "contents.bad_shape", {"info": info_with(contents={"playbooks": "nope"})}),
    ("category declared empty", "contents.empty_category", {"info": info_with(contents={"playbooks": []})}),
    ("payload dir unclaimed", "payload.unclaimed_dir", {"reports__orphan_dot_json": b"{}"}),
    # installers
    ("bundled tgz missing", "installer.file_missing", {"connectors__code-runner_1_dot_0_dot_0_dot_tgz": None}),
    (
        "install_mode nulled",
        "installer.mode_missing",
        {"connectors__data_dot_json": data_json(lambda r: r[0].update(install_mode=None))},
    ),
    (
        "installer_path dropped",
        "installer.path_missing",
        {"connectors__data_dot_json": data_json(lambda r: r[0].pop("installer_path"))},
    ),
    (
        "manifest/tarball version skew",
        "installer.version_mismatch",
        {"connectors__data_dot_json": data_json(lambda r: r[0].update(version="9.9.9"))},
    ),
    (
        "unknown install mode",
        "installer.mode_unknown",
        {"connectors__data_dot_json": data_json(lambda r: r[0].update(install_mode="deb"))},
    ),
    ("tarball corrupt", "installer.unreadable", {"connectors__code-runner_1_dot_0_dot_0_dot_tgz": b"not a tarball"}),
    # payload
    ("payload json corrupt", "payload.bad_json", {"connectors__data_dot_json": b"{ not json"}),
    # pack extras
    ("iconLarge dangling", "pack.icon_missing", {"info": info_with(iconLarge="missing.png")}),
    (
        "postInstall widget dangling",
        "pack.postinstall_widget_missing",
        {"info": info_with(postInstallConfig={"enabled": True, "widgets": [{"name": "ghost", "version": "1.0.0"}]})},
    ),
    ("dependency has no name", "pack.dependency_malformed", {"info": info_with(dependencies=[{"label": "nameless"}])}),
]


@pytest.mark.parametrize(("label", "expected", "overrides"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_is_caught(tmp_path: Path, label: str, expected: str, overrides: dict[str, Any]) -> None:
    assert expected in codes(build_export(tmp_path, **overrides)), label


def test_wrong_connector_packaged_is_caught(tmp_path: Path) -> None:
    """The manifest says one product; the bundled tarball contains another.

    This is the shape of a fuzzy connector lookup silently packaging the wrong
    thing, which a manifest-only reading cannot see.
    """
    path = build_export(
        tmp_path,
        **{"connectors__code-runner_1_dot_0_dot_0_dot_tgz": _tarball("threat-miner", "1.0.0")},
    )
    assert "installer.name_mismatch" in codes(path)


def test_collection_without_metadata_is_silently_skipped_by_the_importer(tmp_path: Path) -> None:
    """The playbook loader only descends into folders that have the sidecar."""
    path = build_export(tmp_path, **{"playbooks__Demo Collection__collection_dot_metadata_dot_json": None})
    assert "collection.metadata_missing" in codes(path)


def test_post_install_widget_needs_a_version(tmp_path: Path) -> None:
    """The marketplace UI gates the post-install button on name *and* version."""
    info = info_with(
        contents={**PACK_INFO["contents"], "widgets": [{"apiName": "wizardWidget", "name": "Wizard"}]},
        postInstallConfig={"enabled": True, "widgets": [{"name": "wizardWidget", "label": "Configure"}]},
    )
    path = build_export(tmp_path, info=info, widgets__data_dot_json=b"[]")
    assert "pack.postinstall_widget_unversioned" in codes(path)


def test_post_install_enabled_with_no_widget_is_an_error(tmp_path: Path) -> None:
    path = build_export(tmp_path, info=info_with(postInstallConfig={"enabled": True, "widgets": []}))
    assert "pack.postinstall_no_widget" in codes(path)


def test_post_install_runtime_state_should_not_ship(tmp_path: Path) -> None:
    """autoLaunchTriggered is per-appliance state; shipping it suppresses auto-launch."""
    info = info_with(
        contents={**PACK_INFO["contents"], "widgets": [{"apiName": "wizardWidget", "name": "Wizard"}]},
        postInstallConfig={
            "enabled": True,
            "widgets": [{"name": "wizardWidget", "version": "1.0.0", "autoLaunchTriggered": True}],
        },
    )
    path = build_export(tmp_path, info=info, widgets__data_dot_json=b"[]")
    assert "pack.postinstall_state_leaked" in codes(path)


def test_install_hooks_are_playbook_collections(tmp_path: Path) -> None:
    """preInstall/postInstall are ordinary collections in a directory named for the section."""
    info = info_with(contents={**PACK_INFO["contents"], "preInstall": [{"name": "Setup"}]})
    path = build_export(
        tmp_path,
        info=info,
        **{
            "preInstall__Setup__collection_dot_metadata_dot_json": json.dumps({"name": "Setup"}).encode(),
            "preInstall__Setup__Prepare_dot_json": json.dumps(PLAYBOOK).encode(),
        },
    )
    assert blocking(path) == []


def test_install_hook_collection_missing_metadata_is_caught(tmp_path: Path) -> None:
    info = info_with(contents={**PACK_INFO["contents"], "preInstall": [{"name": "Setup"}]})
    path = build_export(tmp_path, info=info, **{"preInstall__Setup__Prepare_dot_json": json.dumps(PLAYBOOK).encode()})
    assert "collection.metadata_missing" in codes(path)


# ------------------------------------------------------------------ view templates


VIEW_TEMPLATE_INFO = info_with(
    contents={
        **PACK_INFO["contents"],
        "modules": {"people": {"name": "People", "apiName": "people"}},
        "viewTemplates": {"people": {"name": "People", "apiName": "people", "views": ["detail", "list", "settings"]}},
    }
)

VIEW_TEMPLATE_FILES = {
    "modules__people__detail-layout_dot_json": b"{}",
    "modules__people__list-layout_dot_json": b"{}",
    "modules__people__settings_dot_json": b"{}",
}


def test_view_templates_live_under_modules_not_views(tmp_path: Path) -> None:
    """A view *template* is per-module layout files; only navigation lives in views/."""
    path = build_export(tmp_path, info=VIEW_TEMPLATE_INFO, **VIEW_TEMPLATE_FILES)
    assert blocking(path) == []


def test_missing_layout_file_is_caught(tmp_path: Path) -> None:
    files = dict(VIEW_TEMPLATE_FILES)
    files["modules__people__list-layout_dot_json"] = None
    path = build_export(tmp_path, info=VIEW_TEMPLATE_INFO, **files)
    assert "viewTemplates.layout_missing" in codes(path)


def test_settings_layout_uses_its_own_filename(tmp_path: Path) -> None:
    """detail/form/list are ``<view>-layout.json``; settings is plain ``settings.json``."""
    files = dict(VIEW_TEMPLATE_FILES)
    del files["modules__people__settings_dot_json"]
    files["modules__people__settings-layout_dot_json"] = b"{}"  # the wrong name
    path = build_export(tmp_path, info=VIEW_TEMPLATE_INFO, **files)
    assert "viewTemplates.layout_missing" in codes(path)


def test_empty_declared_category_still_claims_its_directory(tmp_path: Path) -> None:
    """Shipped packs declare an empty category while shipping the directory."""
    info = info_with(contents={**PACK_INFO["contents"], "widgets": []})
    path = build_export(tmp_path, info=info, widgets__data_dot_json=b"[]")
    assert blocking(path) == []
    assert "contents.empty_category" in codes(path)


# --------------------------------------------------------------------- compatibility


def test_target_older_than_min_compatibility_is_an_error(tmp_path: Path) -> None:
    assert "compat.target_too_old" in codes(build_export(tmp_path), target_version="7.6.5")


def test_newer_target_is_fine(tmp_path: Path) -> None:
    assert "compat.target_too_old" not in codes(build_export(tmp_path), target_version="8.0.0-6034")


def test_missing_min_compatibility_warns_when_a_target_is_given(tmp_path: Path) -> None:
    path = build_export(tmp_path, info={k: v for k, v in PACK_INFO.items() if k != "fsrMinCompatibility"})
    assert "compat.unspecified" in codes(path, target_version="8.0.0")


# ------------------------------------------------------------------------- validate()


def test_validate_raises_on_errors_and_lists_them(tmp_path: Path) -> None:
    path = build_export(
        tmp_path, connectors__code_runner_1_dot_0_dot_0_dot_tgz=None, info=info_with(iconLarge="gone.png")
    )
    with Export.open(path) as exp, pytest.raises(ExportValidationError) as excinfo:
        exp.validate()
    assert any(f.code == "pack.icon_missing" for f in excinfo.value.findings)


def test_validate_returns_non_fatal_findings_on_a_clean_pack(tmp_path: Path) -> None:
    with Export.open(build_export(tmp_path)) as exp:
        remaining = exp.validate()
    assert all(f.severity is Severity.INFO for f in remaining)


def test_validate_strict_promotes_warnings(tmp_path: Path) -> None:
    path = build_export(tmp_path, reports__orphan_dot_json=b"{}")
    with Export.open(path) as exp:
        exp.validate()  # warning only -- tolerated by default
        with pytest.raises(ExportValidationError):
            exp.validate(strict=True)
