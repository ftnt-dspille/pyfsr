# Querying

{class}`~pyfsr.query.Query` is a fluent builder for FortiSOAR's structured query
endpoint (`POST /api/query/{module}`). Every method returns `self` so calls chain
naturally. Results come back as typed {class}`~pyfsr.pagination.HydraPage` objects
that you can iterate, slice, or introspect.

## Quick start

```python
from pyfsr import FortiSOAR, Query

client = FortiSOAR("https://your-fsr", token="...")

# Fetch the 50 most recent open Critical/High alerts
alerts = client.records("alerts").filter(
    Query()
    .in_("severity.itemValue", ["Critical", "High"])
    .eq("status.itemValue", "Open")
    .sort("createDate", "DESC")
    .limit(50)
)

for alert in alerts:
    print(alert.name, alert.severity)
```

## Field path syntax

FortiSOAR field paths follow a consistent pattern:

| Field type | Path to filter by display value | Path to filter by IRI/UUID |
|---|---|---|
| Picklist (severity, status…) | `severity.itemValue` | `severity` (IRI) |
| Single relationship (assignedTo…) | `assignedTo.name` | `assignedTo.uuid` |
| Scalar (name, sourceId…) | `name` | — |
| Date/epoch | `createDate` | — |

The `.itemValue` suffix is the most common pattern — it lets you write
`"Critical"` instead of `/api/3/picklists/<uuid>`:

```python
Query().eq("severity.itemValue", "Critical")
Query().eq("status.itemValue", "Open")
Query().eq("type.itemValue", "Brute Force Attack")
```

## Leaf operators

Each method adds one condition. All conditions in the same `Query` are joined by
its `logic` (default `"AND"`):

```python
# Records open AND created in the last 24 hours AND named "phishing*"
import time
Query()
    .eq("status.itemValue", "Open")
    .gt("createDate", time.time() - 86400)
    .like("name", "phishing")
```

| Method | Meaning | Example |
|---|---|---|
| `eq(field, value)` | equals | `.eq("status.itemValue", "Open")` |
| `neq(field, value)` | not equals | `.neq("status.itemValue", "Closed")` |
| `lt(field, value)` | less than | `.lt("createDate", ts)` |
| `lte(field, value)` | ≤ | `.lte("id", 1000)` |
| `gt(field, value)` | greater than | `.gt("createDate", ts)` |
| `gte(field, value)` | ≥ | `.gte("id", 500)` |
| `in_(field, values)` | any of list | `.in_("severity.itemValue", ["Critical", "High"])` |
| `nin(field, values)` | none of list | `.nin("status.itemValue", ["Closed", "Resolved"])` |
| `like(field, pattern)` | substring match | `.like("name", "phishing")` |
| `notlike(field, pattern)` | substring non-match | `.notlike("name", "test")` |
| `contains(field, value)` | collection contains | `.contains("recordTags", "malware")` |
| `exists(field, bool)` | field present/absent | `.exists("assignedTo", False)` |
| `isnull(field, bool)` | field null/non-null | `.isnull("resolvedDate")` |
| `changed(field)` | field changed (trigger only) | `.changed("status")` |
| `in_all(field, values)` | contains all (trigger only) | `.in_all("tags", ["a", "b"])` |

The escape hatch `where(field, operator, value)` works for any operator string.

## OR logic and nested groups

The top-level `Query` joins its conditions with `AND` by default. To express OR,
either change the top-level logic or nest a sub-group:

```python
# Match Open OR In Progress (top-level OR)
Query("OR").eq("status.itemValue", "Open").eq("status.itemValue", "In Progress")

# Match (Critical OR High) AND Open  (nested group)
severity_filter = Query("OR").in_("severity.itemValue", ["Critical", "High"])

client.records("alerts").filter(
    Query()
    .eq("status.itemValue", "Open")
    .group(severity_filter)
)
```

## Sorting and shaping results

```python
Query().sort("createDate", "DESC")      # newest first (default direction)
Query().sort("name", "ASC")             # alphabetical
Query().sort("createDate").sort("name") # multi-field sort

Query().select("uuid", "name", "severity", "status")  # return only these fields
Query().ignore("description", "sourcedata")            # strip large fields

Query().limit(100)        # page size (default 30)
Query().search("lateral movement")  # full-text search alongside filters
```

## Working with pages

`filter()` and `query()` return a {class}`~pyfsr.pagination.HydraPage`:

```python
page = client.records("alerts").filter(Query().eq("status.itemValue", "Open").limit(30))

print(f"{page.total} total open alerts")   # hydra:totalItems
print(f"Got {len(page)} on this page")     # records on this page

for alert in page:                          # iterable
    print(alert.name)

if page.has_next:
    next_page = client.records("alerts").filter(Query().eq("status.itemValue", "Open").limit(30).page(2))
```

## Streaming all results with `iterate()`

For processing more records than fit on one page, `iterate()` pages automatically:

```python
# Stream every open alert — pages fetched on demand, no manual pagination
for alert in client.records("alerts").iterate(Query().eq("status.itemValue", "Open")):
    print(alert.uuid, alert.name)

# Cap at 500 records
for alert in client.records("alerts").iterate(Query(), max_records=500):
    ...
```

## Convenience methods

For common one-liners, {class}`~pyfsr.records.RecordSet` provides shortcuts:

```python
alerts = client.records("alerts")

# First matching record (or None)
latest = alerts.first(Query().eq("status.itemValue", "Open").sort("createDate", "DESC"))

# Total count without fetching records
n = alerts.count(Query().eq("status.itemValue", "Open"))
print(f"{n} open alerts")

# Boolean existence check
if alerts.exists(Query().eq("sourceId", event_id)):
    print("already ingested")
```

## Inspecting the raw query body

Call `to_body()` to see the exact dict sent to the API — useful for debugging or
passing to lower-level calls:

```python
q = Query().eq("status.itemValue", "Open").sort("createDate").limit(50)
print(q.to_body())
# {
#   'logic': 'AND',
#   'filters': [{'field': 'status.itemValue', 'operator': 'eq', 'value': 'Open'}],
#   'sort': [{'field': 'createDate', 'direction': 'DESC'}],
#   'limit': 50
# }
```

Pass `module=` to enable field-path validation against the shipped field
knowledge base:

```python
Query(module="alerts").eq("severity.itemValue", "Critical")  # path checked at build time
Query(module="alerts").eq("typo_field", "value")             # raises ValueError
```
