#!/usr/bin/env python
"""Offline test demonstration of ``pyfsr appliance`` commands.

This script runs all appliance CLI commands against a FakeTransport
(mocked appliance), demonstrating:

  1. Service status, liveness, and restart
  2. RabbitMQ queue health, consumers, permissions
  3. Log tail and error scanning
  4. Database discovery and query execution
  5. Proper error handling and permission gates

Run with:
    python examples/appliance_cli_test_demo.py

This is useful for understanding the CLI API without needing a live appliance.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pyfsr.cli.appliance import db as db_cmds
from pyfsr.cli.appliance import logs as logs_cmds
from pyfsr.cli.appliance import mq as mq_cmds
from pyfsr.cli.appliance import service as service_cmds
from pyfsr.cli.appliance.facts import Facts
from pyfsr.cli.appliance.transport import CommandResult, Transport


class DemoTransport(Transport):
    """Minimal fake transport for demonstration."""

    target = "demo-appliance"
    US = "\x1f"  # Unit separator (field delimiter) used by psql

    def run(self, argv, *, input_text=None, env=None, timeout=60.0, sudo=False):
        # Dispatch based on command
        if argv[:3] == ["csadm", "license", "--get-device-uuid"]:
            return CommandResult(argv, 0, "Device UUID: 0123456789abcdef0123456789abcdef\n", "")

        if argv[:3] == ["csadm", "services", "--status"]:
            return CommandResult(
                argv,
                0,
                "cyops-auth\tactive\t0\t0\ncyops-api\tactive\t0\t0\ncyops-workflow\tactive\t0\t0\n",
                "",
            )

        if argv[0] == "curl":
            # Simulate healthy endpoints
            return CommandResult(argv, 0, "200", "")

        if argv[0] == "ss":
            return CommandResult(
                argv,
                0,
                (
                    'LISTEN  0  128  *:443  *:*  users:(("nginx",pid=1234,fd=5))\n'
                    'LISTEN  0  128  *:80  *:*  users:(("nginx",pid=1234,fd=6))\n'
                    'LISTEN  0  128  *:5672  *:*  users:(("rabbitmq",pid=2345,fd=7))\n'
                ),
                "",
            )

        if argv[0] == "rabbitmqctl":
            if "list_queues" in argv:
                return CommandResult(
                    argv,
                    0,
                    "task_queue\t42\t1\nnotifier_queue\t5\t2\ndefault\t0\t0\n",
                    "",
                )
            if "list_consumers" in argv:
                return CommandResult(argv, 0, "task_queue\t<rabbit@box.1.123>\n", "")
            if "list_vhosts" in argv:
                return CommandResult(argv, 0, "/\n/internal\n", "")
            if "list_permissions" in argv:
                return CommandResult(argv, 0, "guest\t.*\t.*\t.*\nadmin\t.*\t.*\t.*\n", "")
            return CommandResult(argv, 0, "Status\tOK\n", "")

        if argv[0] == "journalctl":
            return CommandResult(argv, 0, "No entries\n", "")

        if argv[0] == "tail":
            return CommandResult(argv, 0, "[INFO] 2026-06-20 12:00:00 service started\n", "")

        if argv[0] == "psql":
            # Handle database queries with proper unit separator format
            sql = argv[-1].lower()
            if "from pg_database" in sql and "pg_size_pretty" in sql:
                # list_databases query: needs name and size columns
                return CommandResult(
                    argv,
                    0,
                    f"venom{self.US}2 GB\ndasdata{self.US}500 MB\ngateway{self.US}100 MB\n",
                    "",
                )
            if "from pg_database" in sql:
                # list databases without sizes
                return CommandResult(argv, 0, "venom\ndasdata\ngateway\n", "")
            if "information_schema.tables" in sql and "model_metadatas" in sql:
                return CommandResult(argv, 0, "1\n", "")
            if "from pg_tables" in sql and "widgets" in sql:
                return CommandResult(argv, 0, "widgets\nwidgets_alerts\nwidgets_team\n", "")
            return CommandResult(argv, 0, "1\n", "")

        if argv[0] == "rpm":
            return CommandResult(argv, 0, "7.6.5", "")

        return CommandResult(argv, 0, "", "")


def _section(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def demo_service(transport):
    _section("SERVICE COMMANDS")

    print("1. Service status:")
    status = service_cmds.status(transport)
    for line in status.split("\n")[:3]:
        if line:
            print(f"   {line}")

    print("\n2. Service liveness (checking endpoints):")
    results = service_cmds.liveness(transport)
    for r in results:
        print(f"   • {r.label:30s} → {r.verdict}")

    print("\n3. Listening ports:")
    headers, rows = service_cmds.listeners(transport)
    print(f"   {headers[0]:30s} | {headers[1]}")
    for row in rows[:2]:
        print(f"   {row[0]:30s} | {row[1][:40]}")


def demo_mq(transport):
    _section("RABBITMQ COMMANDS")

    print("1. RabbitMQ status:")
    status = mq_cmds.status(transport)
    print(f"   {status.strip()}")

    print("\n2. Queue health:")
    headers, rows = mq_cmds.queues(transport)
    print(f"   {headers[0]:20s} | {headers[1]:10s} | {headers[2]:10s} | {headers[3]}")
    for row in rows:
        flag = f"  {row[3]}" if row[3] else ""
        print(f"   {row[0]:20s} | {row[1]:10s} | {row[2]:10s} |{flag}")

    print("\n3. Consumers:")
    headers, rows = mq_cmds.consumers(transport)
    for row in rows[:2]:
        print(f"   {row[0]}")

    print("\n4. Vhosts:")
    headers, rows = mq_cmds.vhosts(transport)
    for row in rows:
        print(f"   {row[0]}")

    print("\n5. Permissions:")
    headers, rows = mq_cmds.permissions(transport)
    print(f"   {headers[0]:10s} | {headers[1]:5s} | {headers[2]:5s} | {headers[3]:5s}")
    for row in rows:
        print(f"   {row[0]:10s} | {row[1]:5s} | {row[2]:5s} | {row[3]:5s}")


def demo_logs(transport):
    _section("LOG COMMANDS")

    print("1. Tail auth log (last 5 lines):")
    output = logs_cmds.tail(transport, "auth", lines=5)
    for line in output.split("\n")[:3]:
        if line:
            print(f"   {line}")

    print("\n2. Scan errors (last 30 min):")
    output = logs_cmds.scan(transport, minutes=30)
    print(f"   {output.strip()}")


def demo_db(transport):
    _section("DATABASE COMMANDS")

    facts = Facts(transport)

    print("1. Device UUID:")
    uuid = facts.device_uuid()
    print(f"   {uuid[:16]}... (masked)")

    print("\n2. Content database:")
    content_db = facts.content_db()
    print(f"   {content_db}")

    print("\n3. List databases:")
    headers, rows = db_cmds.list_databases(facts)
    print(f"   {headers[0]:20s} | {headers[1]:15s} | {headers[2]}")
    for row in rows[:3]:
        print(f"   {row[0]:20s} | {row[1]:15s} | {row[2]}")

    print("\n4. Find module tables (e.g., 'widgets'):")
    tables = db_cmds.find_module_tables(facts, "widgets")
    for t in tables:
        print(f"   {t}")

    print("\n5. Query read-only SQL:")
    result = db_cmds.query(facts, "SELECT 1 FROM information_schema.tables LIMIT 1")
    print(f"   Query returned {len(result)} row(s)")


def demo_permission_gates():
    _section("PERMISSION GATES")

    facts = Facts(DemoTransport())

    print("1. Restart without --yes (should raise PermissionError):")
    try:
        service_cmds.restart(facts.transport, "cyops-auth", yes=False)
        print("   ✗ Should have raised PermissionError!")
    except PermissionError as e:
        print(f"   ✓ Correctly blocked: {e}")

    print("\n2. Drop tables without --yes (should raise PermissionError):")
    try:
        db_cmds.drop_module_tables(facts, "widgets", yes=False)
        print("   ✗ Should have raised PermissionError!")
    except PermissionError as e:
        print(f"   ✓ Correctly blocked: {e}")

    print("\n3. Exec write without --yes (should raise PermissionError):")
    try:
        db_cmds.exec_write(facts, "DELETE FROM widgets", yes=False)
        print("   ✗ Should have raised PermissionError!")
    except PermissionError as e:
        print(f"   ✓ Correctly blocked: {e}")


def main():
    print(__doc__)

    transport = DemoTransport()
    print(f"\nUsing {transport.target}\n")

    try:
        demo_service(transport)
        demo_mq(transport)
        demo_logs(transport)
        demo_db(transport)
        demo_permission_gates()
        _section("SUMMARY")
        print("✓ All command families demonstrated successfully!")
        print("\nNext steps:")
        print("  1. Run 'python -m pytest tests/unit/test_appliance_cli.py -v' to see full tests")
        print("  2. Run 'python examples/appliance_cli_live_example.py --host <ip>' against a live box")
        print("  3. Use 'pyfsr appliance ...' in the CLI for real operations")
        print()
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
