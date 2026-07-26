# AGENTS.md — pyfsr

## Jinja filter lookup (before grepping)

When you need a FortiSOAR Jinja filter, **query the reference DB before
grepping the corpus**. The `fsr_reference.db` has 170+ filters, 15 globals,
39 tests — all with full signatures, curated docs, and 1,690 real usage
examples from 1,669 playbooks.

```bash
# Find a filter by name (shows signature + curated doc + examples)
pyfsr jinja find picklist

# Full-text search across names, docs, and usage examples
pyfsr jinja search "query body"

# List all filters (or --kind globals / --kind tests)
pyfsr jinja list

# Real-world usage examples for a specific filter
pyfsr jinja examples picklist

# Common Jinja patterns from the idioms reference
pyfsr jinja idioms
```

From Python:
```python
from pyfsr.cli.jinja import _find_db, _connect
db = _find_db()
conn = _connect(db)
row = conn.execute("SELECT * FROM jinja_macros WHERE name = 'picklist'").fetchone()
print(row["curated_doc"])
```

The full DB (65MB) lives at:
`/Users/dylanspille/PycharmProjects/fsr-playbook-framework/data/fsr_reference.db`

## Picklist IRI resolution

When a playbook hardcodes picklist IRIs from a different box, use the
picklist helpers to resolve or emit dynamic Jinja:

```python
# Resolve a friendly value to its IRI on this box
iri = client.picklists.resolve("Indicator Extracted", picklist="AlertState")

# Reverse-resolve an IRI back to its friendly name (for patching exports)
info = client.picklists.reverse_resolve("/api/3/picklists/501d0562-...")
# -> {"picklist": "AlertState", "itemValue": "Indicator Extracted", "iri": "..."}

# Build a Jinja picklist expression that resolves dynamically at runtime
expr = client.picklists.jinja_picklist_expr("AlertState", "Indicator Extracted")
# -> '{{ "AlertState" | picklist("Indicator Extracted", "@id") }}'
```

## Step timing + run inspection

```python
# Trigger + poll + return typed result with timing + children
result = client.playbooks.run_and_wait("My Playbook", timeout=60)
print(result.status)           # 'finished' / 'failed' / ...
print(result.slow_steps)       # steps > 30s
for s in result.steps:
    print(f"  {s.name:40} {s.duration_ms}ms  {'*** SLOW' if s.is_slow else ''}")

# Sorted step timeline with timing
for s in client.playbooks.step_timeline(run_pk):
    print(f"  {s.name:40} {s.status:10} {s.duration_ms or 0:6}ms")

# Full audit lifecycle of a record (what changed, by which playbook, when)
life = client.audit.lifecycle(alert_uuid, entity_type="alerts")
print(life.summary())
for e in life.entries:
    print(f"  [{e.timestamp_iso}] {e.kind:10} {e.operation:12} {e.playbook_name or ''}")

# What else was happening to the record during a specific run?
ctx = client.audit.execution_context(run_pk, window_seconds=120)
print(ctx.summary())
for ch in ctx.concurrent_changes:
    print(f"  [{ch.timestamp_iso}] {ch.operation:12} by={ch.user or '?':10} pb={ch.playbook_name or ''}")
print(f"  other playbooks: {ctx.other_playbooks}")
```

## Audit log (per-record change history)

```python
# All audit events for a record
for item in client.audit.all_activities(entity_uuid=alert_uuid):
    print(f"  {item['operation']:12} by={item.get('user','?'):10} {item.get('playbookName','')}")

# Count
total = client.audit.count(entity_uuid=alert_uuid)["total"]

# Module-level operations (Create/Update/Link/Unlink/Comment/Trigger/...)
ops = client.audit.operations(operation_type="module_detail")
```
