"""License (or re-license) a FortiSOAR from FortiFlex: mint, redeem, verify.

Unifies two SDKs around one job -- give an appliance a working license:

* **pyfortiflex** allocates an entitlement on a FortiFlex config and mints its
  single-use redemption token (``mint_token``).
* **pyfsr** redeems that token on the appliance
  (``system.deploy_flex_license``) and then proves the result
  (``system.license_identity``, ``ai.token_balance``).

The flow is derived from the appliances' actual API behavior:

* **The login is the license oracle.** ``POST /auth/authenticate`` answers 200
  when the appliance is licensed and usable. A refusal whose text mentions the
  license (no license, or the ``FSR-Auth-018`` duplicate-license lockout) means
  a license is needed; any other refusal is a real failure and stops here.
* **Redemption is unauthenticated and asynchronous.** The token goes to
  ``POST /api/public/license`` (``action: install_flex_license``) -- no
  credentials, so it works on a fresh or license-locked appliance. Deployment
  is polled via ``action: get_status`` (``depl_status``: ``in_progress`` /
  ``finished`` / ``failed``).
* **``get_status`` alone is not proof.** It can read ``finished`` left over
  from a *previous* deployment. The real proof is a successful login whose
  license details show the serial number you just redeemed, so this script
  polls that instead of trusting the status.
* **FortiFlex tokens are single-use and spent even by a failed redeem.**
  Retrying means minting again -- on the same serial (the owner keeps it), so
  no extra entitlement is burned.
* **FortiAI tokens are a per-device pool keyed to the license serial**
  (``type=DEVICE_LEVEL``), read through the ``fortinet-fortiai-proxy``
  connector. A freshly redeemed serial's pool is granted by the FortiAI
  backend and can lag the license deployment by minutes, so the balance is
  polled for a bounded window and reported, never fatal.

Not done here (needs appliance shell, not REST): accepting the first-boot
application EULA on a never-logged-in appliance. Until it is accepted,
authentication fails even with a valid license.

Usage:
    python examples/relicense_from_fortiflex.py soar.example.com \
        --user csadmin --password ... --flex-config 29529

    # or point at an FSR_* env file for the FortiSOAR side:
    python examples/relicense_from_fortiflex.py --env-file .env.lab --flex-config 29529

    # re-deploy over an already-active license (serial swap / pool re-arm):
    python examples/relicense_from_fortiflex.py soar.example.com --user csadmin \
        --password ... --flex-config 29529 --force

    # report license + FortiAI token state only, touch nothing:
    python examples/relicense_from_fortiflex.py soar.example.com --user csadmin \
        --password ... --check-only

Needs ``pip install pyfortiflex`` and ``FORTIFLEX_USER`` / ``FORTIFLEX_PASS``
(the FortiFlex IAM API id and password), or ``--flex-user`` / ``--flex-pass``.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from pyfsr import FortiSOAR

POLL_S = 15


@dataclass
class Minted:
    """What FortiFlex handed us: the entitlement serial and its one-shot token."""

    serial: str
    token: str
    created: bool


def build_client(args: argparse.Namespace) -> FortiSOAR:
    """The credentialed FortiSOAR client: from an FSR_* env file or from args."""
    if args.env_file:
        return FortiSOAR.from_env_file(args.env_file, override=True, timeout=60)
    return FortiSOAR(
        args.host,
        (args.user, args.password),
        verify_ssl=args.verify_ssl,
        suppress_insecure_warnings=True,
        port=args.port,
        timeout=60,
    )


def public_client(args: argparse.Namespace) -> FortiSOAR:
    """The no-auth client for the unauthenticated license API. Built from the
    same connection source as the credentialed one (env file port included)."""
    if args.env_file:
        from pyfsr.config import EnvConfig

        cfg = EnvConfig.from_env_file(args.env_file, override=True)
        host, port = cfg.base_url, cfg.port
    else:
        host, port = args.host, args.port
    return FortiSOAR(
        host,
        public=True,
        verify_ssl=args.verify_ssl,
        suppress_insecure_warnings=True,
        port=port,
        timeout=60,
    )


def login_probe(args: argparse.Namespace) -> tuple[FortiSOAR | None, str | None]:
    """Try a credentialed login. Returns ``(client, None)`` on success or
    ``(None, refusal_text)`` when the appliance refused -- the caller decides
    whether the refusal is a license reason or a real failure."""
    try:
        return build_client(args), None
    except requests.HTTPError as e:
        return None, str(e)


def token_balance(client: FortiSOAR, *, attempts: int, wait_s: int = 30) -> object | None:
    """Read the FortiAI token pool, retrying while the backend has not granted
    it yet (a fresh serial's pool can lag the license deployment). ``None``
    after the attempts run out -- report-only, never fatal."""
    for attempt in range(1, attempts + 1):
        try:
            return client.ai.token_balance()
        except Exception as e:  # any failure here is report-only
            if attempt == attempts:
                print(f"FortiAI token balance unavailable: {e}")
                return None
            print(f"  [fortiai] pool not granted yet ({e}); retrying ({attempt}/{attempts - 1})")
            time.sleep(wait_s)
    return None


def report_state(client: FortiSOAR, label: str) -> None:
    """Print the license identity and the FortiAI token pool (best effort)."""
    print(f"--- {label} ---")
    identity = client.system.license_identity()
    print(f"license: serial={identity.serial_no} edition={identity.edition} version={identity.fsr_version}")
    balance = token_balance(client, attempts=1)
    if balance is not None:
        print(
            f"FortiAI tokens ({balance.type}): {balance.remain_tokens:,} left of "
            f"{balance.entitled_tokens:,} (device serial {balance.sn})"
        )


def mint(args: argparse.Namespace, owner: str) -> Minted:
    """Allocate the owner's entitlement on the FortiFlex config and mint a
    fresh redemption token. pyfortiflex is optional for this script, so it is
    imported only when a mint is actually needed."""
    import pyfortiflex

    if args.flex_user:
        os.environ["FORTIFLEX_USER"] = args.flex_user
    if args.flex_pass:
        os.environ["FORTIFLEX_PASS"] = args.flex_pass
    result = pyfortiflex.mint_token(
        pyfortiflex.FortiFlexClient.from_env(),
        args.flex_config,
        owner,
        timeout_s=args.mint_timeout,
        poll_interval_s=POLL_S,
        progress=lambda m: print(f"  [fortiflex] {m}"),
    )
    print(
        f"minted: serial {result.serial_number} "
        f"({'newly created' if result.created else 'reused'} entitlement, "
        f"config {result.config_id})"
    )
    return Minted(serial=str(result.serial_number), token=str(result.token), created=bool(result.created))


def redeem(args: argparse.Namespace, token: str) -> dict:
    """Submit the token on the unauthenticated license API and wait for the
    deployment to resolve. Returns ok/depl_status/depl_message (never raises
    on a failed redemption -- the token is spent either way)."""
    outcome = public_client(args).system.deploy_flex_license(token, timeout=args.redeem_timeout, poll_interval=POLL_S)
    return dict(outcome)


def verify_licensed(args: argparse.Namespace, serial: str, timeout_s: float) -> FortiSOAR:
    """Poll until a real login succeeds AND the active license is the serial we
    redeemed. This is the only trustworthy proof (see the module docstring)."""
    deadline = time.monotonic() + timeout_s
    last = "never checked"
    while time.monotonic() < deadline:
        client, refusal = login_probe(args)
        if client is not None:
            active = client.system.license_identity().serial_no
            if active == serial:
                print(f"verified: login OK, active license serial {serial}")
                return client
            last = f"login OK but active serial is {active!r}, waiting for {serial!r}"
        else:
            last = f"login still refused: {(refusal or '?')[:160]}"
        print(f"  [verify] {last}; {int(deadline - time.monotonic())}s left")
        time.sleep(POLL_S)
    raise SystemExit(
        f"license deployment did not become provable within {int(timeout_s)}s "
        f"(last state: {last}). The token is spent; check the appliance, then re-run "
        f"(it will mint again on the same serial)."
    )


def appliance_host(args: argparse.Namespace) -> str:
    """The appliance hostname, for a stable default owner description."""
    if args.env_file:
        from pyfsr.config import EnvConfig

        base = EnvConfig.from_env_file(args.env_file, override=True).base_url
    else:
        base = args.host
    return urlparse(base if "://" in base else f"https://{base}").hostname or "appliance"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", nargs="?", help="FortiSOAR base URL (or --env-file instead)")
    ap.add_argument("--env-file", help="FSR_* env file with the FortiSOAR connection")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--user", default="csadmin")
    ap.add_argument("--password")
    ap.add_argument("--verify-ssl", action="store_true", help="verify TLS (lab appliances are usually self-signed)")
    ap.add_argument("--flex-config", help="FortiFlex config id or name to allocate from")
    ap.add_argument("--flex-user", help="FortiFlex IAM API id (default $FORTIFLEX_USER)")
    ap.add_argument("--flex-pass", help="FortiFlex IAM API password (default $FORTIFLEX_PASS)")
    ap.add_argument(
        "--owner",
        help="entitlement description that claims the serial "
        "(default: 'pyfsr-license <host>'; keep it stable so "
        "re-runs reuse the same serial)",
    )
    ap.add_argument("--force", action="store_true", help="re-deploy a fresh token even though the login succeeds")
    ap.add_argument("--check-only", action="store_true", help="report license + FortiAI token state and exit")
    ap.add_argument(
        "--mint-timeout", type=int, default=1800, help="how long to wait for an entitlement to turn ACTIVE (s)"
    )
    ap.add_argument("--redeem-timeout", type=float, default=900, help="how long to wait for the license deployment (s)")
    ap.add_argument("--verify-timeout", type=float, default=900, help="how long to wait for the login+serial proof (s)")
    ap.add_argument(
        "--token-wait", type=int, default=600, help="how long to wait for the FortiAI device pool to appear (s)"
    )
    args = ap.parse_args()

    if not args.env_file and not (args.host and args.password):
        ap.error("give host + --password, or --env-file")

    # 1. Assess: the login is the license oracle.
    client, refusal = login_probe(args)
    if client is not None:
        report_state(client, "appliance is licensed and usable")
        if args.check_only:
            return
        if not args.force:
            print("nothing to do (pass --force to re-deploy a fresh token anyway)")
            return
        print("--force: re-deploying a fresh token over the active license")
    elif args.check_only:
        raise SystemExit(f"cannot report state, login refused: {(refusal or '?')[:300]}")
    elif refusal and "licen" not in refusal.lower():
        raise SystemExit(f"login refused for a non-license reason -- not fixing that here: {(refusal or '?')[:300]}")
    else:
        print(f"appliance needs a license: {(refusal or '?')[:200]}")

    # 2. Mint on FortiFlex (separate SDK; imported only when actually needed).
    if not args.flex_config:
        ap.error("--flex-config is required to mint a license token")
    flex = mint(args, args.owner or f"pyfsr-license {appliance_host(args)}")

    # 3. Redeem on the appliance (unauthenticated; works locked or licensed).
    print("redeeming the token on the appliance (deployment is asynchronous)...")
    outcome = redeem(args, flex.token)
    if not outcome.get("ok"):
        raise SystemExit(
            f"license deployment failed: {outcome.get('depl_message') or outcome} "
            "(the token is spent; fix the cause, then re-run to mint again)"
        )
    print(f"deployment reported {outcome['depl_status']!r} after {outcome['polls']} poll(s)")

    # 4. Prove it: a real login whose license shows the redeemed serial.
    client = verify_licensed(args, flex.serial, args.verify_timeout)

    # 5. Report the FortiAI pool (per-device, keyed to the serial; may lag).
    balance = token_balance(client, attempts=max(args.token_wait // 30, 1))
    if balance is not None:
        print(
            f"FortiAI tokens ({balance.type}): {balance.remain_tokens:,} left of "
            f"{balance.entitled_tokens:,} (device serial {balance.sn})"
        )
    else:
        print(
            "FortiAI token pool not granted yet -- the backend can lag the deployment "
            "by minutes; re-run with --check-only later to see it."
        )


if __name__ == "__main__":
    main()
