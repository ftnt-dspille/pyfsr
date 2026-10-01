#!/usr/bin/env python3
"""User password lifecycle -- admin creates a user, sets its password, the user
logs in and changes its own password.

Walks the two password endpoints end to end:

  1. An admin creates a user with no password (``client.users.create``). It
     cannot log in yet.
  2. The admin sets the user's password -- ``client.users.reset_password``
     (``POST /api/3/resetpassword``). das refuses this for your own login.
  3. The user logs in with that password; ``client.users.whoami`` shows who the
     session belongs to (login ID, People UUID, das user ID).
  4. The user changes its own password -- ``client.users.change_password(old,
     new)`` (``PUT /api/3/changepassword``). The People UUID and login ID the
     endpoint needs are looked up automatically.
  5. The new password logs in; the old one is refused.

These are FortiSOAR **application** logins. The appliance's Linux accounts
(``csadmin`` over SSH) are separate -- see
``Appliance.host.set_os_password``.

Live-verified on FortiSOAR 8.0.1.

Usage:
    export FSR_BASE_URL=https://fortisoar.example.com
    export FSR_USERNAME=csadmin FSR_PASSWORD='<your-password>'
    python user_password_lifecycle.py --login pwtest01

    # or use a pyfsr instance alias from ~/.pyfsr/instances.toml
    python user_password_lifecycle.py --instance my-box --login pwtest01
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

import requests

from pyfsr import FortiSOAR


def _admin_client(instance: str | None) -> FortiSOAR:
    if instance:
        from pyfsr.instances import InstanceRegistry

        return InstanceRegistry.load().client(instance)
    base = os.environ.get("FSR_BASE_URL")
    user = os.environ.get("FSR_USERNAME", "csadmin")
    password = os.environ.get("FSR_PASSWORD")
    if not base or not password:
        sys.exit("set FSR_BASE_URL and FSR_PASSWORD (and FSR_USERNAME), or pass --instance")
    return FortiSOAR(base, username=user, password=password, verify_ssl=False)


def _new_password() -> str:
    # Mixed classes so it passes the default password policy.
    return "Fsr!" + secrets.token_urlsafe(9) + "9a"


def _can_login(base_url: str, login: str, password: str) -> bool:
    try:
        FortiSOAR(base_url, username=login, password=password, verify_ssl=False)
    except requests.HTTPError as exc:
        # Bad credentials, an unset password and a locked account all come back
        # as 400 "Invalid credentials or account locked".
        if exc.response is not None and exc.response.status_code in (400, 401):
            return False
        raise
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instance", help="pyfsr instance alias (else FSR_* env vars)")
    ap.add_argument("--login", default="pwtest01", help="login ID of the user to create")
    ap.add_argument("--role", default="Read-Only User", help="role name for the new user")
    ap.add_argument("--team", default="SOC Team", help="team name for the new user")
    args = ap.parse_args()

    admin = _admin_client(args.instance)
    base = admin.base_url
    first_pw, second_pw = _new_password(), _new_password()
    print(f"admin: {admin.users.whoami()['loginid']} on {base}")

    print(f"\n[1] create {args.login} with no password")
    person = admin.users.create(
        args.login,
        "",
        "PW",
        "Test",
        f"{args.login}@example.com",
        roles=[args.role],
        teams=[args.team],
        typed=False,
    )
    print(f"    People UUID {person['uuid']}")
    early = _can_login(base, args.login, first_pw)
    print(f"    login before a password is set: {'works (unexpected)' if early else 'refused'}")

    print("\n[2] admin sets the password")
    print("   ", admin.users.reset_password(args.login, first_pw))

    print("\n[3] user logs in")
    user = FortiSOAR(base, username=args.login, password=first_pw, verify_ssl=False)
    print("    whoami:", user.users.whoami())

    print("\n[4] user changes own password")
    print("   ", user.users.change_password(first_pw, second_pw))

    print("\n[5] verify")
    new_ok = _can_login(base, args.login, second_pw)
    old_ok = _can_login(base, args.login, first_pw)
    print(f"    new password: {'works' if new_ok else 'REFUSED'}")
    print(f"    old password: {'STILL WORKS' if old_ok else 'refused'}")
    print(f"\n{args.login} password is now: {second_pw}")

    return 0 if new_ok and not old_ok else 1


if __name__ == "__main__":
    sys.exit(main())
