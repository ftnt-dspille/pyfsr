# Module Editor

Where {class}`~pyfsr.api.modules.ModulesAPI` (`client.modules`) is read-only
*discovery*, `client.modules_admin` ({class}`~pyfsr.api.modules_admin.ModulesAdminAPI`)
is the **write** surface for the Application/Module Editor — create modules, add and
alter fields, track pending changes, and publish.

All examples below were run against a live FortiSOAR appliance; the outputs shown are
real (trimmed for length).

## How the editor really works

FortiSOAR keeps schema in two parallel stores, and a separate physical layer:

| Store / layer | Endpoint | Holds |
| --- | --- | --- |
| **Staging** | `/api/3/staging_model_metadatas` | the editable draft of every module |
| **Published** | `/api/3/model_metadatas` | the committed schema records reads use |
| **Physical table** | `/api/3/<module>` | only created when a global **publish** runs its migration |

Both stores mirror *all* modules. A module has an **uncommitted change** when its
staging record differs from its published one. Creating a module or editing a field
touches **staging only** — nothing is live until you {meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.publish`,
which runs an appliance-wide backup + DB migrate cycle and creates the table.

```{warning}
**Publish is appliance-wide.** `PUT /api/publish` promotes *every* pending staged
change across the whole instance, not just modules you touched. On a shared box,
check {meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.pending_changes` first.
```

## Walkthrough: two linked modules (a heist tracker)

Before the reference sections, here's the whole arc end to end. We'll build a
tiny **heist tracker**: a `crew` module (the people pulling the job) and a
`heists` module (the jobs), linked so a heist has a whole crew and a crew member
has a rap sheet of heists. The fun part — you only declare the link **once**;
the SDK stages the reverse side for you.

```python
admin = client.modules_admin

# 1. The crew. Each member has a name and a specialty.
admin.create_module(
    "crew",
    label="Crew Member",
    plural="Crew",
    fields=[
        admin.text_field("alias", required=True),  # "The Brains", "Wheels"  (grid column by default)
        admin.picklist_field("specialty", "AlertType"),              # reuse any existing picklist
        admin.checkbox_field("trustworthy"),
    ],
    record_uniqueness=["alias"],
)

# 2. The heists. The `crew` field is the link — a many-to-many relationship to
#    the module we just made. We declare it ONLY here.
admin.create_module(
    "heists",
    label="Heist",
    plural="Heists",
    fields=[
        admin.text_field("codename", required=True),  # "Operation Cannoli"
        admin.text_field("target"),
        admin.integer_field("takeUsd"),
        admin.datetime_field("goTime"),
        admin.relationship_field("crew", "crew", label="Crew"),         # <-- the linkage
    ],
)
```

That single `relationship_field` is the whole trick. Because the SDK keeps both
sides of a relationship valid, it auto-stages the **reverse field on `crew`** —
so each crew member gets a `heists` field listing every job they're on, without
you touching the `crew` module again:

```python
[a["name"] for a in admin.get_staging("crew")["attributes"]]
# ['alias', 'specialty', 'trustworthy', 'heists']   <-- 'heists' appeared on its own
```

Nothing is live yet — both modules are staging-only drafts. Check what a publish
would commit, then commit it (remember: **publish is appliance-wide**):

```python
admin.pending_changes()
# [{'module': 'crew', 'change': 'created'}, {'module': 'heists', 'change': 'created'}]

admin.publish()   # backup + migrate; blocks ~30–60s while /api/3 is down
```

Now the tables exist and you can populate the caper. Create the crew, then a
heist that references them — the link is just a list of record IRIs:

```python
danny  = client.records("crew").create({"alias": "The Brains",  "trustworthy": True})
linus  = client.records("crew").create({"alias": "Light Fingers", "trustworthy": True})

job = client.records("heists").create({
    "codename": "Operation Cannoli",
    "target": "Bellagio Vault",
    "takeUsd": 150_000_000,
    "crew": [danny["@id"], linus["@id"]],   # link by IRI
})
```

Because the reverse field exists, the relationship reads **both ways** for free —
ask a heist for its crew, or a crew member for their heists:

```python
client.records("heists").get(job["uuid"], relationships=True)["crew"]
# -> [{'alias': 'The Brains', ...}, {'alias': 'Light Fingers', ...}]

client.records("crew").get(danny["uuid"], relationships=True)["heists"]
# -> [{'codename': 'Operation Cannoli', ...}]
```

That's the full loop: **two `create_module` calls, one relationship, one
publish** — and a bidirectional link you only had to describe once. The rest of
this guide is the reference behind each step.

## Inspecting existing schema (read-only)

These read-only calls are doctested against captured appliance responses
(`demo_client()`), so the outputs below are real:

```{doctest}
>>> client = demo_client()
>>> admin = client.modules_admin
>>> admin.is_published("alerts")
True
>>> admin.is_published("nonexistentmod")
False
>>> pub = admin.get_published("alerts", typed=True)
>>> (pub.type, pub.module)            # PublishedModelMetadata
('alerts', 'alerts')
>>> sev = admin.get_field("alerts", "severity", typed=True)
>>> (sev.name, sev.type)             # AttributeMetadata
('severity', 'picklists')
>>> admin.pending_changes()          # fully-published box: nothing staged
[]
```

`get_published` / `get_staging` return the raw record dict (with every field under
`attributes`) when called without `typed=True`; pass `typed=True` for the matching
{class}`~pyfsr.models.PublishedModelMetadata` /
{class}`~pyfsr.models.StagingModelMetadata` /
{class}`~pyfsr.models.AttributeMetadata` model shown above.

```{note}
`is_published()` reports presence in `model_metadatas`. A freshly created module is
**staging-only** until you publish, so it reads `False` until then.
```

## Building fields

```{tip}
For the **full field-type catalogue** — every display type, its storage type, properties,
and relationship/reverse-field semantics — see {doc}`module-field-schema`. This section is a
quick start; that page is the authoring reference.
```

Prefer the **typed builders**, which set the storage `type` and `formType` (display type)
to a matching pair for you (e.g. a `datetime` field must store `integer`; a `text` field
must store `string`):

```python
admin.text_field("summary", area=True)     # string / textarea
admin.integer_field("score")                # integer / integer
admin.datetime_field("detectedOn")          # integer / datetime
admin.checkbox_field("isExternal")          # boolean / checkbox
admin.object_field("payload", label="Payload")   # object / object
```

```{warning}
There is **no `text` storage type** (and no `json` type). Text fields store `string`;
JSON stores `object`. Hand-setting `db_type="text"` stages fine but **fails at publish**
("Attribute type 'text' does not exist"). The typed builders avoid this entirely.
```

{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.field` is the low-level escape hatch where
you set both axes yourself; `admin.typed_field(name, display_type)` derives the storage
type for any scalar display type. The object field above produces:

```json
{
  "name": "payload",
  "type": "object",
  "formType": "object",
  "descriptions": {"singular": "Payload"},
  "displayName": "{{ payload }}",
  "searchable": false,
  "collection": false,
  "visibility": true,
  "readable": true,
  "writeable": true,
  "validation": {"required": false, "minlength": 0, "maxlength": 10485760}
}
```

### Field options

`field()` mirrors the editor's **Properties** panel. Beyond `db_type`/`form_type`, it
exposes the full options surface. **`grid_column` (Default Grid Column) is on by default**
for scalar, lookup and picklist fields — they show in the module's list/grid view without
opting each one in — and **off** for `password`, `object`/`json`/`array` and collection
relationships (`manyToMany`/`oneToMany`), the types that are never grid columns in
practice. Override either way with `grid_column=True/False`:

```python
admin.field(
    "secret",
    label="API Secret",          # Field Title (name is the immutable API Key)
    editable=True,               # UI "Editable"  -> writeable
    searchable=False,            # Field Options row...
    grid_column=False,           # "Default Grid Column" — text defaults visible; hide this one
    encrypted=True,              # "Encrypted" (mutually exclusive with searchable)
    required=True,               # or a condition dict for "Required by condition"
    visibility=True,             # or a condition dict for "Visible by Condition"
    default_value="",
    tooltip="Stored encrypted",
    minlength=0, maxlength=1024, enable_range=True,   # Length Constraints
    bulk_edit=True,              # "Allow Bulk Edit" -> bulkAction.allow
)

# the default picks a sensible value per type, so most fields need no grid_column at all:
admin.password_field("apiKey")               # -> gridColumn: false  (default for password)
admin.text_field("notes", grid_column=False) # scalar, but kept out of the list view
```

### Picklist and relationship fields

```python
# single- or multi-select picklist, bound to a picklist list name
admin.picklist_field("severity", "AlertSeverity")
admin.picklist_field("tags", "AlertType", multi=True)   # -> multiselectpicklist

# a single reference to one record of another module (many-to-one, no reverse field)
admin.lookup_field("owner", "people", label="Owner")

# a many-to-many relationship to another module (reverse field auto-created on target)
admin.relationship_field("relatedalerts", "alerts", label="Related Alerts")
```

```{note}
`add_field` keeps both sides of a relationship valid: it creates the reverse field on the
target when the platform won't (the `oneToMany` target lookup, the custom-inverse
`manyToMany` mirror). Pass `create_reverse=False` to manage the target side yourself. See
{doc}`module-field-schema` for the per-relationship rules and `reverse_field()` verification.
```

## Creating a module

`create_module` posts to staging and — matching the in-product editor — also creates
the default list/detail/form layouts so the module renders in the UI. Pass
`create_view_templates=False` for an API-only module. The keyword flags map directly to
the editor's **Additional Settings**.

```python
admin.create_module(
    "widgets",
    label="Widget",
    plural="Widgets",
    fields=[
        admin.text_field("name", required=True),
        admin.text_field("payload", area=True),
        admin.picklist_field("severity", "AlertSeverity"),
        admin.relationship_field("relatedalerts", "alerts"),
    ],
    # Additional Settings:
    ownable=True,                # Team Ownable (also sets userOwnable)
    trackable=True,
    indexable=True,
    taggable=True,
    queueable=False,
    recycle_bin=True,            # Enable Recycle Bin -> softDeleteable
    multi_tenancy=False,         # Enable Multi-Tenancy -> peerReplicable
    record_uniqueness=["name"],  # uniqueConstraint
    default_sort=[{"field": "createDate", "direction": "DESC"}],
)
# staging record -> {'uuid': '868221dc-...', 'type': 'widgets',
#                    'module': 'widgets', 'displayName': '{{ name }}'}

admin.get_view_templates("widgets")
# layouts created -> ['detail', 'form', 'list']
```

Edit staged fields before publishing:

```python
admin.add_field("widgets", admin.email_field("reporter"))
admin.set_field_type("widgets", "payload", db_type="object", form_type="object")

[(a["name"], a["type"], a["formType"]) for a in admin.get_staging("widgets")["attributes"]]
# [('name', 'string', 'text'), ('payload', 'object', 'object'), ('reporter', 'string', 'email')]
```

### Editing settings on an existing module

`set_module_settings` updates the **Additional Settings** (and display template / sort)
of a staged module, using the same friendly names as `create_module`:

```python
admin.set_module_settings(
    "widgets",
    taggable=False,
    ownable=True,                       # also syncs userOwnable
    recycle_bin=True,                   # -> softDeleteable
    display_template="{{ name }}",
    default_sort=[{"field": "createDate", "direction": "DESC"}],
)
```

```{note}
**Auto-mirror appliances.** Some builds (e.g. with the dev-mode schema toggle on)
re-sync `staging_model_metadatas` into `model_metadatas` on *every* write — so a staged
create or edit shows up in the "published" store immediately, and a settings PUT can
surface a sync error in its response even though the staging row updated. Because of
this, `set_module_settings` confirms the change by **re-reading staging** and only raises
if a value did not actually take. It's also why `is_published()` may read `True` for a
module you have not explicitly published on such a box.
```

## Tracking pending changes

Before an appliance-wide publish, see exactly what would be committed.
{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.pending_changes` diffs staging against
published:

```python
admin.pending_changes()
# [{'module': 'widgets', 'change': 'created'}]
#   change is one of: 'created' | 'modified' | 'deleted'
```

An empty list means the appliance is fully published — nothing for `publish()` to do.

## Publishing

```python
admin.publish()   # appliance-wide commit; blocks until the migrate cycle finishes
```

`PUT /api/publish` only *starts* the publish — its response is `{"status": "started"}` —
and the backup + DB migrate then runs asynchronously, during which the **whole API
(`/api/3`) returns 503** for ~30–60s. By default `publish()` is synchronous: it waits out
that outage and confirms the result via `/api/publish/error` (a fresh `last_publish_time`
with `status: "Success"`), returning that body so you can read the published schema
immediately. It is always synchronous — during the migrate the whole appliance is down, so
there is nothing else to do but wait.

```{note}
**Validation errors are raised synchronously, before any migrate.** A field whose `type`
does not exist, or a `oneToMany` with no matching lookup on its target, comes back as an
{class}`~pyfsr.exceptions.APIError` (HTTP 400) on the PUT itself — its message is the
appliance's own (e.g. *"there is no lookup field present in 'alerts' module"*), so surface
it to the user. If the *async* publish fails instead, `publish()` raises
{class}`~pyfsr.exceptions.FortiSOARException` with the status from `/api/publish/error`; a
publish that never reports back raises `TimeoutError`.
```

## Discarding an unpublished draft

`discard_staging_draft` fires the same `DELETE` the editor's **Revert** button uses, and
additionally cleans up the module's view templates (which the UI's own revert leaves
orphaned):

```python
admin.discard_staging_draft("widgets")   # -> True
admin.get_view_templates("widgets")      # -> []   (cleaned up)
```

```{danger}
**There is no API path to delete a *published* module.** `discard_staging_draft` only
undoes an unpublished draft. If a module was ever published (its draft committed by *any*
publish on the appliance), the live module and its Postgres table remain, with no API to
remove them — that needs backend CLI/SQL. For a clean throwaway, **never publish it**;
then discarding the draft removes it entirely.
```

```{note}
**Auto-mirror appliances are the exception to "discarding removes it entirely."** On a
box that re-syncs staging into `model_metadatas` on every write (see the settings note
above), even a *never-explicitly-published* module has already been mirrored into
`model_metadatas` by its create/edit writes. There, `discard_staging_draft` removes the
staging draft but leaves a stale published **stub** (a `model_metadatas` row with no
physical table). Clearing that stub needs one `publish()` — which reconciles the mirror
and drops the orphaned row. So on auto-mirror boxes the throwaway recipe is
create → `discard_staging_draft` → one `publish()`, after which `is_published()` reads
`False` and `pending_changes()` is empty.
```
