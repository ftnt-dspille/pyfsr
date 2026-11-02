# Working with Records

pyfsr offers two ways to work with FortiSOAR data: a generic
{class}`~pyfsr.records.RecordSet` that works for **any** module, and typed,
module-specific APIs like `client.alerts`.

```{seealso}
Runnable examples:
[`examples/create_and_list_alerts.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/create_and_list_alerts.py)
(a minimal create + read) and
[`examples/upload_attachment_record.py`](https://github.com/ftnt-dspille/pyfsr/blob/main/examples/upload_attachment_record.py)
(file upload + linking an attachment record).
```

## Generic CRUD

`client.records("<module>")` returns a {class}`~pyfsr.records.RecordSet` bound to that
module, so you never hand-build `/api/3/<module>` URLs or unwrap Hydra
envelopes:

```{code-block} python
incidents = client.records("incidents")

inc = incidents.get("0d2c...")                       # fetch by uuid
created = incidents.create(name="Breach", severity=...)
incidents.update("0d2c...", status=...)
incidents.delete("0d2c...")
```

A record reference can be a bare uuid, the `module:uuid` shorthand, or a full
`/api/3/<module>/<uuid>` IRI -- all resolve to the same record.

### Return shapes

`get` returns the bound model (here `Alert`); picklist fields come back as
friendly values (`"Low"`) by default. Pass `raw=True` for the plain decoded
dict, and `resolve_picklists=False` to keep the wire shape (IRI strings or the
full expanded `itemValue` block the box returns):

```{doctest}
>>> client = demo_client()
>>> alerts = client.records("alerts")
>>> alert = alerts.get("9f0eb603-ac1e-41c3-b47b-444589beed39")
>>> type(alert).__name__, alert.name
('Alert', 'Response Capture Test Alert')
>>> alert.severity                       # typed: friendly picklist value
'Low'
>>> raw = alerts.get("9f0eb603-ac1e-41c3-b47b-444589beed39", raw=True, resolve_picklists=False)
>>> raw["severity"]["itemValue"], raw["status"]["itemValue"]   # raw, unresolved: expanded picklist block
('Low', 'Open')
```

`create` and `update` return the created/updated record the same way;
`delete` returns `None`:

```{doctest}
>>> created = alerts.create({"name": "New Alert"}, resolve_picklists=False)
>>> type(created).__name__, created.name, created.uuid[:8]
('Alert', 'Response Capture Test Alert', '9f0eb603')
>>> updated = alerts.update(
...     "9f0eb603-ac1e-41c3-b47b-444589beed39", {"name": "Renamed"},
...     resolve_picklists=False)
>>> updated.name
'Response Capture Test Alert'
>>> alerts.delete("9f0eb603-ac1e-41c3-b47b-444589beed39")  # returns None
```

`list` and `query` return a {class}`~pyfsr.pagination.HydraPage` -- iterate it,
index `members`, or read `total` / `has_next`:

```{doctest}
>>> page = alerts.list()
>>> type(page).__name__, len(page), page.total, page.has_next
('HydraPage', 1, 1, False)
>>> [a.name for a in page]
['Response Capture Test Alert']
>>> qpage = alerts.query(Query().eq("status.itemValue", "Open"))
>>> qpage.members[0].name
'Response Capture Test Alert'
```

`iterate()` streams across pages (here one page, one record) and `first` /
`count` / `exists` are the one-liner conveniences:

```{doctest}
>>> [a.name for a in alerts.iterate(Query())]
['Response Capture Test Alert']
>>> alerts.first(Query()).name, alerts.count(Query()), alerts.exists(Query())
('Response Capture Test Alert', 1, True)
```

## Querying & iterating

Pass a {class}`~pyfsr.query.Query` to fetch a page, or `iterate()` to stream across
pages transparently:

```{code-block} python
from pyfsr import Query

page = incidents.query(Query().eq("status.itemValue", "Open").limit(50))

for rec in incidents.iterate(Query().gt("createDate", ts)):
    print(rec.name)
```

See {doc}`querying` for the full DSL.

## Typed models

`client.records("<module>")` parses each record into the module's Pydantic
model (here `Alert`) -- attribute access, validation, and picklist-IRI
flattening for free:

```{doctest}
>>> alert = client.records("alerts").get("9f0eb603-ac1e-41c3-b47b-444589beed39")
>>> type(alert).__name__, alert.name
('Alert', 'Response Capture Test Alert')
```

The legacy `client.alerts` accessor (and the other package-level module APIs)
return the **raw decoded dict** instead -- handy when you want the wire shape
untouched, but without the typed niceties:

```{doctest}
>>> raw = client.alerts.get("9f0eb603-ac1e-41c3-b47b-444589beed39")
>>> type(raw).__name__, raw["name"]
('dict', 'Response Capture Test Alert')
```

Available typed models include `Alert`, `Incident`,
`Task`, `Comment`, `Workflow`, and
more. Look up the model class for any module with {func}`~pyfsr.models.model_for`.
Reads always come back typed; pass `raw=True` on an individual read (e.g.
`client.records("alerts").get(uuid, raw=True)`) when you want a plain dict.

## Picklist resolution

Picklist fields are stored as IRIs, not friendly strings -- but pyfsr speaks
friendly names in both directions by default. `create`, `update`, and `upsert`
resolve friendly values to IRIs before sending, and reads (including the
created/updated record that comes back) map picklist IRIs and expanded
picklist objects back to friendly values, so you can pass and read `"High"` /
`"Open"` directly:

```{code-block} python
alert = client.records("alerts").create({
    "name": "Test Alert",
    "severity": "High",     # → resolved to the severity IRI on the wire
    "status": "Open",       # → resolved to the status IRI on the wire
})
alert.severity               # → 'High' on the way back
```

Resolution only touches fields the module flags as picklist-backed, passes
already-resolved IRIs through untouched, and is cached per client. Pass
`resolve_picklists=False` to skip it -- both the friendly→IRI mapping on the
way out and the IRI→friendly mapping on the way back -- when you want the raw
wire shape:

```{code-block} python
client.records("alerts").create(data, resolve_picklists=False)
client.records("alerts").get(uuid, resolve_picklists=False)
```

Need to resolve a value yourself? `client.picklists` exposes the lower-level
{func}`~pyfsr.api.picklists.PicklistsAPI.resolve` (friendly→IRI),
`reverse_resolve` (IRI→friendly), and `resolve_record_fields` /
`reverse_resolve_record_fields` record-level helpers (including a `strict=True`
mode that raises with the valid options on a bad value).

## Bulk writes with per-row results

{meth}`~pyfsr.records.RecordSet.bulk_upsert` and
{meth}`~pyfsr.records.RecordSet.bulk_insert` write many rows in one request.
FortiSOAR answers with a multi-status envelope where a *partial* batch can
half-succeed: some rows land, others are rejected with a bare error string.
Pass `parse=True` to get a {class}`~pyfsr.records.BulkUpsertResult` instead of
that raw dict -- `.ok`, `.succeeded` (typed records), and `.failed` (one
{class}`~pyfsr.records.BulkUpsertFailure` per rejected row, with the input
`index` parsed out of FortiSOAR's raw message):

```{doctest}
>>> client = demo_client_jwt()
>>> result = client.records("alerts").bulk_upsert(
...     [{"name": "good alert", "severity": "Low"},
...      {"name": "bad alert", "severity": "not-a-real-severity"}],
...     parse=True,
... )
>>> result.ok                       # False -- at least one row was rejected
False
>>> len(result.succeeded), len(result.failed)
(1, 1)
>>> result.failed[0].index          # 0-based index into the input rows
1
>>> "does not match any of the options" in result.failed[0].message
True
```

`.raw` keeps the untouched server response if you need a field the result
class doesn't surface. To delete a known set of records, the mirror of a bulk
write is {meth}`~pyfsr.records.RecordSet.delete_many` (by IRI/ref list) or
{meth}`~pyfsr.records.RecordSet.delete_by_query` (by filter).
