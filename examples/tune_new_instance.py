"""Apply the standard tuning every brand-new FortiSOAR instance needs.

Repeatable, idempotent settings pass for a fresh appliance. Everything here is
REST-driven via pyfsr EXCEPT setting the admin password to a non-compliant
value like "fortinet" — FortiSOAR's password-complexity check rejects that on
every REST path, so it must be written directly to the DAS user table over the
appliance shell (see PASSWORD note at the bottom).

Usage:
    python examples/tune_new_instance.py 10.99.249.159 --port 13002 \
        --user csadmin --password fortinet
"""

from __future__ import annotations

import argparse

from pyfsr import FortiSOAR

# Tuning targets ---------------------------------------------------------------
IDLE_TIMEOUT_MIN = 360  # 6 h — the server-enforced maximum for idle_time
MAX_SESSION_MIN = 1440  # 24 h absolute session cap


def tune(client: FortiSOAR) -> None:
    # 1. Session/login timeout (DAS auth config) -------------------------------
    print("auth_config.idle_time   ->", client.auth_config.set_idle_timeout(IDLE_TIMEOUT_MIN))
    print("auth_config.max_session ->", client.auth_config.set_max_session(MAX_SESSION_MIN))

    # 2. Playbook execution logging --------------------------------------------
    # Force DEBUG on every run (playbooks can't opt out) and exclude no tags so
    # the log view shows everything.
    client.system_settings.set_playbook_debug_logging(True, allow_playbook_override=False)
    client.system_settings.set_workflow_log_filter([], operation="exclude")
    pv = client.system_settings.get_public_values()
    print("system_settings.logs    ->", pv["playbook"]["logs"])
    print("system_settings.debug   ->", pv["workflow_log_config"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--user", default="csadmin")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    client = FortiSOAR(
        args.host,
        (args.user, args.password),
        verify_ssl=False,
        suppress_insecure_warnings=True,
        port=args.port,
    )
    tune(client)
    print("\nDone. Note: setting the admin password to a non-compliant value")
    print("(e.g. 'fortinet') is NOT possible over REST — run on the appliance:")
    print(r"""
  # peppered-bcrypt hash must come from the box's csbcrypt (fixed global pepper):
  HASH=$(sudo -u cyops-auth /opt/cyops-auth/.env/bin/python3 -c \
    "import utilities.csbcrypt as b; print(b.CSBcrypt().get_hash('fortinet'))")
  sudo -u postgres psql -d das -c \
    "UPDATE users SET password='$HASH' WHERE login_id='csadmin';"
  # then clear any lockout so the new password takes effect immediately:
  sudo -u postgres psql -d das -c \
    "UPDATE userstatus SET status='active', num_failed=0, locked_until=NULL \
     WHERE user_id=(SELECT id FROM users WHERE login_id='csadmin');"
""")


if __name__ == "__main__":
    main()
