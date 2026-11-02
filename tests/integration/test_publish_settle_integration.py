"""How long after ``publish()`` returns a new module is usable (opt-in: pytest -m integration).

Measures, from the moment ``modules_admin.publish()`` returns, when the module:
shows as published, is listed in ``/api/3/modules`` (what role grants resolve
against), and accepts a record write once granted. ``publish()`` should only
return when all three hold; a non-zero lag here is what callers have been
papering over with their own polling. One publish; the module is removed after.
"""

import time

import pytest

pytestmark = pytest.mark.integration

MODULE = f"pyfsr_settle_{int(time.time()) % 100000}"
ROLE = "Full App Permissions"


def _until(check, timeout=300, interval=1.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            if check():
                return round(time.monotonic() - start, 1)
        except Exception:  # noqa: BLE001 - not ready yet
            pass
        time.sleep(interval)
    return None


def test_publish_settle_lag(client, capsys):
    ma = client.modules_admin
    ma.create_module(MODULE, label="pyfsr Settle Probe", fields=[ma.text_field("name")])
    try:
        ma.publish(timeout=900)
        returned = time.monotonic()
        published = _until(lambda: ma.is_published(MODULE))
        listed = _until(lambda: bool(client.get("/api/3/modules", params={"type": MODULE}).get("hydra:member")))
        client.roles.grant_module_permissions(ROLE, module=MODULE)

        def writable():
            r = client.post(f"/api/3/{MODULE}", {"name": "settle"})
            client.delete(r["@id"])
            return True

        written = _until(writable)
        total = round(time.monotonic() - returned, 1)
        with capsys.disabled():
            print(
                f"\nafter publish() returned: published +{published}s, listed +{listed}s, "
                f"writable (after grant) +{written}s, total {total}s"
            )
        assert published is not None and listed is not None and written is not None
    finally:
        ma.delete_module(MODULE)


def test_reads_during_a_publish(client, capsys):
    """What a schema read answers while a publish is migrating."""
    import threading

    from pyfsr.exceptions import is_migrate_transient

    ma = client.modules_admin
    name = f"{MODULE}b"
    ma.create_module(name, label="pyfsr Settle Probe B", fields=[ma.text_field("name")])
    seen: dict[str, int] = {}
    done = threading.Event()

    def publish():
        try:
            ma.publish(timeout=900)
        finally:
            done.set()

    t = threading.Thread(target=publish)
    t.start()
    try:
        while not done.is_set():
            try:
                ma.pending_changes()
                key = "ok"
            except Exception as exc:  # noqa: BLE001 - the answer is the observation
                key = ("transient: " if is_migrate_transient(exc) else "OTHER: ") + str(exc)[:70]
            seen[key] = seen.get(key, 0) + 1
            time.sleep(0.5)
        t.join()
        with capsys.disabled():
            print("\npending_changes() during a publish:")
            for k, n in seen.items():
                print(f"  {n:4} x {k}")
        assert not any(k.startswith("OTHER") for k in seen), seen
    finally:
        ma.delete_module(name)
