"""Drive a parent do-until loop that re-prompts a child playbook's Manual Input.

This example builds, deploys, and exercises a two-playbook collection
(``playbooks/do_until_validation_demo.yaml``) end to end:

* The **child** ("Validate Six Digit Number") pops a Manual Input that collects
  an integer ``my_number``, then a ``set_variable`` runs a jinja check and
  returns ``is_valid_number`` (plus the echoed number) as its output.
* The **parent** ("Loop Until Six Digits") calls the child synchronously inside
  a do-until / ``retry:`` loop, re-running it until ``is_valid_number`` is true,
  then stamps a final variable from the child's output via
  ``vars.steps.<ref>.<child var>``.

The script triggers the parent, answers the Manual Input WRONG a few times (the
loop keeps re-popping the prompt), then RIGHT once (the loop exits), and prints
the proof: the parent's ``StampResult`` step finishes (a broken child-output
reference would have failed it) and the child ran once per loop turn.

Requires the compiler extra::

    pip install "pyfsr[playbooks]"

Usage::

    python examples/do_until_validation_loop.py \
        --host fortisoar.example.com --user csadmin --password '...' --port 13002

    # answer with your own values
    python examples/do_until_validation_loop.py --wrong 12 345 7 --right 654321

    # leave the collection deployed afterwards
    python examples/do_until_validation_loop.py --keep

Environment variables (used when the matching flag is omitted):
    FSR_BASE_URL / FSR_HOST       appliance host or URL
    FSR_USERNAME / FSR_PASSWORD   credential auth
    FSR_API_KEY                   API-key auth (alternative to user/password)
    FSR_PORT                      optional port override

A note on persistence: FortiSOAR only stores runtime jinja vars /
``set_variable`` values in the retrievable run record when global workflow debug
logging is enabled; with it off (the default) you cannot read the literal
stamped value back from the finished run. The verifiable proof is behavioral --
``StampResult`` finishing and the child-run (loop-turn) count -- and holds
regardless of the debug-logging setting.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from pyfsr import FortiSOAR

YAML_PATH = Path(__file__).parent / "playbooks" / "do_until_validation_demo.yaml"
PARENT_NAME = "Loop Until Six Digits"
CHILD_NAME = "Validate Six Digit Number"
# A pending manual input's `title` is the prompt's SCHEMA title -- the
# manual_input step's `title:` -- not the step name (the step is "AskNumber").
MI_TITLE = "Enter a six digit number"


# --------------------------------------------------------------------------- #
# client / user helpers
# --------------------------------------------------------------------------- #
def build_client(args) -> FortiSOAR:
    host = args.host or os.environ.get("FSR_BASE_URL") or os.environ.get("FSR_HOST")
    if not host:
        raise SystemExit("set --host or FSR_BASE_URL to run this example")
    api_key = args.api_key or os.environ.get("FSR_API_KEY")
    if api_key:
        auth: str | tuple[str, str] = api_key
    else:
        user = args.user or os.environ.get("FSR_USERNAME", "csadmin")
        password = args.password or os.environ.get("FSR_PASSWORD")
        if not password:
            raise SystemExit("set --password / FSR_PASSWORD (or --api-key / FSR_API_KEY)")
        auth = (user, password)
    return FortiSOAR(
        host,
        auth=auth,
        verify_ssl=False,
        suppress_insecure_warnings=True,
        port=args.port,
    )


# --------------------------------------------------------------------------- #
# manual-input plumbing
# --------------------------------------------------------------------------- #
def pending_demo_inputs(client: FortiSOAR) -> list:
    """Our demo's pending manual inputs, newest first."""
    rows = client.manual_input.list(assigned_to="all")
    return [mi for mi in rows if (mi.title or "") == MI_TITLE]


def wait_for_new_input(client: FortiSOAR, handled: set[int], timeout: float = 90):
    """Block until a demo manual input we have not handled yet appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for mi in pending_demo_inputs(client):
            if mi.id not in handled:
                return mi
        time.sleep(2)
    return None


# --------------------------------------------------------------------------- #
# run introspection
# --------------------------------------------------------------------------- #
def _recent_runs(client: FortiSOAR, limit: int = 50) -> list:
    resp = client.get("/api/wf/api/workflows/", params={"limit": limit, "ordering": "-id", "format": "json"})
    return resp.get("hydra:member") or resp.get("results") or []


def find_parent_run(client: FortiSOAR) -> str | None:
    """Pk of the newest top-level parent run (``parent_wf`` is null).

    The trigger task_id resolves to a *child* run, and the do-until ``retry:``
    loop re-launches the reference each turn, so the parent is located by name +
    being top-level (no ``parent_wf``)."""
    for m in _recent_runs(client):
        if m.get("name") == PARENT_NAME and not m.get("parent_wf"):
            pk = (m.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
            if pk.isdigit():
                return pk
    return None


def count_child_runs(client: FortiSOAR, parent_pk: str) -> int:
    """How many times the child playbook ran (one per loop turn).

    Scoped to the parent via ``parent_wf`` rather than counting every recent run
    named like the child -- synchronous (``apply_async: false``) reference
    children ARE ``parent_wf``-linked, so ``child_runs`` returns exactly this
    parent's loop turns and doesn't pick up unrelated runs."""
    return len(client.playbooks.child_runs(parent_pk))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=None, help="appliance host or URL (FSR_BASE_URL)")
    ap.add_argument(
        "--port",
        type=int,
        default=(int(os.environ["FSR_PORT"]) if os.environ.get("FSR_PORT") else None),
        help="port override (omit if the host URL already has one)",
    )
    ap.add_argument("--user", default=None, help="login user (FSR_USERNAME)")
    ap.add_argument("--password", default=None, help="login password (FSR_PASSWORD)")
    ap.add_argument("--api-key", default=None, help="API key (FSR_API_KEY)")
    ap.add_argument(
        "--wrong",
        nargs="*",
        default=["12", "12345", "1234567"],
        help="answers that should FAIL validation (not 6 digits)",
    )
    ap.add_argument("--right", default="654321", help="the valid 6-digit answer")
    ap.add_argument("--keep", action="store_true", help="leave the collection deployed")
    args = ap.parse_args()

    client = build_client(args)

    # --- 1. deploy -------------------------------------------------------- #
    print("\n=== deploy ===")
    created = client.workflow_collections.import_from_yaml(str(YAML_PATH), replace=True)
    coll_uuid = created[0]["uuid"]
    print(f"deployed collection {created[0].get('name')!r} ({coll_uuid})")

    try:
        # --- 2. trigger the parent --------------------------------------- #
        print("\n=== trigger ===")
        # ignore any stale demo inputs left over from a previous run
        handled = {mi.id for mi in pending_demo_inputs(client)}
        resp = client.playbooks.trigger(PARENT_NAME)
        task_id = resp["task_id"] if isinstance(resp, dict) else resp.task_id
        print(f"triggered {PARENT_NAME!r}, task_id={task_id}")

        # --- 3. answer WRONG a few times --------------------------------- #
        print("\n=== wrong answers (loop should keep re-prompting) ===")
        for i, bad in enumerate(args.wrong, 1):
            mi = wait_for_new_input(client, handled)
            if mi is None:
                raise SystemExit(f"no manual input appeared for wrong-answer #{i}")
            handled.add(mi.id)
            print(f"  attempt {i}: prompt id={mi.id} -> answering {bad!r} ({len(str(bad))} digits, expect INVALID)")
            # answer() finds the input, resolves the run id / step_iri / user,
            # maps the scalar to the prompt's single variable, and resumes.
            client.manual_input.answer(int(bad), input_id=mi.id)

        # --- 4. answer RIGHT --------------------------------------------- #
        print("\n=== correct answer (loop should exit) ===")
        mi = wait_for_new_input(client, handled)
        if mi is None:
            raise SystemExit("no manual input appeared for the correct answer")
        handled.add(mi.id)
        print(f"  prompt id={mi.id} -> answering {args.right!r} ({len(str(args.right))} digits, expect VALID)")
        client.manual_input.answer(int(args.right), input_id=mi.id)

        # --- 5. wait for the parent run + show the proof ----------------- #
        print("\n=== wait for parent run to finish ===")
        run = client.playbooks.wait(task_id, timeout=120)
        print(f"run status: {run.get('status')}")

        leftover = wait_for_new_input(client, handled, timeout=8)
        if leftover is not None:
            print(f"  WARNING: an extra prompt appeared (id={leftover.id}) -- loop did not exit")
        else:
            print("  no further prompt -- loop exited on the valid answer")

        # FortiSOAR only records runtime jinja vars / set_variable values in the
        # run record when global workflow debug logging is enabled; with it off
        # (the default) we can't read the literal stamped value back. The
        # verifiable proof: StampResult finished (it references
        # vars.steps.CallChild.* -- a broken ref would FAIL it) and the child ran
        # once per loop turn.
        print("\n=== proof: parent steps + loop count ===")
        parent_pk = find_parent_run(client)
        if parent_pk is not None:
            parent = client.playbooks.get_execution(parent_pk, step_detail=True)
            steps = {s.get("name"): s for s in (parent.get("steps") or [])}
            for name in ("CallChild", "StampResult"):
                print(f"  {name}: {(steps.get(name) or {}).get('status')}")
            ok = (steps.get("StampResult") or {}).get("status") == "finished"
            print(f"  -> parent {'consumed child output OK' if ok else 'did NOT finish StampResult'}")
            child_runs = count_child_runs(client, parent_pk)
            expected = len(args.wrong) + 1
            print(f"  child runs (loop turns): {child_runs}  (expected {expected}: {len(args.wrong)} wrong + 1 valid)")

    finally:
        if args.keep:
            print(f"\nkeeping collection {coll_uuid}")
        else:
            client.workflow_collections.delete(coll_uuid)
            print(f"\ndeleted collection {coll_uuid}")


if __name__ == "__main__":
    main()
