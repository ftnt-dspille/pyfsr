# Module & Field Schema Reference

This is the **authoring reference** for FortiSOAR modules and fields: every field type
you can create, the properties each carries, and how relationship fields wire to other
modules. It is the companion to {doc}`module-admin` (which covers the staging → publish
*workflow*); this page is the **data model** you build with — and is written so an LLM can
generate valid field definitions from it.

Everything here was extracted from a live FortiSOAR appliance (its 64 modules and ~1,500
real fields) and verified by creating and publishing test modules.

```{contents}
:local:
:depth: 2
```

## The two axes of a field: `type` vs `formType`

Every field (an *attribute* in the metadata) has **two** type axes, and they are not the
same thing:

| Axis | Metadata key | What it is | Allowed values |
| --- | --- | --- | --- |
| **Storage type** | `type` | the Postgres column type the platform stores | `string`, `integer`, `boolean`, `picklists`, `object`, `array`, or a **module type name** (for relationships) |
| **UI widget** | `formType` | the editor control rendered for the field | `text`, `textarea`, `richtext`, `html`, `integer`, `datetime`, `checkbox`, `email`, `url`, `phone`, `password`, `filehash`, `ipv4`, `file`, `picklist`, `multiselectpicklist`, `lookup`, `manyToMany`, `oneToMany`, `object` |

```{important}
**There is no `text` storage type.** Text-like widgets (`text`, `textarea`, `richtext`,
`email`, ...) all store `string`. Setting `type: "text"` *looks* fine in staging but
**fails at publish** with:

> `Attribute type 'text' does not exist as core or custom model metadata.`

This is the single most common authoring mistake. Always pair the widget with its correct
storage type — or let the typed builders do it for you (see below).
```

The widget → storage mapping is exposed in code as
{data}`pyfsr.api.modules_admin.WIDGET_STORAGE_TYPE`:

| `formType` (widget) | `type` (storage) | Notes |
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

`admin.typed_field(name, form_type, ...)` is the generic form for any scalar widget in the
table above (e.g. `admin.typed_field("md5", "filehash")`). For the low-level escape hatch
where you set both axes yourself, use
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

Conditional `required`/`visibility` take the FortiSOAR filter shape, e.g. "require
`emailFrom` only when `type` is Phishing":

```python
admin.email_field("emailFrom", required={
    "logic": "AND",
    "filters": [
        {"field": "type", "operator": "eq",
         "value": "/api/3/picklists/<phishing-uuid>"}
    ],
})
```

## Picklist fields

Picklists store `type: "picklists"` and bind to a named picklist via `dataSource`:

```python
admin.picklist_field("severity", "AlertSeverity")             # single-select
admin.picklist_field("threatTypes", "ThreatType", multi=True)  # multi-select (collection)
```

- `picklist_name` is the picklist's **list name** (e.g. `"AlertSeverity"`), discoverable
  via `client.picklists`.
- `multi=True` switches the widget to `multiselectpicklist` and sets `collection=True`.
- The builder writes the `dataSource` query that filters `picklists` by
  `listName__name == <picklist_name>`, sorted by `orderIndex`.

To create the picklist *values* themselves, manage `/api/3/picklist_names` and
`/api/3/picklists` separately — fields only *reference* an existing picklist.

## Relationships

This is where "SOAR sometimes auto-creates the reverse field and sometimes doesn't" lives.
There are three relationship widgets, and they behave **very differently** with respect to
the field created on the *other* (target) module.

| Widget | Cardinality | `collection` | Owns join? | Reverse field on target? |
| --- | --- | --- | --- | --- |
| `lookup` | many-to-one | `False` | no | **Never** — one-directional pointer |
| `manyToMany` | many-to-many | `True` | yes | **Yes if default inverse; no if custom `inversedField`** |
| `oneToMany` | one-to-many | `True` | yes | **Must pre-exist** as a `lookup` on the target |

The wiring keys on each relationship attribute:

- **`type`** — the **target module type** (e.g. `"alerts"`), not `string`.
- **`inversedField`** — the *name of the field on the target module* that points back.
- **`ownsRelationship`** — `True` on the side that owns the join table.

### Lookup (many-to-one): a single reference, no reverse

```python
admin.lookup_field("owner", "people")     # one person per record
```

A lookup is a single pointer to one record of the target. It is **not** a collection and
owns nothing, so FortiSOAR creates **no reverse field** on the target. Two modules can each
have a lookup to `people` independently. This is the safe, predictable relationship — use
it whenever you just need "this record references one X".

### Many-to-many: reverse field depends on `inversedField`

```python
# Default inverse — reverse field IS auto-created on the target:
admin.relationship_field("campaigns", "campaigns")

# Custom inverse name — reverse field is NOT auto-created:
admin.relationship_field("campaigns", "campaigns", inversed_field="myAlerts")
```

Verified behavior (observed at **staging** time, before publish):

- With the **default** inverse (`inversed_field=None`), the editor immediately adds a
  reverse `manyToMany` field to the target module, **named after the source module**, with
  `ownsRelationship=False` and `inversedField` pointing back to your field. This is the
  "it auto-created the field for me" case.
- With a **custom** `inversed_field`, FortiSOAR stores your name on the owning side but
  **does not** create the matching reverse field on the target. You must add it yourself
  (a second `relationship_field` on the target pointing back). This is the "it didn't
  create the field" case.

### One-to-many: the reverse lookup must already exist

```python
# On the target module FIRST, create the back-reference lookup:
admin.add_field("agents", admin.lookup_field("router", "routers"))

# Then the one-to-many on the source, whose inverse is that lookup's name:
admin.add_field("routers", admin.relationship_field(
    "agents", "agents", many=False, inversed_field="router"))
```

A `oneToMany` is **not** self-sufficient: it requires a matching `lookup` (many-to-one)
field on the target whose name equals `inversed_field`. If it is missing, **publish fails**
with:

> `For many-to-one '<field>' field in '<module>' module there is no lookup field present in
> '<target>' module.`

Create the lookup on the target *before* publishing.

## Verifying reverse fields & publishing

Because reverse-field creation is conditional, **always verify after publish** rather than
assuming. The {meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.reverse_field` helper resolves
whatever reverse attribute (if any) the platform created:

```python
admin.create_module("widgets", fields=[
    admin.text_field("name", required=True),
    admin.relationship_field("relatedAlerts", "alerts"),
])
admin.publish()                       # appliance-wide; blocks until committed

# Did the reverse field actually land on `alerts`?
rev = admin.reverse_field("widgets", "relatedAlerts", published=True)
if rev is None:
    print("No reverse field — add it manually on the target if you need it.")
else:
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
| `oneToMany` with no lookup on target | publish | create the target lookup first (see Relationships) |
| relationship to a non-existent target module | publish | verify the target exists first |

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
2. For `oneToMany`, create the target-side `lookup` **first**.
3. `create_module(...)` / `add_field(...)` → stages the draft.
4. `pending_changes()` / `find_invalid_drafts()` → confirm only your modules are pending and
   nothing staged (by you or anyone) would wedge the appliance-wide publish.
5. `publish()` → prechecks for invalid drafts, commits, and **waits for the real commit**.
6. `reverse_field(..., published=True)` → verify each relationship's reverse, and add any
   missing reverse field manually, then publish again.

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
