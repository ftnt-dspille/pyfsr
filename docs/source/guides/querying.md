# Querying

{class}`~pyfsr.query.Query` is a fluent builder for FortiSOAR's ad-hoc query endpoint
(`POST /api/query/{module}`). It assembles the filter/sort/select body so you
never hand-write the JSON. Every mutator returns `self`, so calls chain:

```{code-block} python
from pyfsr import Query

q = (
    Query()
    .eq("status.itemValue", "Open")
    .gt("createDate", 1700000000)
    .sort("createDate", "DESC")
    .select("uuid", "name", "severity")
    .limit(50)
)

page = client.records("alerts").query(q)
```

## Leaf operators

Each operator adds one filter condition:

| Method | Operator | Meaning |
| --- | --- | --- |
| `eq` / `neq` | `eq` / `neq` | equals / not equals |
| `lt` / `lte` / `gt` / `gte` | comparison | less/greater than (or equal) |
| `in_` / `nin` | `in` / `nin` | value in / not in a list |
| `like` / `notlike` | `like` / `notlike` | pattern match |
| `contains` | `contains` | collection contains value |
| `exists` / `isnull` | existence | field present / null |
| `changed` | `changed` | field changed |
| `in_all` | `in_all` | contains all of the values |

The generic `where(field, operator, value)` is the escape hatch for any
operator.

## Nested groups & logic

Combine groups with `AND`/`OR` logic by nesting `Query` objects:

```{code-block} python
critical = Query("OR").eq("severity.itemValue", "Critical").eq("severity.itemValue", "High")

q = Query().eq("status.itemValue", "Open").group(critical)
```

## Sorting, selecting, paging

```{code-block} python
Query().sort("createDate", "ASC")     # sort direction (default DESC)
Query().select("uuid", "name")        # __selectFields allow-list
Query().ignore("description")         # exclude fields
Query().limit(30).page(2)             # page size + page number
Query().search("ransomware")          # free-text search
```

Call `to_body()` to inspect the raw dict that will be sent.

## Paginating results

`query()` returns a {class}`~pyfsr.pagination.HydraPage` — iterable, with `count`,
`has_next`, and `len()`. To stream across all pages, use `iterate()` on a
{class}`~pyfsr.records.RecordSet`, or the standalone {func}`~pyfsr.pagination.paginate` helper:

```{code-block} python
for rec in client.records("alerts").iterate(q):
    print(rec.name)
```
