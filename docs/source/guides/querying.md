# Querying

{class}`~pyfsr.query.Query` is a fluent builder for FortiSOAR's structured query
endpoint (`POST /api/query/{module}`). Every method returns `self` so calls chain
naturally. Results come back as typed {class}`~pyfsr.pagination.HydraPage` objects
that you can iterate, slice, or introspect.

```{note}
This guide covers the pyfsr `Query` **builder** (the Python ergonomics). For the underlying
**wire protocol** — every filter/aggregation operator, OR/AND nesting, `$search`, Elasticsearch
global search, pagination, and source-verified quirks — see the canonical FortiSOAR Query API
reference: `~/PycharmProjects/Miscellaneous/fortisoar/FortiSOAR_Query_Aggregation_and_Filter_Options.md`.
```

```{seealso}
A runnable, guided tour of every builder feature lives in
[`examples/queries.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/queries.py).
```

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

:::{tip}
**Bind the query to a module and pyfsr fills in `.itemValue` for you.** When you
pass `module=`, a bare picklist field is auto-resolved to its `.itemValue` path,
so you can drop the suffix entirely:

```{doctest}
>>> Query(module="alerts").eq("severity", "Critical").to_body()["filters"][0]["field"]
'severity.itemValue'
```

If you pass an IRI or UUID value instead (e.g. `eq("severity", "/api/3/picklists/…")`),
pyfsr leaves the field bare so the comparison is by IRI. This only applies to
picklist fields; module relationships like `assignedTo` stay explicit (you choose
`.name` vs `.uuid`).
:::

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

### Inline grouping with `.or_()` and `.and_()`

`.group()` is explicit but verbose. `.or_()` and `.and_()` build the same nested
groups inline — pass a pre-built `Query`, or call with no argument to open an
inline context that collects the following leaf filters:

```python
# (status == Open) OR (type == phishing AND severity == High)
(Query("OR")
 .eq("status.itemValue", "Open")
 .and_()                              # opens an AND sub-group
 .eq("type.itemValue", "phishing")
 .eq("severity.itemValue", "High"))

# Equivalent with a pre-built group:
inner = Query("AND").eq("type.itemValue", "phishing").eq("severity.itemValue", "High")
Query("OR").eq("status.itemValue", "Open").and_(inner)
```

Inside an inline context, leaf methods (`.eq()`, `.in_()`, …) apply to the
sub-group, while shaping methods (`.sort()`, `.select()`, `.limit()`) and the
terminal `.to_body()` / `.model()` apply to and close out the parent query:

```python
(Query("OR")
 .eq("status.itemValue", "Open")
 .and_().eq("type.itemValue", "phishing").eq("severity.itemValue", "High")
 .sort("createDate", "DESC")          # applies to the parent query
 .limit(50))
```

The wire body these build is exactly what you'd hand-assemble — an `AND`
sub-group nested under the parent's filters:

```{doctest}
>>> body = (Query("OR")
...     .eq("status.itemValue", "Open")
...     .and_().eq("type.itemValue", "phishing").eq("severity.itemValue", "High")
...     .to_body())
>>> body["logic"]
'OR'
>>> body["filters"][1]["logic"]
'AND'
>>> [f["field"] for f in body["filters"][1]["filters"]]
['type.itemValue', 'severity.itemValue']
```

Arbitrary depth is reachable by nesting `.group()` inside a pre-built sub-group —
e.g. `(A AND (B OR C)) OR (D AND E)`:

```python
(Query("OR")
 .and_(Query("AND")
       .eq("status.itemValue", "Open")
       .group(Query("OR").eq("type.itemValue", "A").eq("type.itemValue", "B")))
 .and_(Query("AND")
       .eq("severity.itemValue", "High")
       .eq("owner.itemValue", "alice")))
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

The executed shape against a recorded response (no network — `demo_client()`
replays a captured `/api/query/alerts` page):

```{doctest}
>>> client = demo_client()
>>> page = client.records("alerts").filter(Query().eq("status.itemValue", "Open"))
>>> type(page).__name__
'HydraPage'
>>> page.total, len(page), page.has_next     # hydra:totalItems, on-page count, more?
(1, 1, False)
>>> page.members[0].name                     # index members directly
'Response Capture Test Alert'
>>> [a.name for a in page]                    # or iterate
['Response Capture Test Alert']
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

```{doctest}
>>> q = Query().eq("status.itemValue", "Open").sort("createDate").limit(50)
>>> q.to_body()
{'logic': 'AND', 'filters': [{'field': 'status.itemValue', 'operator': 'eq', 'value': 'Open'}], 'sort': [{'field': 'createDate', 'direction': 'DESC'}], 'limit': 50}
```

Pass `module=` to enable field-path validation against the shipped field
knowledge base:

```{doctest}
>>> Query(module="alerts").eq("severity.itemValue", "Critical").to_body()["filters"][0]
{'field': 'severity.itemValue', 'operator': 'eq', 'value': 'Critical'}

>>> Query(module="alerts").eq("typo_field", "value")  # doctest: +ELLIPSIS
Traceback (most recent call last):
    ...
ValueError: 'alerts' has no field 'typo_field' (in path 'typo_field'); did you mean one of: ...
```
