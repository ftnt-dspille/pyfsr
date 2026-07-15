#!/usr/bin/env python3
"""End-to-end proof: install a connector through the mirror, update it, sync,
re-install, and run the new action repeatedly.

This proves the full self-hosted Content Hub loop in one script:

  1. publish a ping-free baseline (1.1.1) to the mirror — synthesized from the
     1.2.0 source by stripping the ``ping`` operation + its wiring.
  2. force a Content Hub sync on the appliance, install 1.1.1, and confirm the
     ``ping`` action is NOT yet present (the baseline state).
  3. update the mirror: publish the real 1.2.0 source (which adds ``ping``) —
     RPM + merged connectors-all.json + metadata zip.
  4. force another Content Hub sync, install 1.2.0, and confirm ``ping`` IS now
     present (the update took effect).
  5. run the new ``ping`` action 5x in a row — each must be ``Success`` AND
     return a *distinct* ``server_time`` (proves every run is fresh, not cached).

The script is fully self-contained: it publishes its OWN baseline (1.1.1,
ping-free) from the 1.2.0 source, so no prior connector publish is assumed —
only that the mirror is up and the appliance is pointed at it.

Credentials come from env (gitignored ``.env.fsr-ga`` + ``.env.appliance``);
nothing is hardcoded. The appliance REST client uses ``FortiSOAR.from_env_file``;
the SSH/sudo sync uses the ``pyfsr appliance content-hub sync`` verb's transport
(``PYFSR_APPLIANCE_*``), invoked here as a library call so the script doesn't
shell out. The script reads those env files itself via ``_load_env``, so sourcing
them into the shell first is optional (belt-and-suspenders).

Run from the pyfsr repo root:

    set -a; . ./.env.fsr-ga; . ./.env.appliance; set +a   # optional
    .venv/bin/python deploy/content-hub-mirror/example_install_updated_connector.py

Prereqs:
  * mirror up at $MIRROR_HOST ($MIRROR_ADMIN defaults to http://$MIRROR_HOST:9000)
  * the editable hello-world source at $HW_SOURCE_DIR (the 1.2.0 one, adds ping)
  * appliance pointed at the mirror (setup-appliance.sh already run once)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

# ---- config from env -------------------------------------------------------
MIRROR_HOST = os.environ.get("MIRROR_HOST", "content-hub-mirror.example.com")
MIRROR_ADMIN = os.environ.get("MIRROR_ADMIN", f"http://{MIRROR_HOST}:9000")
CHM_TOKEN = os.environ.get("CHM_TOKEN", "")  # only if the mirror sets ADMIN_TOKEN

# editable hello-world source (adds the ping action); bumped to 1.2.0 there
HW_SOURCE_DIR = os.environ.get("HW_SOURCE_DIR", "/Users/dylanspille/PycharmProjects/test/hello-world")
ENV_FSR = os.environ.get("ENV_FSR", ".env.fsr-ga")  # appliance REST creds
ENV_APPLIANCE = os.environ.get("ENV_APPLIANCE", ".env.appliance")  # SSH/sudo creds

NAME = "hello-world"
VER_INITIAL = "1.1.1"  # ping-free baseline synthesized from the 1.2.0 source
VER_UPDATED = "1.2.0"  # adds the ping action
CONFIG_NAME = "mirror-hw-test"


# ---- helpers ---------------------------------------------------------------
def _load_env(path: str) -> dict[str, str]:
    """Parse a raw KEY=VALUE env file (values may contain '!' literally)."""
    env: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return env
    for line in p.read_text().splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v
    return env


def step(n: int, msg: str) -> None:
    print(f"\n==> [{n}] {msg}")


def fail(msg: str) -> typing.NoReturn:  # noqa: F821
    print(f"\nFAIL: {msg}", file=sys.stderr)
    sys.exit(1)


# ---- mirror publish --------------------------------------------------------
def build_tgz(source_dir: str, dest: str) -> str:
    """Pack the hello-world source dir into <name>/...tgz (top-level <name>/).

    publish_connector normalizes the top-level dir to <name> anyway, but a clean
    tarball keeps the payload small and predictable.
    """
    name = json.loads(Path(source_dir, "info.json").read_text())["name"]
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(source_dir, arcname=name)
    return dest


def build_baseline_tgz(source_dir: str, dest: str, version: str) -> str:
    """Synthesize a ping-free ``<version>`` baseline tgz from the ping-having source.

    The on-disk source is the *updated* one (1.2.0, ``ping`` wired in). For a
    faithful old->new update proof we need a real baseline that LACKS ``ping``,
    so we copy the source, strip ``ping`` from ``info.json`` (operations list)
    and ``connector.py`` (import + operations map), set the version, and tar it.
    The standalone ``ping`` function left behind in ``operations.py`` is never
    imported, so it is harmless — keeping it makes the only delta between the
    baseline and the update the wiring + the version bump.
    """
    info = json.loads(Path(source_dir, "info.json").read_text())
    name = info["name"]
    with tempfile.TemporaryDirectory(prefix="hw-baseline-src-") as tmp:
        dst = Path(tmp) / name
        shutil.copytree(source_dir, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
        # info.json: set the baseline version + drop the ping operation
        info["version"] = version
        info["operations"] = [o for o in info.get("operations", []) if o.get("operation") != "ping"]
        Path(dst, "info.json").write_text(json.dumps(info, indent=2))
        # connector.py: drop the ping import + the ping entry in the operations map
        conn = Path(dst, "connector.py").read_text()
        conn = conn.replace("reverse_text, ping", "reverse_text")
        conn = conn.replace('    "ping": ping,\n', "")
        Path(dst, "connector.py").write_text(conn)
        with tarfile.open(dest, "w:gz") as tf:
            tf.add(dst, arcname=name)
    return dest


def publish_to_mirror(tgz_path: str, release: str = "1") -> dict:
    """POST the tgz to the mirror admin API; server builds the RPM + stages it."""
    import urllib.request

    url = f"{MIRROR_ADMIN}/api/connector"
    # multipart form: tgz file + release
    boundary = "----pyfsr-example"
    with open(tgz_path, "rb") as fh:
        blob = fh.read()
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="tgz"; filename="{Path(tgz_path).name}"\r\n'
            f"Content-Type: application/gzip\r\n\r\n"
        ).encode()
        + blob
        + (
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="release"\r\n\r\n{release}\r\n--{boundary}--\r\n'
        ).encode()
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if CHM_TOKEN:
        headers["Authorization"] = f"Bearer {CHM_TOKEN}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    # mirror nests the built-RPM info under "published"; accept a flat shape too.
    published = resp.get("published", resp) if isinstance(resp, dict) else {}
    if "rpm_full_name" not in published:
        fail(f"publish did not return rpm_full_name: {resp}")
    return published


def mirror_serves(path: str) -> int:
    """HEAD-style GET a mirror path; return its HTTP status code."""
    import urllib.request

    url = f"http://{MIRROR_HOST}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code


# ---- appliance -------------------------------------------------------------
def make_client():
    from pyfsr import FortiSOAR

    env = _load_env(ENV_FSR)
    if "FSR_BASE_URL" not in env:
        fail(f"{ENV_FSR} missing FSR_BASE_URL")
    # FortiSOAR.from_env_file reads the file itself; ensure os.environ has it too.
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return FortiSOAR.from_env_file(ENV_FSR)


def make_sync_transport():
    from pyfsr.cli.appliance.content_hub import sync as content_hub_sync
    from pyfsr.cli.appliance.transport import transport_from_env

    env = _load_env(ENV_APPLIANCE)
    if "PYFSR_APPLIANCE_HOST" not in env:
        fail(f"{ENV_APPLIANCE} missing PYFSR_APPLIANCE_HOST")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    t = transport_from_env()
    return t, content_hub_sync


def content_hub_sync(transport, sync_fn) -> None:
    """Force a Content Hub sync on the appliance; fail the proof if it errors."""
    print(f"    sync target: {getattr(transport, 'target', '?')}")
    r = sync_fn(transport, force=True, yes=True)
    print(f"    {r}")
    if not r.ok:
        fail("content-hub sync failed — the appliance can't see the new content")


def install_version(client, version: str) -> None:
    """Install (or replace) hello-world <version> from the mirror, wait for done."""
    already = client.connectors.resolve_version(NAME)
    if already == version:
        print(f"    {NAME} {version} already installed — skipping")
        return
    if already is not None:
        print(f"    uninstalling existing {NAME} {already} …")
        client.connectors.uninstall(NAME)
    res = client.connectors.install(NAME, version, wait=True, timeout=300)
    status = getattr(res, "status", None) or (res.get("status") if isinstance(res, dict) else None)
    print(f"    install {NAME} {version} -> {status!r}")


def ensure_config(client, version: str):
    """Create a default config (default_greeting is required by check_health)."""
    cfgs = client.connectors.configurations(NAME)
    for c in cfgs:
        if getattr(c, "name", None) == CONFIG_NAME:
            return getattr(c, "config_id", None) or getattr(c, "id", None)
    cfg = client.connectors.create_configuration(
        NAME,
        {"default_greeting": "Hello", "salutation": "Bye"},
        name=CONFIG_NAME,
        version=version,
        default=True,
        exist_ok=True,
    )
    return getattr(cfg, "config_id", None) or getattr(cfg, "id", None)


def operations_of(client, version: str) -> list[str]:
    return [o.operation for o in client.connectors.operations(NAME, version=version)]


def run_ping_n_times(client, n: int = 5) -> None:
    seen: set[str] = set()
    for i in range(n):
        r = client.connectors.execute(NAME, "ping")
        st = r.data.get("server_time") if isinstance(r.data, dict) else None
        print(f"    ping #{i + 1}: ok={r.ok} status={r.status!r} server_time={st}")
        if not r.ok:
            fail(f"ping #{i + 1} was not Success (status={r.status!r})")
        if st is None:
            fail(f"ping #{i + 1} returned no server_time: {r.data}")
        if st in seen:
            fail(f"ping #{i + 1} reused timestamp {st} (stale/cached result!)")
        seen.add(st)
    print(f"    PASS: {n} runs, {n} distinct timestamps, all Success")


# ---- main ------------------------------------------------------------------
def main() -> int:
    if not Path(HW_SOURCE_DIR, "info.json").is_file():
        fail(f"hello-world source not found at {HW_SOURCE_DIR}")
    src_ver = json.loads(Path(HW_SOURCE_DIR, "info.json").read_text()).get("version")
    if src_ver != VER_UPDATED:
        fail(
            f"hello-world source is at {src_ver!r}, expected {VER_UPDATED!r} "
            f"(the source must already add the ping action)"
        )

    client = make_client()
    transport, sync_fn = make_sync_transport()

    # 1. publish a ping-free baseline (1.1.1) to the mirror
    step(1, f"publish ping-free baseline {NAME} {VER_INITIAL} to the mirror")
    with tempfile.TemporaryDirectory(prefix="hw-baseline-publish-") as tmp:
        tgz = build_baseline_tgz(HW_SOURCE_DIR, os.path.join(tmp, f"{NAME}-{VER_INITIAL}.tgz"), VER_INITIAL)
        summary = publish_to_mirror(tgz, release="1")
        print(f"    published baseline -> {summary['rpm_full_name']}")

    # 2. sync + install the baseline; confirm 'ping' is NOT yet present
    step(2, f"sync + install baseline {NAME} {VER_INITIAL}; assert 'ping' absent")
    content_hub_sync(transport, sync_fn)
    install_version(client, VER_INITIAL)
    ensure_config(client, VER_INITIAL)
    base_ver = client.connectors.resolve_version(NAME)
    print(f"    {NAME} installed version now: {base_ver}")
    ops = operations_of(client, base_ver)
    print(f"    operations at {base_ver}: {ops}")
    if "ping" in ops:
        fail(f"baseline {base_ver} already has 'ping' — the update delta is vacuous")

    # 3. update the mirror: publish the real 1.2.0 source (adds the ping action)
    step(3, f"publish updated {NAME} {VER_UPDATED} (adds the ping action) to the mirror")
    with tempfile.TemporaryDirectory(prefix="hw-publish-") as tmp:
        tgz = build_tgz(HW_SOURCE_DIR, os.path.join(tmp, f"{NAME}-{VER_UPDATED}.tgz"))
        summary = publish_to_mirror(tgz, release="1")
        print(f"    published -> {summary['rpm_full_name']}")
    # confirm the mirror serves the new version's install artifacts
    build_no = str(json.loads(Path(HW_SOURCE_DIR, "info.json").read_text()).get("buildNumber", 1))
    for sub in (build_no, "latest"):
        code = mirror_serves(f"/content-hub/{NAME}-{VER_UPDATED}/{sub}/{NAME}-{VER_UPDATED}.zip")
        print(f"    mirror /content-hub/{NAME}-{VER_UPDATED}/{sub}/...zip -> HTTP {code}")

    # 4. sync + install the updated version; confirm 'ping' IS now present
    step(4, f"sync + install updated {NAME} {VER_UPDATED}; assert 'ping' present")
    content_hub_sync(transport, sync_fn)
    install_version(client, VER_UPDATED)
    new_ver = client.connectors.resolve_version(NAME)
    print(f"    {NAME} installed version now: {new_ver}")
    if new_ver != VER_UPDATED:
        fail(f"expected {VER_UPDATED} after install, got {new_ver!r}")
    ops = operations_of(client, new_ver)
    print(f"    operations at {new_ver}: {ops}")
    if "ping" not in ops:
        fail(f"the new 'ping' action is not present after updating to {new_ver}")

    # 5. run the new action repeatedly
    step(5, "run the new 'ping' action 5x in a row (distinct timestamps = fresh)")
    ensure_config(client, new_ver)
    run_ping_n_times(client, n=5)

    print("\n== PASS: full loop proven ==")
    print("  baseline 1.1.1 (no ping) installs through the mirror; publishing 1.2.0")
    print("  + syncing makes the new version installable, and the new 'ping' action")
    print("  runs repeatedly with fresh (distinct) results each time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
