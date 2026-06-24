# Module & Field Schema Reference

This is the **authoring reference** for FortiSOAR modules and fields: every field type
you can create, the properties each carries, and how relationship fields wire to other
modules. It is the companion to {doc}`module-admin` (which covers the staging → publish
*workflow*); this page is the **data model** you build with — and is written so an LLM can
generate valid field definitions from it.

Everything here was extracted from a live FortiSOAR appliance (its 64 modules and ~1,500
real fields) and verified by creating and publishing test modules.

```{seealso}
[`examples/all_field_types_module.py`](https://github.com/dylanspille/pyfsr/blob/main/examples/all_field_types_module.py)
builds a module exercising every field type described here.
```

## The two axes of a field: `type` vs `formType`

Every field (an *attribute* in the metadata) has **two** type axes, and they are not the
same thing:

| Axis | Metadata key | What it is | Allowed values |
| --- | --- | --- | --- |
| **Storage type** | `type` | the Postgres column type the platform stores | `string`, `integer`, `boolean`, `picklists`, `object`, `array`, or a **module type name** (for relationships) |
| **Display type** | `formType` | the kind of field shown/edited in the editor | `text`, `textarea`, `richtext`, `html`, `integer`, `datetime`, `checkbox`, `email`, `url`, `phone`, `password`, `filehash`, `ipv4`, `file`, `picklist`, `multiselectpicklist`, `lookup`, `manyToMany`, `oneToMany`, `object` |

```{important}
**There is no `text` storage type.** Text-like fields (`text`, `textarea`, `richtext`,
`email`, ...) all store `string`. Setting `type: "text"` *looks* fine in staging but
**fails at publish** with:

> `Attribute type 'text' does not exist as core or custom model metadata.`

This is the single most common authoring mistake. Always pair the display type with its
correct storage type — or let the typed builders do it for you (see below).
```

The display type → storage mapping is exposed in code as
{data}`pyfsr.api.modules_admin.DISPLAY_STORAGE_TYPE`:

| `formType` (display type) | `type` (storage) | Notes |
| --- | --- | --- |
| `text` | `string` | single-line |
| `textarea` | `string` | multi-line plain text |
| `richtext` | `string` | WYSIWYG rich text |
| `html` | `string` | raw HTML |
| `email` | `string` | email-format validation |
| `url` | `string` | URL field |
| `phone` | `string` | phone field |
| `password` | `string` | masked input; pair with `encrypted=True` to store encrypted |
| `filehash` | `string` | hash field (MD5/SHA) |
| `ipv4` | `string` | IP field |
| `file` | `string` | file attachment (`dataSource.model = "files"`) |
| `integer` | `integer` | whole number |
| `datetime` | `integer` | **stored as epoch-millis integer** — not a bug |
| `checkbox` | `boolean` | true/false |
| `object` | `object` | arbitrary JSON object |
| `picklist` | `picklists` | single-select — see [Picklist fields](#picklist-fields) |
| `multiselectpicklist` | `picklists` | multi-select (a collection) |
| `lookup` | *target module* | single reference (many-to-one) |
| `manyToMany` | *target module* | collection relationship |
| `oneToMany` | *target module* | collection relationship |

## Building fields: typed builders vs. `field()`

`client.modules_admin` gives you **typed builders** that set both axes correctly. Prefer
them — they make the `type: "text"` mistake impossible:

```python
admin = client.modules_admin

fields = [
    admin.text_field("name", required=True),          # string / text
    admin.text_field("summary", area=True),            # string / textarea
    admin.text_field("writeup", rich=True),            # string / richtext
    admin.integer_field("score"),                      # integer / integer
    admin.datetime_field("detectedOn"),                # integer / datetime
    admin.checkbox_field("isExternal"),                # boolean / checkbox
    admin.email_field("reporter"),                     # string / email
    admin.url_field("reference"),                      # string / url
    admin.object_field("rawPayload"),                  # object / object
    admin.picklist_field("status", "AlertStatus"),     # picklists / picklist
    admin.lookup_field("owner", "people"),             # people / lookup  (many-to-one)
    admin.relationship_field("relatedAlerts", "alerts"),  # alerts / manyToMany
]
admin.create_module("widgets", label="Widget", fields=fields)
```

`admin.typed_field(name, display_type, ...)` is the generic form for any scalar display
type in the table above (e.g. `admin.typed_field("md5", "filehash")`). For the low-level
escape hatch where you set both axes yourself, use
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.field` directly.

### Field properties (the editor's Properties panel)

Every builder accepts these keyword options (they map 1:1 to the in-product editor):

| Builder kwarg | Metadata | Meaning |
| --- | --- | --- |
| `label` | `descriptions.singular` | Field **Title** (human-readable). `name` is the immutable **API Key**. |
| `required` | `validation.required` | `True`/`False`, or a **condition dict** for "Required by condition". |
| `searchable` | `searchable` | Indexed for search. **Mutually exclusive with `encrypted`.** |
| `editable` | `writeable` | Editable in the UI. |
| `grid_column` | `gridColumn` | Shown as a default column in the list/grid view. |
| `encrypted` | `encrypted` | Stored encrypted at rest. Cannot be searchable. |
| `visibility` | `visibility` | `True`/`False`, or a **condition dict** for "Visible by condition". |
| `default_value` | `defaultValue` | Pre-filled value on new records. |
| `tooltip` | `tooltip` | Help text shown next to the field. |
| `minlength` / `maxlength` | `validation.*` | Length constraints (default max `10485760`). |
| `enable_range` | `validation._enableRange` | Enables the min/max numeric range UI. |
| `bulk_edit` | `bulkAction.allow` | Allow editing this field in bulk actions. |

Conditional `required`/`visibility` take a condition. Pass a {class}`~pyfsr.query.Query`
and pyfsr renders the FortiSOAR filter shape for you — e.g. "require `emailFrom` only
when `type` is Phishing":

```python
from pyfsr import Query

admin.email_field("emailFrom",
    required=Query(module="alerts").eq("type", "Phishing"))
```

Because the `Query` is module-bound, the picklist field auto-resolves to
`type.itemValue` and you compare by the friendly name instead of hunting down
`/api/3/picklists/<uuid>`. A pre-built condition `dict` (or a plain `True`/`False`)
still works if you'd rather assemble it yourself.

## Picklist fields

Picklists store `type: "picklists"` and bind to a named picklist via `dataSource`:

```python
admin.picklist_field("severity", "AlertSeverity")             # single-select
admin.picklist_field("threatTypes", "ThreatType", multi=True)  # multi-select (collection)
```

- `picklist_name` is the picklist's **list name** (e.g. `"AlertSeverity"`), discoverable
  via `client.picklists`.
- `multi=True` switches the display type to `multiselectpicklist` and sets `collection=True`.
- The builder writes the `dataSource` query that filters `picklists` by
  `listName__name == <picklist_name>`, sorted by `orderIndex`.

To create the picklist *values* themselves, manage `/api/3/picklist_names` and
`/api/3/picklists` separately — fields only *reference* an existing picklist.

## Relationships

Three relationship display types, distinguished by cardinality and which side owns the join:

| Display type | Cardinality | `collection` | Owns join? | Reverse field on target |
| --- | --- | --- | --- | --- |
| `lookup` | many-to-one | `False` | no | none — one-directional pointer |
| `manyToMany` | many-to-many | `True` | yes | always exists (see below) |
| `oneToMany` | one-to-many | `True` | yes | a `lookup` on the target |

Wiring keys on a relationship attribute:

- **`type`** — the target module type (e.g. `"alerts"`), not `string`.
- **`inversedField`** — name of the field on the target that points back.
- **`ownsRelationship`** — `True` on the side that owns the join table.

**pyfsr keeps both sides valid for you.**
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.add_field` creates the reverse side on the
target whenever the platform won't, so you only declare the relationship once. Pass
`create_reverse=False` to opt out and manage the target side yourself.

### Lookup (many-to-one)

```python
admin.add_field("incidents", admin.lookup_field("owner", "people"))
```

A single pointer to one target record — not a collection, owns nothing, and intentionally
has **no** reverse field. Two modules can each look up `people` independently. Use it
whenever a record just references one X.

### Many-to-many

```python
# Default inverse — FortiSOAR mirrors the reverse field itself:
admin.add_field("incidents", admin.relationship_field("relatedAlerts", "alerts"))

# Custom inverse name — pyfsr adds the matching reverse field to the target:
admin.add_field("incidents",
    admin.relationship_field("relatedAlerts", "alerts", inversed_field="parentIncidents"))
```

A many-to-many always ends up two-directional. With the **default** inverse the platform
creates the reverse field (named after the source module) at staging time. With a **custom**
`inversed_field` the platform does not — so `add_field` adds the mirror `manyToMany`
(`ownsRelationship=False`, `inversedField` pointing back) to the target for you.

### One-to-many

```python
admin.add_field("incidents",
    admin.relationship_field("relatedAlerts", "alerts", many=False, inversed_field="incident"))
```

A `oneToMany` requires a matching `lookup` (many-to-one) on the target whose name equals
`inversed_field` — without it, publish fails with *"there is no lookup field present in
'<target>'"*. `add_field` creates that lookup on the target automatically, so the single
call above leaves both modules publishable. (The target module must already exist.)

## Verifying & publishing

After publish, confirm the reverse attribute the platform actually stored with
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.reverse_field`:

```python
admin.create_module("widgets", fields=[admin.text_field("name", required=True)])
admin.add_field("widgets", admin.relationship_field("relatedAlerts", "alerts"))
admin.publish()                       # appliance-wide; blocks until committed

rev = admin.reverse_field("widgets", "relatedAlerts", published=True)
print("Reverse field:", rev["name"], rev["formType"])
```

```{warning}
**A publish that reports "started" is not a publish that committed.** `PUT /api/publish`
only *kicks off* an asynchronous backup + migrate + commit, during which the **entire API
(`/api/3`) returns 503** for ~30–60s. The default
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.publish` waits out that outage and confirms
the outcome via `/api/publish/error` (a fresh `last_publish_time` with `status: "Success"`),
raising the appliance's reported error on any other state. Test reverse-field behavior
end-to-end on a real publish — staging shows the *intended* wiring, but only a committed
publish creates the physical join.
```

```{note}
**Schema validation errors are synchronous.** A bad field — a `type` that does not exist,
or a `oneToMany` whose target has no matching lookup — is rejected on the `PUT /api/publish`
itself (HTTP 400) *before* any migrate runs, raised as an
{class}`~pyfsr.exceptions.APIError` whose message is the appliance's own, e.g.:

> `For many-to-one 'brokenRel' field in 'widgets' module there is no lookup field present
> in 'alerts' module.`

Surface that message verbatim — it names the offending field and module. (Each error line
is prefixed with internal `modelMetadatas[uuid].attributes[uuid].formtype:` ids you can
strip for end users.) Because validation fails before the migrate, `/api/publish/error` is
untouched and nothing on the appliance changes.
```

## Validation: caught early vs. caught at publish

The appliance accepts a lot of *invalid* schema into **staging** and only rejects it during
the slow, appliance-wide **publish** — or worse, publishes a broken module. To avoid that
round-trip, the builders reject the known-bad inputs **client-side**, raising `ValueError`
before anything is sent:

| Bad input | Where the appliance catches it | pyfsr guard |
| --- | --- | --- |
| `db_type="text"` / `"json"` / `"datetime"` | publish (*"Attribute type 'text' does not exist"*) | `field()` raises — use the typed builder |
| field name with spaces / punctuation / leading digit | publish (bad SQL column) | `field()` raises — must match `^[A-Za-z][A-Za-z0-9_]*$` |
| `encrypted` **and** `searchable` both set | silently broken | `field()` raises (mutually exclusive) |
| module name with uppercase / spaces / leading digit | publish / broken table | `create_module()` raises — must match `^[a-z][a-z0-9_]*$` |
| empty `fields=[]` | invalid module | `create_module()` raises |
| name longer than 63 chars | publish (Postgres identifier limit) | both raise |
| **duplicate field name** in a module | **staging POST** (fast) | appliance already rejects — *"Duplicate field 'x'… names are case-insensitive"* |
| **reserved key** `id` | **staging POST** (fast) | appliance already rejects — *"'id' is a reserved keyword"* |
| **duplicate module** type | **staging POST** (fast) | appliance already rejects (uniqueness constraint) |
| `oneToMany` with no lookup on target | publish | `add_field` creates the target lookup for you (see Relationships) |
| relationship to a non-existent target module | publish | `add_field` raises a clear error naming the missing target |

```{note}
The last group is *not* guarded client-side because the appliance already fails fast (at
the cheap staging `POST`, not at publish) with a clear message — surface it as-is. Only the
checks that would otherwise slip through to the expensive publish are enforced in pyfsr.
```

### A bad draft anywhere wedges the whole publish

Publish is **appliance-wide**, so a single illegally-named draft created outside pyfsr (in
the in-product editor, or by another tool) makes *every* publish fail mid-migrate with a
cryptic Postgres error and no module name:

> `syntax error, unexpected integer "9", expecting identifier`

Worse, `/api/publish/error` can still report `Success` while nothing actually commits. To
prevent this, {meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.publish` runs
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.find_invalid_drafts` first and refuses with
a named error before the destructive PUT:

```python
admin.find_invalid_drafts()
# [{'module': '9probe', 'uuid': '...', 'problem': 'invalid module name'}]

admin.publish()
# ValueError: refusing to publish: invalid staged draft(s) would fail the
# appliance-wide migrate: '9probe' (invalid module name). Fix or discard them...

admin.discard_staging_draft("9probe")   # remove the offender, then publish cleanly
admin.publish()
```

Use `find_invalid_drafts(deep=True)` to also scan every draft's **field** names (one read
per module). Pass `publish(precheck=False)` only if you deliberately want to skip the check.

### Recommended authoring workflow

1. Build all fields with the **typed builders** (correct `type`/`formType` guaranteed).
2. `create_module(...)` / `add_field(...)` → stages the draft; `add_field` also stages the
   reverse side of relationships on the target (the `oneToMany` target lookup, the
   custom-inverse `manyToMany` mirror).
3. `pending_changes()` / `find_invalid_drafts()` → confirm only your modules are pending and
   nothing staged (by you or anyone) would wedge the appliance-wide publish.
4. `publish()` → prechecks for invalid drafts, commits, and **waits for the real commit**.
5. `reverse_field(..., published=True)` → confirm each relationship's reverse landed.

## Module-level settings

When you `create_module(...)` (or `set_module_settings(...)`), these flags map to the
editor's **Additional Settings**:

| Builder kwarg | Metadata | Meaning |
| --- | --- | --- |
| `ownable` | `ownable` + `userOwnable` | Team/user record ownership. |
| `trackable` | `trackable` | Record-level change history. |
| `indexable` | `indexable` | Full-text indexing. |
| `taggable` | `taggable` | Allow tags on records. |
| `queueable` | `queueable` | Eligible for queue management. |
| `recycle_bin` | `softDeleteable` | Soft-delete / recycle bin. |
| `multi_tenancy` | `peerReplicable` | Replicate across tenants. |
| `display_template` | `displayName` | Jinja record title, e.g. `"{{ name }}"`. |
| `record_uniqueness` | `uniqueConstraint` | List of field names enforcing uniqueness. |
| `default_sort` | `defaultSort` | e.g. `[{"field": "createDate", "direction": "DESC"}]`. |

```{note}
There is **no delete-module API**. An unpublished draft can be removed with
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.discard_staging_draft`; a *published*
module and its Postgres table persist even after the draft is discarded.
```
