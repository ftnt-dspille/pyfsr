"""Publish a connector to the mirror straight from its ``.tgz`` — the mirror
builds the RPM for you.

Hand this a connector source tarball (the top-level ``<name>/`` dir with an
``info.json``, i.e. ``tar czf http.tgz http/``) and it does everything the
OFFLINEREPO install path needs, so ``client.connectors.install(name, version)``
against this mirror Just Works:

  1. read ``info.json`` -> name / version / buildNumber / label / operations
  2. build a thin ``cyops-connector-<name>-<version>-<release>.rpm`` (payload =
     your tgz; the el9 ``%post`` activates it) using ``cyops-connector.spec.in``
  3. drop the RPM in the local yum repo + ``createrepo_c`` so it wins by priority
  4. merge ``<name>_<version> -> {rpm_full_name}`` into ``connectors-all.json``
     (what the installer reads to learn the exact RPM file to pull)
  5. stage the Content-Hub metadata zip (``info.json`` + ``connectors/data.json``
     with ``install_mode: rpm``) under ``/content-hub/<name>-<version>/{build,latest}/``

The RPM is just a ~2 KB wrapper around the tgz, so the caller never touches
``rpmbuild`` — that is the whole point of doing it here.

Usable as a library (``publish_connector``) or a CLI
(``python connector_publish.py <tgz> [--release N]``). The admin API and
``chctl add-connector`` both call ``publish_connector``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from typing import Any

# ``name``/``version``/``release`` come from the UPLOADED tgz's info.json (untrusted)
# and are substituted into filesystem paths and into the RPM spec — whose %post runs
# as shell at install time. Constrain them to a package-name-safe charset so a crafted
# info.json cannot traverse directories (``../``) or inject spec directives / shell.
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_VERSION_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_RELEASE_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._]{0,31}\Z")


def _validate_token(kind: str, value: str, pattern: re.Pattern[str]) -> str:
    """Reject a token that isn't safe to use in a path or the RPM spec."""
    if not pattern.match(value):
        raise ValueError(
            f"unsafe connector {kind} {value!r}: only letters, digits, '.', '_' and '-' "
            "are allowed (no path separators, '..', whitespace, or spec metacharacters)"
        )
    return value


# The spec template lives beside the standalone build.sh; reused verbatim so the
# in-container build and the dev-laptop build stay identical.
_SPEC_IN_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "connector-build", "cyops-connector.spec.in"
)


def _read_info_from_tgz(tgz_path: str) -> tuple[str, dict[str, Any]]:
    """Return (connector_root_member_prefix, info_dict) from a connector tgz.

    Finds the ``.../info.json`` nearest the root and returns its parent path
    prefix (e.g. ``http/``) plus the parsed info.json.
    """
    with tarfile.open(tgz_path, "r:*") as tf:
        info_members = [m for m in tf.getmembers() if m.isfile() and os.path.basename(m.name) == "info.json"]
        if not info_members:
            raise ValueError("tgz has no info.json — not a connector package")
        # shallowest info.json wins (the connector root, not a nested sample)
        info_members.sort(key=lambda m: m.name.count("/"))
        member = info_members[0]
        prefix = os.path.dirname(member.name)  # "" if info.json is at the tar root
        fh = tf.extractfile(member)
        if fh is None:
            raise ValueError("could not read info.json from tgz")
        info = json.loads(fh.read().decode("utf-8"))
    return prefix, info


def _normalize_payload_tgz(src_tgz: str, name: str, dest_tgz: str) -> None:
    """Rewrite ``src_tgz`` so its single top-level dir is exactly ``<name>/``.

    ``manage.py connectors`` keys the connector directory off the tarball's
    top-level folder, so it must be ``<name>`` regardless of what the uploader
    named it. Cheap to always normalize.
    """
    with tarfile.open(src_tgz, "r:*") as tf:
        prefix, _ = _read_info_from_tgz(src_tgz)
        with tarfile.open(dest_tgz, "w:gz") as out:
            for m in tf.getmembers():
                # map "<oldprefix>/rest" -> "<name>/rest"
                rel = m.name[len(prefix) :].lstrip("/") if prefix else m.name
                if not rel:
                    continue
                new = tarfile.TarInfo(name=f"{name}/{rel}")
                new.size, new.mode, new.mtime = m.size, m.mode, m.mtime
                new.type, new.linkname = m.type, m.linkname
                new.uid = new.gid = 0
                new.uname = new.gname = "root"
                if m.isfile():
                    data = tf.extractfile(m)
                    out.addfile(new, data)
                else:
                    out.addfile(new)


def _build_rpm(name: str, version: str, release: str, payload_tgz: str, spec_in: str, workdir: str) -> str:
    """Render the spec + rpmbuild -bb. Returns the built RPM path."""
    mod_version = version.replace(".", "_")
    top = os.path.join(workdir, "rpmbuild")
    for sub in ("SPECS", "SOURCES", "BUILD", "BUILDROOT", "RPMS", "SRPMS"):
        os.makedirs(os.path.join(top, sub), exist_ok=True)
    shutil.copy(payload_tgz, os.path.join(top, "SOURCES", f"{name}.tgz"))

    with open(spec_in, encoding="utf-8") as fh:
        spec = fh.read()
    spec = (
        spec.replace("@NAME@", name)
        .replace("@VERSION@", version)
        .replace("@RELEASE@", release)
        .replace("@MOD_VERSION@", mod_version)
    )
    spec_path = os.path.join(top, "SPECS", f"cyops-connector-{name}.spec")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write(spec)

    # No --target: the spec is BuildArch: noarch (pure data + scriptlets), so it
    # builds natively on any host arch and still installs on the x86_64 appliance.
    proc = subprocess.run(
        ["rpmbuild", "--define", f"_topdir {top}", "-bb", spec_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"rpmbuild failed:\n{proc.stdout.decode(errors='replace')[-2000:]}")
    # Locate the built RPM (arch subdir is 'noarch'; glob to stay arch-agnostic).
    import glob

    hits = glob.glob(os.path.join(top, "RPMS", "*", f"cyops-connector-{name}-{version}-{release}.*.rpm"))
    if not hits:
        raise RuntimeError(f"rpmbuild did not produce an RPM for {name}-{version}-{release}")
    return hits[0]


def _merge_connectors_all(cinfo_path: str, name: str, version: str, rpm_full_name: str) -> None:
    """Add/replace ``<name>_<version> -> {rpm_full_name}`` in connectors-all.json.

    Preserves the (large, Fortinet-derived) rest of the file so the installer
    still resolves every upstream connector too.
    """
    data: dict[str, Any] = {}
    if os.path.isfile(cinfo_path):
        with open(cinfo_path, encoding="utf-8") as fh:
            data = json.load(fh)
    data[f"{name}_{version}"] = {"rpm_full_name": rpm_full_name}
    os.makedirs(os.path.dirname(cinfo_path), exist_ok=True)
    tmp = cinfo_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, cinfo_path)


def _stage_metadata_zip(
    content_hub_dir: str, name: str, version: str, build_number: int, info: dict[str, Any], rpm_full_name: str
) -> None:
    """Write the Content-Hub metadata zip + info.json under both the build dir
    and ``latest/`` — the two paths the installer probes."""
    slug = f"{name}-{version}"
    data_json = [
        {
            "name": name,
            "version": version,
            "title": info.get("label") or info.get("title") or name,
            "install_mode": "rpm",
            "rpm_name": f"cyops-connector-{name}-{version}",
            "installer_path": slug,
            "publisher": info.get("publisher", "Fortinet"),
        }
    ]
    # Build the zip in memory: <slug>/info.json + <slug>/connectors/data.json
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{slug}/info.json", json.dumps(info, indent=1))
        z.writestr(f"{slug}/connectors/data.json", json.dumps(data_json, indent=4))
    zip_bytes = buf.getvalue()
    info_bytes = json.dumps(info, indent=1).encode("utf-8")

    for sub in (str(build_number), "latest"):
        d = os.path.join(content_hub_dir, slug, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{slug}.zip"), "wb") as fh:
            fh.write(zip_bytes)
        with open(os.path.join(d, "info.json"), "wb") as fh:
            fh.write(info_bytes)


def publish_connector(
    tgz_path: str,
    *,
    connectors_local_dir: str = "/connectors-local",
    content_hub_dir: str = "/srv/content-hub",
    cinfo_path: str = "/srv/local-cinfo/connectors-all.json",
    spec_in: str = _SPEC_IN_DEFAULT,
    release: str = "1",
    createrepo_bin: str = "createrepo_c",
) -> dict[str, Any]:
    """Build + publish a connector tgz to the mirror. Returns a summary dict."""
    prefix, info = _read_info_from_tgz(tgz_path)
    name = info.get("name")
    version = str(info.get("version") or "")
    if not name or not version:
        raise ValueError("info.json is missing name/version")
    name = _validate_token("name", str(name), _NAME_RE)
    version = _validate_token("version", version, _VERSION_RE)
    release = _validate_token("release", str(release), _RELEASE_RE)
    build_number = int(info.get("buildNumber") or 1)

    with tempfile.TemporaryDirectory(prefix="conn-publish-") as work:
        payload = os.path.join(work, f"{name}.tgz")
        _normalize_payload_tgz(tgz_path, name, payload)
        rpm = _build_rpm(name, version, release, payload, spec_in, work)
        rpm_full_name = os.path.basename(rpm)

        # 1. place RPM in the local yum repo + reindex
        arch_dir = os.path.join(connectors_local_dir, "x86_64")
        os.makedirs(arch_dir, exist_ok=True)
        # drop any older release of the SAME name+version so the resolver is unambiguous
        for existing in os.listdir(arch_dir):
            if existing.startswith(f"cyops-connector-{name}-{version}-") and existing != rpm_full_name:
                os.remove(os.path.join(arch_dir, existing))
        shutil.copy(rpm, os.path.join(arch_dir, rpm_full_name))
        repodata = os.path.join(arch_dir, "repodata")
        cmd = [createrepo_bin, "--update", arch_dir] if os.path.isdir(repodata) else [createrepo_bin, arch_dir]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

    # 2. connectors-all.json merge (installer's name->rpm map)
    _merge_connectors_all(cinfo_path, name, version, rpm_full_name)

    # 3. Content-Hub metadata zip
    _stage_metadata_zip(content_hub_dir, name, version, build_number, info, rpm_full_name)

    return {
        "name": name,
        "version": version,
        "buildNumber": build_number,
        "rpm_full_name": rpm_full_name,
        "info": {k: info.get(k) for k in ("label", "publisher", "category")},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build + publish a connector tgz to the mirror.")
    ap.add_argument("tgz", help="connector source tarball (top-level <name>/ with info.json)")
    ap.add_argument("--release", default="1", help="RPM release number (bump to force a re-pull)")
    ap.add_argument("--connectors-local-dir", default=os.environ.get("CONNECTORS_LOCAL_DIR", "/connectors-local"))
    ap.add_argument("--content-hub-dir", default=os.environ.get("CONTENT_HUB_DIR", "/srv/content-hub"))
    ap.add_argument("--cinfo-path", default=os.environ.get("CONNECTORS_CINFO", "/srv/local-cinfo/connectors-all.json"))
    args = ap.parse_args(argv)
    summary = publish_connector(
        args.tgz,
        connectors_local_dir=args.connectors_local_dir,
        content_hub_dir=args.content_hub_dir,
        cinfo_path=args.cinfo_path,
        release=args.release,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
