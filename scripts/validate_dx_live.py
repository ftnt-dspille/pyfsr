#!/usr/bin/env python3
"""Live validation for the two DX improvements against a real FortiSOAR box.

Reads **all** connection config from the environment — it ships no hardcoded
host or credentials, so it is safe to commit. Required env vars:

    BASE_URL      e.g. https://fortisoar.example.com
    FSR_USER      login user
    FSR_PASSWORD  login password

Optional:
    DX_TEAM       a team name to bind a created key to (defaults to none)

What it checks:

1. ``raise_on_status=False`` (fire-and-observe-status) — a real 4xx returns the
   raw ``requests.Response`` with the right ``.status_code`` instead of raising,
   on both ``client.get`` and ``playbooks.trigger_by_name``. Also confirms the
   default path still raises a typed exception.
2. ``client.api_keys.ensure_usable(name, teams=)`` — validates the wire shapes
   the coordination logic depends on: ``GET /api/3/api_keys`` (list), the
   ``GET /api/auth/users?show_api_key=true`` plaintext-recovery shape, and the
   ``auth_config.is_api_key_retrievable`` read. Where the appliance permits
   api-key-user creation, exercises the full create → recover → authenticate →
   idempotent-reuse → cleanup path; where the appliance's ``/api/auth/users``
   handler is broken (a known server-side bug on some builds), reports it
   gracefully rather than failing.

Run:
    BASE_URL=... FSR_USER=... FSR_PASSWORD=... .venv/bin/python scripts/validate_dx_live.py
"""

from __future__ import annotations

import os
import sys
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = os.environ.get("BASE_URL")
FSR_USER = os.environ.get("FSR_USER")
FSR_PASSWORD = os.environ.get("FSR_PASSWORD")
if not (BASE_URL and FSR_USER and FSR_PASSWORD):
    sys.exit("set BASE_URL, FSR_USER, FSR_PASSWORD in the environment")

BASE_URL = BASE_URL.rstrip("/")
TEAM = os.environ.get("DX_TEAM") or ""
KEY_NAME = f"pyfsr-dx-validate-{int(time.time())}"

from pyfsr import FortiSOAR  # noqa: E402
from pyfsr.api.api_keys import _api_key_plaintext  # noqa: E402
from pyfsr.exceptions import APIError, ResourceNotFoundError  # noqa: E402


def line(m: str) -> None:
    print(f"[+] {m}", flush=True)


def hr(t: str) -> None:
    print(f"\n=== {t} ===", flush=True)


def validate_raise_on_status(client: FortiSOAR) -> None:
    hr("DX#1: raise_on_status=False (fire-and-observe-status)")
    bogus = "00000000-0000-0000-0000-000000000000"

    # (a) Default path still raises a typed exception on 4xx.
    try:
        client.records("alerts").get(bogus)
        raise AssertionError("expected ResourceNotFoundError on default path")
    except ResourceNotFoundError as e:
        line(f"default path raised {type(e).__name__} as expected (status {getattr(e.response, 'status_code', '?')})")

    # (b) raise_on_status=False returns the raw Response — no raise.
    resp = client.get(f"/api/3/alerts/{bogus}", raise_on_status=False)
    assert isinstance(resp, requests.Response), type(resp)
    assert resp.status_code == 404, resp.status_code
    body = resp.json()
    line(
        f"client.get(raise_on_status=False) -> raw Response, status={resp.status_code}, "
        f"body keys={sorted(body.keys())[:6]}"
    )

    # (c) trigger_by_name on a non-existent route — raw 4xx, not a raise.
    trig = client.playbooks.trigger_by_name(
        "pyfsr-dx-no-such-route-xyz",
        body={},
        raise_on_status=False,
    )
    assert isinstance(trig, requests.Response), type(trig)
    assert 400 <= trig.status_code < 500, trig.status_code
    line(f"trigger_by_name(raise_on_status=False) -> status={trig.status_code} (4xx, no raise)")
    line("DX#1 OK")


def validate_ensure_usable(client: FortiSOAR) -> None:
    hr("DX#2: api_keys.ensure_usable — wire shapes + end-to-end where possible")

    # 1. List wire shape — each binding carries name/userId/uuid.
    keys = client.api_keys.list()
    line(f"GET /api/3/api_keys -> {len(keys)} binding(s)")
    assert keys, "no API-key bindings on the appliance to validate against"
    sample = keys[0]
    assert {"name", "userId", "uuid"} <= set(sample.keys()), sample.keys()
    line(f"  binding shape ok: name={sample['name']!r} userId={sample['userId']!r}")

    # 2. Plaintext-recovery wire shape (read-only, safe — no regenerate).
    #    GET /api/auth/users?uuid=&show_api_key=true -> {api_key: {key, retrievable}}.
    user = client.api_users.get(sample["userId"], show_api_key=True)
    assert "api_key" in user, user.keys()
    ak = user["api_key"] or {}
    plaintext = _api_key_plaintext(client, sample["userId"])
    line(
        f"  recover shape ok: api_key keys={sorted(ak.keys())}; "
        f"plaintext {'present' if plaintext else 'masked (per-key retrievable=False)'}"
    )

    # 3. retrievable_mode read.
    retr = client.auth_config.is_api_key_retrievable()
    line(f"  is_api_key_retrievable() -> {retr} (bool ok)")

    # 4. End-to-end create path. Some appliance builds have a broken
    #    /api/auth/users handler (server-side `encrypt()` kwarg bug) that 400s
    #    on any api-key-user creation — that's not a pyfsr defect, so report it
    #    gracefully instead of failing the whole validation.
    teams = [TEAM] if TEAM else None
    line(f"attempting ensure_usable(create) name={KEY_NAME!r}" + (f" teams=[{TEAM!r}]" if TEAM else " (no team)"))
    try:
        binding, plaintext2 = client.api_keys.ensure_usable(
            name=KEY_NAME,
            teams=teams,
            api_key_validity=365,
        )
    except APIError as e:
        body = getattr(getattr(e, "response", None), "text", "") or str(e)
        if "preserve_compatibility" in body or "encrypt()" in body:
            line(
                "  SKIP end-to-end: appliance /api/auth/users is broken "
                "(server-side `encrypt() preserve_compatibility` bug) — "
                "create path cannot run on this build. Coordination logic is "
                "covered by unit tests; wire shapes validated above."
            )
            line("DX#2 OK (wire shapes; end-to-end blocked by appliance bug)")
            return
        raise
    user_uuid = binding["userId"]
    line(
        f"  created: binding={binding.get('uuid')} user={user_uuid} plaintext={plaintext2[:6]}… (len {len(plaintext2)})"
    )

    # The recovered plaintext must authenticate a second client.
    key_client = FortiSOAR(BASE_URL, token=plaintext2, verify_ssl=False, suppress_insecure_warnings=True)
    who = key_client.get("/api/auth/users", params={"uuid": user_uuid})
    assert who.get("uuid") == user_uuid, who
    line(f"  plaintext authenticates: fetched user {who.get('uuid')}")

    # Idempotent reuse — same binding, no new user.
    binding2, plaintext3 = client.api_keys.ensure_usable(
        name=KEY_NAME,
        teams=teams,
        api_key_validity=365,
    )
    assert binding2["userId"] == user_uuid
    line(f"  idempotent reuse ok (same user {binding2['userId']})")

    # Cleanup.
    try:
        client.api_users.revoke(user_uuid)
        line(f"  cleanup: revoked user {user_uuid}")
    except Exception as e:  # noqa: BLE001
        line(f"  cleanup: revoke failed (non-fatal): {e}")
    line("DX#2 OK (end-to-end)")


def main() -> int:
    client = FortiSOAR(
        BASE_URL, username=FSR_USER, password=FSR_PASSWORD, verify_ssl=False, suppress_insecure_warnings=True
    )
    line(f"connected to {BASE_URL} as {FSR_USER} (version {client.version()})")
    validate_raise_on_status(client)
    validate_ensure_usable(client)
    print("\nALL LIVE VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
