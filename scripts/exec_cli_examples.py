#!/usr/bin/env python3
"""Execute the offline-runnable ``pyfsr playbook`` commands the guides teach.

Complements ``check_doc_examples.py`` (which statically lints symbols/flags)
by *actually running* the commands end-to-end against real library fixtures and
asserting they succeed (or fail, where the doc shows a failure path). This is
the layer that proves the examples "work right" rather than merely exist.

What runs (no live appliance / API needed):

- ``pyfsr playbook steps`` / ``step-help <type> [--schema]`` — offline catalog.
- ``pyfsr playbook examples [--intent ..] [--stage ..]`` — library listing.
- ``pyfsr playbook show <slug>`` -- print one playbook's metadata + YAML.
- ``pyfsr playbook validate <file>`` — compile + diagnostics; exit 0 on a
  clean playbook, nonzero on a broken one (the error path is asserted too).
- ``pyfsr playbook compile <file> -o <out>`` — emit envelope JSON; output is
  parsed as JSON to prove it's real.
- ``pyfsr playbook deploy <file> --dry-run`` — show the import plan without
  posting (offline).

What does NOT run here (needs a live box / API): every ``pyfsr appliance ...``
verb (SSH), ``deploy`` (without ``--dry-run``), and ``check-fresh``. Those are
covered by ``make doctest`` where ReplayTransport captures exist, else deferred.

Invoked via ``[sys.executable, '-m', 'pyfsr.cli.__main__', ...]`` so it needs no
``pyfsr`` binary on PATH -- only an importable install (``-e .`` in CI). Skips
with a warning if the ``[playbooks]`` extra is absent (authoring import fails).

Exit code 1 if any command misbehaves, 0 otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO_ROOT, "examples", "playbooks", "library", "manifest.json")


def _run(args, timeout=60):
    """Run `python -m pyfsr.cli.__main__ <args>`; return (returncode, stdout, stderr)."""
    cmd = [sys.executable, "-m", "pyfsr.cli.__main__"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _pick_fixture():
    """Return (path, slug) of a compiles-OK library playbook from the manifest."""
    m = json.load(open(MANIFEST))
    for pb in m["playbooks"]:
        if pb.get("compiles_ok"):
            return os.path.join(REPO_ROOT, pb["path"]), pb["slug"]
    raise RuntimeError("no compiles_ok playbook in library manifest")


class Fail(Exception):
    pass


def check(label, args, expect_exit=0, stdout_test=None, stderr_test=None):
    rc, out, err = _run(args)
    detail = f"rc={rc}" + (f"\n--stdout--\n{out[:400]}" if out else "") + (f"\n--stderr--\n{err[:400]}" if err else "")
    if rc != expect_exit:
        raise Fail(f"{label}: expected exit {expect_exit}, got {rc}\n{detail}")
    if stdout_test and not stdout_test(out):
        raise Fail(f"{label}: stdout assertion failed\n{detail}")
    if stderr_test and not stderr_test(err):
        raise Fail(f"{label}: stderr assertion failed\n{detail}")
    print(f"  ok  {label}  (exit {rc})")
    return out


def main():
    # gate on the [playbooks] extra
    try:
        import pyfsr.authoring  # noqa: F401
    except Exception:
        print(
            "WARNING: pyfsr.authoring unavailable ([playbooks] extra not installed); skipping CLI execution checks",
            file=sys.stderr,
        )
        return 0

    fixture, slug = _pick_fixture()
    print(f"fixture: {os.path.relpath(fixture, REPO_ROOT)}  slug: {slug}")

    failures = []
    checks = [
        ("steps", ["playbook", "steps"], dict(stdout_test=lambda o: "step type" in o.lower())),
        (
            "step-help",
            ["playbook", "step-help", "manual_input"],
            dict(stdout_test=lambda o: "manual_input" in o.lower()),
        ),
        (
            "step-help --schema",
            ["playbook", "step-help", "decision", "--schema"],
            dict(stdout_test=lambda o: "schema" in o.lower() or "arguments" in o.lower()),
        ),
        ("examples", ["playbook", "examples"], dict(stdout_test=lambda o: "playbook" in o.lower())),
        ("examples --intent", ["playbook", "examples", "--intent", "incident"], {}),
        ("examples --stage", ["playbook", "examples", "--stage", "action"], {}),
        (
            "show <slug>",
            ["playbook", "show", slug],
            dict(stdout_test=lambda o: "name:" in o.lower() or slug in o.lower()),
        ),
        ("validate <clean>", ["playbook", "validate", fixture], dict(expect_exit=0)),
        (
            "compile -o <json>",
            ["playbook", "compile", fixture, "-o", "/tmp/_exec_cli_compile.json"],
            dict(stdout_test=lambda o: True),
        ),  # success asserted by JSON parse below
        (
            "deploy --dry-run",
            ["playbook", "deploy", fixture, "--dry-run"],
            dict(stdout_test=lambda o: "playbook" in o.lower() or len(o) > 0),
        ),
    ]
    # run the success-path checks
    for label, args, kw in checks:
        try:
            check(label, args, **kw)
        except Fail as e:
            failures.append(str(e))
        except Exception as e:
            failures.append(f"{label}: unexpected {type(e).__name__}: {e}")

    # compile output must be valid JSON with the expected envelope shape
    try:
        out_path = "/tmp/_exec_cli_compile.json"
        if os.path.exists(out_path):
            d = json.load(open(out_path))
            assert "data" in d and "type" in d, f"missing envelope keys; got {list(d)[:6]}"
            print(f"  ok  compile JSON envelope  (keys: {list(d)[:4]})")
        else:
            failures.append("compile -o: output file not written")
    except Exception as e:
        failures.append(f"compile JSON: {type(e).__name__}: {e}")

    # error path: a broken playbook must exit nonzero
    broken = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    broken.write(
        "name: broken-demo\nplaybooks:\n  - name: P\n    steps:\n      - name: S\n        type: not_a_real_type\n"
    )
    broken.close()
    try:
        check("validate <broken> (expect nonzero)", ["playbook", "validate", broken.name], expect_exit=1)
    except Fail:
        # argparse may exit 2 on some failures; accept any nonzero
        rc, out, err = _run(["playbook", "validate", broken.name])
        if rc == 0:
            failures.append("validate <broken>: expected nonzero exit, got 0")
        else:
            print(f"  ok  validate <broken> nonzero  (exit {rc})")
    finally:
        os.unlink(broken.name)

    print(f"\n--- {len(checks) + 2} CLI checks; {len(failures)} failure(s) ---")
    if failures:
        print("\n".join(f"  FAIL {f}" for f in failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
