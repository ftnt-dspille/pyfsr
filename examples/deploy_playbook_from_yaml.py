"""Author a playbook in YAML and deploy it to FortiSOAR with pyfsr.

This is the high-level counterpart to ``create_safe_playbook.py``: instead of
hand-building the workflow/steps/routes JSON, you write the playbook as YAML and
let the optional ``pyfsr[playbooks]`` compiler turn it into the FortiSOAR import
envelope. pyfsr then pushes it through its normal collection-import path.

The script runs in two phases:

1. **Compile (offline, no appliance):** parse + compile the YAML and print what
   would be created. This always runs and needs no credentials.
2. **Deploy (live):** only when ``--deploy`` is passed (or creds are present),
   create the collection on the appliance, read it back, and — unless ``--keep``
   — hard-delete it so the run is safe to repeat.

Requires the compiler extra::

    pip install "pyfsr[playbooks]"

Usage::

    # offline: just compile and show the plan
    python examples/deploy_playbook_from_yaml.py

    # live: compile then deploy (replacing any same-uuid collection)
    python examples/deploy_playbook_from_yaml.py --deploy --replace \
        --host fortisoar.example.com --user csadmin --password '...' --port 13002

Environment variables (used when the matching flag is omitted):
    FSR_BASE_URL / FSR_HOST   appliance host or URL
    FSR_USERNAME / FSR_PASSWORD   credential auth
    FSR_API_KEY               API-key auth (alternative to user/password)
    FSR_PORT                  optional port override
    KEEP_COLLECTION=1         leave the created collection in place
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pyfsr import FortiSOAR
from pyfsr.authoring import compile_playbook_yaml, format_diagnostic

DEFAULT_YAML = Path(__file__).parent / "playbooks" / "yaml_demo.yaml"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _build_client(args) -> FortiSOAR:
    host = args.host or os.environ.get("FSR_BASE_URL") or os.environ.get("FSR_HOST")
    if not host:
        raise SystemExit("set --host or FSR_BASE_URL to deploy")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("yaml", nargs="?", default=str(DEFAULT_YAML), help="playbook YAML file")
    parser.add_argument("--deploy", action="store_true", help="actually create it on the appliance")
    parser.add_argument("--replace", action="store_true", help="hard-delete + recreate if it exists")
    parser.add_argument(
        "--keep",
        action="store_true",
        default=_truthy(os.environ.get("KEEP_COLLECTION")),
        help="leave the created collection in place",
    )
    parser.add_argument("--host", default=None, help="appliance host or URL (FSR_BASE_URL)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FSR_PORT", "443")))
    parser.add_argument("--user", default=None, help="login user (FSR_USERNAME)")
    parser.add_argument("--password", default=None, help="login password (FSR_PASSWORD)")
    parser.add_argument("--api-key", default=None, help="API key (FSR_API_KEY)")
    args = parser.parse_args()

    yaml_text = Path(args.yaml).read_text(encoding="utf-8")

    # --- Phase 1: compile offline (no appliance needed) -----------------
    # compile_playbook_yaml is network-free; you can inspect the result before
    # touching an appliance. (The same compile is also available as
    # client.workflow_collections.compile_yaml once you have a client.)
    result = compile_playbook_yaml(yaml_text)
    print("=== compile ===")
    print("ok                 :", result.ok)
    print("collections        :", result.collection_names)
    print("playbooks          :", result.playbook_names)
    for diag in result.errors:
        print("diagnostic         :", format_diagnostic(diag))
    if not result.ok:
        raise SystemExit("compilation failed — fix the diagnostics above")

    if not args.deploy:
        print("\n(dry run — pass --deploy with credentials to create it on an appliance)")
        return

    # --- Phase 2: deploy to the appliance -------------------------------
    client = _build_client(args)
    print("\n=== deploy ===")
    # One call: compile the YAML and import the resulting collection(s).
    created = client.workflow_collections.import_from_yaml(args.yaml, replace=args.replace)
    for col in created:
        print("created collection :", col.get("name"), col.get("uuid"))

    # Read one back to confirm it round-tripped.
    first_uuid = created[0]["uuid"]
    fetched = client.workflow_collections.get(first_uuid)
    workflows = fetched.get("workflows") or []
    print("fetched workflows  :", [w.get("name") for w in workflows])

    if args.keep:
        print("keeping collection :", first_uuid)
        return
    for col in created:
        client.workflow_collections.delete(col["uuid"])
        print("deleted collection :", col["uuid"])


if __name__ == "__main__":
    main()
