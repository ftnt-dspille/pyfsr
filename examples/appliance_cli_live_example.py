#!/usr/bin/env python
"""Live example demonstrating all ``pyfsr appliance`` CLI commands.

This is a working script that validates the appliance CLI on both local and
remote boxes. Use this to:

1. Test connectivity and credentials.
2. Understand the API for each command family (service/mq/logs/db).
3. Troubleshoot a live FortiSOAR appliance.

Run locally on an appliance:
    python examples/appliance_cli_live_example.py

Run remotely against 10.0.0.1:
    python examples/appliance_cli_live_example.py --host 10.0.0.1 --user csadmin

Credentials are read from the CLI args, env (PYFSR_APPLIANCE_*), or prompted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to path so we can import pyfsr locally
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pyfsr.cli.appliance import logs as logs_cmds
from pyfsr.cli.appliance import mq as mq_cmds
from pyfsr.cli.appliance import service as service_cmds
from pyfsr.cli.appliance.facts import Facts
from pyfsr.cli.appliance.transport import TransportError, make_transport


def _section(title: str):
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def _demo_service(facts: Facts):
    """Demonstrate service commands: status, liveness, listeners."""
    _section("SERVICE DIAGNOSTICS")

    # Status
    print("1. Service status (csadm services --status):")
    try:
        status = service_cmds.status(facts.transport)
        print(status)
    except Exception as e:
        print(f"   ERROR: {e}")

    # Liveness
    print("\n2. Service liveness probe (detects wedged services):")
    try:
        results = service_cmds.liveness(facts.transport)
        for r in results:
            status_icon = "✓" if "ok" in r.verdict else "⚠"
            print(f"   {status_icon} {r.label:30s} | {r.method} {r.path}")
            print(f"      → {r.verdict} (HTTP {r.code})")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Listeners
    print("\n3. Listening TCP ports (ss -tlnp):")
    try:
        headers, rows = service_cmds.listeners(facts.transport)
        print(f"   {headers[0]:30s} | {headers[1]}")
        print(f"   {'-' * 30}-+-{'-' * 40}")
        for row in rows[:10]:  # limit output
            print(f"   {row[0]:30s} | {row[1]}")
        if len(rows) > 10:
            print(f"   ... and {len(rows) - 10} more")
    except Exception as e:
        print(f"   ERROR: {e}")


def _demo_mq(facts: Facts):
    """Demonstrate MQ commands: status, queues, consumers, vhosts, permissions."""
    _section("RABBITMQ DIAGNOSTICS")

    # Status
    print("1. RabbitMQ status:")
    try:
        status = mq_cmds.status(facts.transport)
        # Show first few lines
        lines = status.split("\n")[:5]
        for line in lines:
            if line.strip():
                print(f"   {line}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Queues
    print("\n2. Queue health (messages, consumers, flags):")
    try:
        headers, rows = mq_cmds.queues(facts.transport)
        print(f"   {headers[0]:25s} | {headers[1]:10s} | {headers[2]:10s} | {headers[3]}")
        print(f"   {'-' * 25}-+-{'-' * 10}-+-{'-' * 10}-+-{'-' * 40}")
        for row in rows:
            flag_icon = "⚠" if row[3] else " "
            print(f"   {flag_icon} {row[0]:23s} | {row[1]:10s} | {row[2]:10s} | {row[3]}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Consumers
    print("\n3. Active consumers (queue subscriptions):")
    try:
        headers, rows = mq_cmds.consumers(facts.transport)
        for row in rows[:5]:  # limit output
            print(f"   {row[0]}")
        if len(rows) > 5:
            print(f"   ... and {len(rows) - 5} more")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Vhosts
    print("\n4. Virtual hosts:")
    try:
        headers, rows = mq_cmds.vhosts(facts.transport)
        for row in rows:
            print(f"   {row[0]}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Permissions
    print("\n5. Permissions (user → configure/write/read):")
    try:
        headers, rows = mq_cmds.permissions(facts.transport)
        print(f"   {headers[0]:15s} | {headers[1]:5s} | {headers[2]:5s} | {headers[3]:5s}")
        print(f"   {'-' * 15}-+-{'-' * 5}-+-{'-' * 5}-+-{'-' * 5}")
        for row in rows:
            print(f"   {row[0]:15s} | {row[1]:5s} | {row[2]:5s} | {row[3]:5s}")
    except Exception as e:
        print(f"   ERROR: {e}")


def _demo_logs(facts: Facts):
    """Demonstrate log commands: tail, scan."""
    _section("APPLIANCE LOGS")

    # Tail auth logs
    print("1. Auth service logs (last 5 lines):")
    try:
        tail_output = logs_cmds.tail(facts.transport, "auth", lines=5)
        for line in tail_output.strip().split("\n")[-5:]:
            print(f"   {line}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Tail nginx errors
    print("\n2. Nginx error log (last 3 lines):")
    try:
        tail_output = logs_cmds.tail(facts.transport, "nginx", lines=3)
        for line in tail_output.strip().split("\n")[-3:]:
            if line.strip():
                print(f"   {line}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Error scan (last 30 min)
    print("\n3. Recent errors (last 30 minutes via journalctl):")
    try:
        scan_output = logs_cmds.scan(facts.transport, minutes=30)
        if "no journal errors" in scan_output.lower():
            print(f"   ✓ {scan_output}")
        else:
            for line in scan_output.split("\n")[:20]:  # limit output
                print(f"   {line}")
            if len(scan_output.split("\n")) > 20:
                print("   ... (truncated)")
    except Exception as e:
        print(f"   ERROR: {e}")


def _demo_db(facts: Facts):
    """Demonstrate database facts: device UUID, content DB discovery, version."""
    _section("DATABASE & APPLIANCE FACTS")

    # Device UUID
    print("1. Device UUID (also the DB password):")
    try:
        uuid = facts.device_uuid()
        print(f"   {uuid[:16]}... (masked for security)")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Content DB
    print("\n2. Content database discovery:")
    try:
        content_db = facts.content_db()
        print(f"   Found: {content_db}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Version
    print("\n3. FortiSOAR version:")
    try:
        version = facts.fsr_version()
        if version:
            print(f"   {version}")
        else:
            print("   (could not determine)")
    except Exception as e:
        print(f"   ERROR: {e}")


def _demo_help():
    """Print usage help."""
    _section("APPLIANCE CLI HELP")
    print("""
The ``pyfsr appliance`` CLI provides diagnostic and admin commands for FortiSOAR:

SERVICE (systemd / cyops services):
  status [--name X]    - Show all services or a specific service
  liveness             - Probe endpoints to detect *active but wedged* services
  restart --name X     - Restart a service (gated by --yes)
  listeners            - Show listening TCP ports with owning processes

MQ (RabbitMQ diagnostics):
  status               - Show RabbitMQ broker status
  queues               - List queues with message depth + consumer count
  consumers            - List active consumers
  vhosts               - List virtual hosts
  permissions          - List user permissions per vhost

LOGS (log tail / error scan):
  tail <service|path>  - Tail a service log or raw path
  scan [--minutes N]   - Scan recent errors via journalctl

DATABASE (module operations):
  query [--db X]       - Execute a read-only SQL query
  list [--role X]      - List databases
  find [--role X] <mod>- Find tables belonging to a module
  drop --yes [--role]  - Delete a module's tables (orphans included)

TRANSPORT (location selection):
  Local appliance      - Detected via /opt/cyops marker
  SSH remote           - Via --host / PYFSR_APPLIANCE_HOST
  Password fallback    - PYFSR_APPLIANCE_PASSWORD env

Try:
  pyfsr appliance service status
  pyfsr appliance mq queues
  pyfsr appliance logs tail auth
  pyfsr appliance db list
""")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        help="Remote appliance IP/hostname (ssh-based); auto-detect if not given",
    )
    parser.add_argument(
        "--user",
        default="csadmin",
        help="SSH user (default: csadmin)",
    )
    parser.add_argument(
        "--password",
        help="SSH/sudo password (or set PYFSR_APPLIANCE_PASSWORD)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=22,
        help="SSH port (default: 22)",
    )
    parser.add_argument(
        "--key",
        help="SSH key path",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSH host-key verification (for ephemeral lab boxes only)",
    )

    args = parser.parse_args()

    print(__doc__)

    # Create transport
    print("Connecting to FortiSOAR appliance...")
    try:
        transport = make_transport(
            host=args.host,
            user=args.user,
            password=args.password,
            port=args.port,
            key_path=args.key,
            insecure_skip_host_key_check=args.insecure,
        )
        print(f"✓ Connected to {transport.target}\n")
    except TransportError as e:
        print(f"✗ Connection failed: {e}")
        _demo_help()
        sys.exit(1)

    # Create facts (lazy-loaded appliance metadata)
    facts = Facts(transport)

    # Run demos
    try:
        _demo_service(facts)
        _demo_mq(facts)
        _demo_logs(facts)
        _demo_db(facts)
        _demo_help()
        print("\n✓ All demonstrations completed.\n")
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
