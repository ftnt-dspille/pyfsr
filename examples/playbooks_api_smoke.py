"""Live smoke test for the whole ``client.playbooks`` (``PlaybooksAPI``) surface.

Exercises every method on the playbook API against a real FortiSOAR appliance and
checks that each returns the expected (now pydantic-typed) shape — ``RunSummary``,
``RunEnv``, ``RunFailure``, ``TriggerResponse``, ``Workflow``, ``WorkflowRun`` —
while staying dict-compatible. Think of it as an end-to-end "do the APIs work?"
harness, not a unit test (those live in ``tests/unit/test_playbooks.py``).

It runs in tiers so it's safe to point at any box:

- **read-only** (always): definition reads, run-history reads, counts, jinja
  render, manual-input listing. These never mutate the appliance.
- **--write**: a full definition lifecycle — create a throwaway playbook in a
  throwaway collection, update it, clone it, then hard-delete all of them. Always
  cleans up after itself (even on error).
- **--trigger PLAYBOOK**: manually trigger a real manual-execute playbook by name
  or uuid and wait for it to finish (then read its run env / failure / steps).

Each call is wrapped so one failure doesn't abort the run; a PASS/FAIL/SKIP table
prints at the end and the process exits nonzero if anything hard-failed.

Connection comes from ``FSR_*`` env (see ``pyfsr.config.EnvConfig``) or flags::

    FSR_BASE_URL=https://fortisoar.example.com FSR_USERNAME=csadmin FSR_PASSWORD='$FSR_PASSWORD' \
        FSR_VERIFY_SSL=false python examples/playbooks_api_smoke.py --write

    python examples/playbooks_api_smoke.py \
        --server fortisoar.example.com --username csadmin --password "$FSR_PASSWORD" --no-verify-ssl \
        --write --trigger "My Manual Playbook"
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

from pyfsr import FortiSOAR
from pyfsr.config import EnvConfig
from pyfsr.models import (
    RunEnv,
    RunFailure,
    RunSummary,
    TriggerResponse,
    Workflow,
)


# --------------------------------------------------------------------- runner
class Runner:
    """Records the outcome of each named API check and prints a summary."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (name, status, detail)

    def check(self, name: str, fn: Callable[[], Any], *, skip: str | None = None) -> Any:
        if skip:
            self.rows.append((name, "SKIP", skip))
            print(f"  · SKIP {name}: {skip}")
            return None
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - the whole point is to report, not raise
            detail = f"{type(e).__name__}: {e}"
            self.rows.append((name, "FAIL", detail))
            print(f"  ✗ FAIL {name}: {detail}")
            if "-v" in sys.argv or "--verbose" in sys.argv:
                traceback.print_exc()
            return None
        summary = _describe(result)
        self.rows.append((name, "PASS", summary))
        print(f"  ✓ PASS {name}: {summary}")
        return result

    def summary(self) -> int:
        passed = sum(1 for _, s, _ in self.rows if s == "PASS")
        failed = sum(1 for _, s, _ in self.rows if s == "FAIL")
        skipped = sum(1 for _, s, _ in self.rows if s == "SKIP")
        print("\n" + "=" * 60)
        print(f"playbooks API smoke: {passed} passed, {failed} failed, {skipped} skipped")
        if failed:
            print("failures:")
            for name, status, detail in self.rows:
                if status == "FAIL":
                    print(f"  - {name}: {detail}")
        return 1 if failed else 0


def _describe(result: Any) -> str:
    """One-line shape summary of a return value, naming its type."""
    tname = type(result).__name__
    if isinstance(result, list):
        head = type(result[0]).__name__ if result else "?"
        return f"list[{head}] (n={len(result)})"
    if isinstance(result, (RunSummary, RunFailure)):
        return f"{tname}(status={result.get('status')!r}, pk={result.get('pk')!r})"
    if isinstance(result, RunEnv):
        return f"RunEnv(status={result.status!r}, steps={list(result.steps)})"
    if isinstance(result, TriggerResponse):
        return f"TriggerResponse(task_id={result.task_id!r})"
    if isinstance(result, Workflow):
        return f"Workflow(name={result.get('name')!r}, uuid={result.get('uuid')!r})"
    if isinstance(result, dict):
        return f"dict(keys={list(result)[:6]})"
    return f"{tname}={result!r}"[:80]


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ----------------------------------------------------------------- client
def build_client(args: argparse.Namespace) -> FortiSOAR:
    overrides: dict[str, Any] = {}
    if args.server:
        overrides["base_url"] = args.server
    if args.token:
        overrides["auth"] = args.token
    elif args.username and args.password:
        overrides["auth"] = (args.username, args.password)
    if args.port is not None:
        overrides["port"] = args.port
    if args.no_verify_ssl:
        overrides["verify_ssl"] = False
        overrides["suppress_insecure_warnings"] = True
    if "base_url" in overrides and "auth" in overrides:
        return FortiSOAR(**overrides)
    return EnvConfig.from_env().client(**overrides)


# --------------------------------------------------------------- read checks
def run_read_checks(r: Runner, pb) -> dict[str, Any]:
    """Read-only methods. Returns context (a sample workflow + run) for later tiers."""
    print("\n[read-only] definition reads")
    defs = r.check("list", lambda: pb.list(limit=5))
    sample_uuid = None
    if defs:
        sample_uuid = defs[0].get("uuid")
    r.check(
        "get_definition",
        lambda: _typed(pb.get_definition(sample_uuid), Workflow),
        skip=None if sample_uuid else "no playbook definitions on this box",
    )
    r.check("count", lambda: pb.count())
    r.check("query", lambda: pb.query({"logic": "AND", "filters": [], "limit": 3}))

    print("\n[read-only] run-history reads")
    runs = r.check("execution_history", lambda: _typed_list(pb.execution_history(limit=5), RunSummary))
    r.check("last_run", lambda: pb.last_run() if runs else None, skip=None if runs else "no runs to summarize")
    sample_pk = None
    if runs:
        sample_pk = runs[0].pk
    r.check("search_executions", lambda: _typed_list(pb.search_executions(limit=5), RunSummary))
    r.check(
        "get_execution",
        lambda: pb.get_execution(sample_pk),
        skip=None if sample_pk else "no run pk available",
    )
    r.check(
        "run_env",
        lambda: _typed(pb.run_env(sample_pk), RunEnv),
        skip=None if sample_pk else "no run pk available",
    )
    sample_task = runs[0].task_id if runs else None
    r.check(
        "historical_steps",
        lambda: pb.historical_steps(sample_task, limit=20),
        skip=None if sample_task else "no run task_id available",
    )

    print("\n[read-only] log + manual-input surface")
    r.check("log_list", lambda: pb.log_list(limit=3))
    r.check("query_logs", lambda: pb.query_logs(limit=3))
    r.check("manual_inputs", lambda: pb.manual_inputs())
    r.check(
        "render_jinja",
        lambda: _expect_str(pb.render_jinja("{{ 1 + 1 }}", {})),
    )
    return {"sample_uuid": sample_uuid, "sample_pk": sample_pk}


def _typed(result: Any, cls: type) -> Any:
    _expect(isinstance(result, cls), f"expected {cls.__name__}, got {type(result).__name__}")
    return result


def _typed_list(result: list[Any], cls: type) -> list[Any]:
    if result:
        _expect(isinstance(result[0], cls), f"expected list[{cls.__name__}], got {type(result[0]).__name__}")
    return result


def _expect_str(result: Any) -> str:
    _expect(isinstance(result, str), f"render_jinja should return str, got {type(result).__name__}")
    return result


# -------------------------------------------------------------- write checks
def run_write_checks(r: Runner, pb) -> None:
    """Full definition lifecycle in a throwaway collection. Always cleans up."""
    print("\n[--write] definition lifecycle (create / update / clone / delete)")
    stamp = str(int(time.time()))
    coll_name = f"pyfsr-smoke-{stamp}"
    pb_name = f"pyfsr-smoke-pb-{stamp}"
    clone_name = f"{pb_name}-clone"
    created_coll = None
    created_pb = None
    created_clone = None
    client = pb.client
    try:
        created_coll = r.check(
            "workflow_collections.create_collection",
            lambda: client.workflow_collections.create_collection(coll_name),
        )
        coll_uuid = created_coll.get("uuid") if created_coll else None
        if not coll_uuid:
            r.check("create_playbook", lambda: None, skip="collection create failed")
            return

        created_pb = r.check("create_playbook", lambda: pb.create_playbook(pb_name, coll_uuid, is_active=False))
        pb_uuid = created_pb.get("uuid") if created_pb else None
        if pb_uuid:
            r.check("update", lambda: pb.update(pb_uuid, description="smoke-test edit"))
            created_clone = r.check("clone", lambda: pb.clone(pb_uuid, clone_name))
    finally:
        print("\n[--write] cleanup")
        if created_clone and created_clone.get("uuid"):
            r.check("delete (clone)", lambda: pb.delete(created_clone["uuid"]))
        if created_pb and created_pb.get("uuid"):
            r.check("delete (playbook)", lambda: pb.delete(created_pb["uuid"]))
        if created_coll and created_coll.get("uuid"):
            r.check(
                "workflow_collections.delete",
                lambda: client.workflow_collections.delete(created_coll["uuid"]),
            )


# ------------------------------------------------------------ trigger checks
def run_trigger_checks(r: Runner, pb, playbook_ref: str) -> None:
    print(f"\n[--trigger] manual trigger + wait: {playbook_ref!r}")
    resp = r.check(
        "trigger",
        lambda: _typed(pb.trigger(playbook_ref), TriggerResponse),
    )
    task_id = resp.task_id if isinstance(resp, TriggerResponse) else None
    r.check(
        "wait",
        lambda: _typed(pb.wait(task_id, timeout=120, interval=3), RunSummary),
        skip=None if task_id else "trigger returned no task_id",
    )
    r.check(
        "why_failed",
        lambda: _maybe(pb.why_failed(playbook=playbook_ref), RunFailure),
    )


def _maybe(result: Any, cls: type) -> Any:
    if result is not None:
        _expect(isinstance(result, cls), f"expected {cls.__name__} or None, got {type(result).__name__}")
    return result


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", help="appliance host or URL (else FSR_BASE_URL)")
    p.add_argument("--token", "--api-key", dest="token", help="API key (else FSR_API_KEY)")
    p.add_argument("--username", help="login user (else FSR_USERNAME)")
    p.add_argument("--password", help="login password (else FSR_PASSWORD)")
    p.add_argument("--port", type=int, help="port override (else FSR_PORT)")
    p.add_argument("--no-verify-ssl", action="store_true", help="disable TLS verification")
    p.add_argument("--write", action="store_true", help="run the create/update/clone/delete lifecycle tier")
    p.add_argument("--trigger", metavar="PLAYBOOK", help="manually trigger this playbook (name or uuid) and wait")
    p.add_argument("-v", "--verbose", action="store_true", help="print tracebacks on failure")
    args = p.parse_args(argv)

    try:
        client = build_client(args)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not build client — {e}", file=sys.stderr)
        return 2

    r = Runner()
    pb = client.playbooks
    run_read_checks(r, pb)
    if args.write:
        run_write_checks(r, pb)
    if args.trigger:
        run_trigger_checks(r, pb, args.trigger)
    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
