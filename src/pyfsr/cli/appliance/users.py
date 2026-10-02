"""``appliance.users`` -- login accounts that only the appliance can manage.

Deleting a People record through the API leaves the user's login (the das
account) behind, and ``DELETE /api/auth/users`` is refused even for an
administrator. Fortinet ships ``/opt/cyops/scripts/userDelete`` for this: it
removes every login UUID listed in ``usersToDelete.txt``. :func:`delete_logins`
drives that script and restores the list file afterwards.

The login UUID is the People record's ``userId`` -- read it (for example with
``client.users.lookup(loginid)["user_id"]``) *before* deleting the People record.

:func:`logged_in` and :func:`logout` wrap ``csadm user``.
"""

from __future__ import annotations

import re

from .facts import Facts
from .transport import Transport, TransportError

SCRIPTS_DIR = "/opt/cyops/scripts"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9_.@+-]+$")

# Back up the list file, write the UUIDs, answer the script's prompt, and put
# the original list back on any exit. ``cat >`` keeps the file's owner and mode.
_DELETE_SCRIPT = f"""
set -u
cd {SCRIPTS_DIR} || exit 3
if [ ! -x ./userDelete ]; then echo "userDelete not found in {SCRIPTS_DIR}" >&2; exit 3; fi
backup=$(mktemp) || exit 3
cp usersToDelete.txt "$backup" 2>/dev/null || : > "$backup"
trap 'cat "$backup" > usersToDelete.txt; rm -f "$backup"' EXIT
printf '%s\\n' "$@" > usersToDelete.txt
echo y | ./userDelete
"""


def _remaining(facts: Facts, user_ids: list[str]) -> list[str]:
    in_list = ", ".join(f"'{u}'" for u in user_ids)  # validated UUIDs only
    rows = facts.psql(f"SELECT uuid FROM users WHERE uuid IN ({in_list})", db="das")
    return [row[0] for row in rows if row and row[0]]


def delete_logins(facts: Facts, user_ids: list[str], *, yes: bool = False, timeout: float = 300.0) -> str:
    """Remove login accounts with Fortinet's ``userDelete`` script.

    Args:
        user_ids: Login UUIDs (the People record's ``userId``), not People UUIDs.
        yes: confirmation gate; without it the call raises rather than run.

    Returns the script's output. Raises :class:`TransportError` if the script
    fails or any login is still present afterwards (the script prints
    ``DELETE 0`` for rows it had nothing to do with, so its output is not proof).
    """
    ids = [u.strip().lower() for u in user_ids]
    if not ids:
        raise ValueError("no login UUIDs given")
    bad = [u for u in ids if not _UUID_RE.match(u)]
    if bad:
        raise ValueError(f"not login UUIDs: {bad}")
    if not yes:
        raise PermissionError(f"refusing to delete {len(ids)} login(s) without confirmation (pass yes=True)")
    res = facts.transport.run(["bash", "-c", _DELETE_SCRIPT, "userDelete", *ids], sudo=True, timeout=timeout).check()
    left = _remaining(facts, ids)
    if left:
        raise TransportError(f"userDelete ran but these logins remain: {left}\n{res.stdout.strip()}")
    return res.stdout


def logged_in(transport: Transport, *, access_type: str = "Concurrent", limit: int = 10) -> list[str]:
    """Login IDs with a live session (``csadm user show-logged-in-users``).

    ``access_type`` is ``"Concurrent"`` (csadm's default) or ``"Named"``;
    ``limit`` is 1-30.
    """
    if access_type not in ("Concurrent", "Named"):
        raise ValueError("access_type must be 'Concurrent' or 'Named'")
    if not 1 <= limit <= 30:
        raise ValueError("limit must be 1-30")
    argv = ["csadm", "user", "show-logged-in-users", "--access-type", access_type, "--limit", str(limit)]
    out = transport.run(argv, sudo=True).check().stdout
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if "Users with following usernames are logged in currently" not in lines:
        return []
    names = lines[lines.index("Users with following usernames are logged in currently") + 1 :]
    return [n.strip() for n in ", ".join(names).split(",") if n.strip()]


def logout(transport: Transport, username: str, *, yes: bool = False) -> str:
    """End every session of ``username`` (``csadm user logout-user``). Returns csadm's message."""
    if not _LOGIN_RE.match(username or ""):
        raise ValueError(f"invalid login ID {username!r}")
    if not yes:
        raise PermissionError(f"refusing to log out {username!r} without confirmation (pass yes=True)")
    out = transport.run(["csadm", "user", "logout-user", "--username", username], sudo=True).check().stdout
    # csadm's message is the last line; Python warnings from the tool can precede it.
    message = next((line.strip() for line in reversed(out.splitlines()) if line.strip()), "")
    if "does not exist" in message:
        raise LookupError(f"no user with login ID {username!r}")
    return message
