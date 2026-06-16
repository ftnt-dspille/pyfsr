# Working with Records

pyfsr offers two ways to work with FortiSOAR data: a generic
{class}`~pyfsr.records.RecordSet` that works for **any** module, and typed,
module-specific APIs like `client.alerts`.

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
`/api/3/<module>/<uuid>` IRI — all resolve to the same record.

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

The package-level module APIs return Pydantic models with attribute access and
validation:

```{code-block} python
alert = client.alerts.get("alert-uuid")
print(alert.name, alert.severity)
```

Available typed models include `Alert`, `Incident`,
`Task`, `Comment`, `Workflow`, and
more. Look up the model class for any module with {func}`~pyfsr.models.model_for`, or
disable typing for a `RecordSet` with `client.records("alerts", typed=False)` to
get raw dicts.

## Picklist resolution

Picklist fields are stored as IRIs, not friendly strings — but `create`,
`update`, and `upsert` resolve friendly values for you automatically, so you
can pass `"High"` / `"Open"` directly:

```{code-block} python
alert = client.records("alerts").create({
    "name": "Test Alert",
    "severity": "High",     # → resolved to the severity IRI
    "status": "Open",       # → resolved to the status IRI
})
```

Resolution only touches fields the module flags as picklist-backed, passes
already-resolved IRIs through untouched, and is cached per client. Pass
`resolve_picklists=False` to skip it when every value is already an IRI:

```{code-block} python
client.records("alerts").create(data, resolve_picklists=False)
```

Need to resolve a value yourself? `client.picklists` exposes the lower-level
{func}`~pyfsr.api.picklists.PicklistsAPI.resolve` and
`resolve_record_fields` helpers (including a `strict=True` mode that raises with
the valid options on a bad value).
