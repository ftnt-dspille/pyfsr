# Module Editor

Where {class}`~pyfsr.api.modules.ModulesAPI` (`client.modules`) is read-only
*discovery*, `client.modules_admin` ({class}`~pyfsr.api.modules_admin.ModulesAdminAPI`)
is the **write** surface for the Application/Module Editor -- create modules, add and
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
touches **staging only** -- nothing is live until you {meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.publish`,
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
has a rap sheet of heists. The fun part -- you only declare the link **once**;
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

# 2. The heists. The `crew` field is the link -- a many-to-many relationship to
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

`create_module` returns the created **staging** record -- the draft, not yet live
(`@type` is `StagingModelMetadata`). The call below is doctested against a real
capture of a throwaway module, so the return shape is exactly what the appliance
sends: the module identity plus its seeded `name` field. `create_view_templates`
and `add_to_nav` are turned off here to keep the example to the one write:

```{doctest}
>>> admin = demo_client().modules_admin
>>> mod = admin.create_module(
...     "doctestmod", label="Doctest Module", plural="Doctest Modules",
...     create_view_templates=False, add_to_nav=False,
... )
>>> mod["@type"]                       # the staging (draft) store, not model_metadatas
'StagingModelMetadata'
>>> mod["type"], mod["displayName"]
('doctestmod', '{{ name }}')
>>> mod["descriptions"]
{'singular': 'Doctest Module', 'plural': 'Doctest Modules'}
>>> [a["name"] for a in mod["attributes"]]   # the auto-seeded required field
['name']
```

That single `relationship_field` is the whole trick. Because the SDK keeps both
sides of a relationship valid, it auto-stages the **reverse field on `crew`** --
so each crew member gets a `heists` field listing every job they're on, without
you touching the `crew` module again:

```python
[a["name"] for a in admin.get_staging("crew")["attributes"]]
# ['alias', 'specialty', 'trustworthy', 'heists']   <-- 'heists' appeared on its own
```

Nothing is live yet -- both modules are staging-only drafts. Check what a publish
would commit, then commit it (remember: **publish is appliance-wide**). The
`pending_changes()` call below is doctested against a scoped overlay
(`pending_create_overlay`) that stages `crew` + `heists` without publishing -- the
exact post-`create_module`, pre-`publish` state:

```{doctest}
>>> from pyfsr._testing.client_captures import pending_create_overlay
>>> admin = demo_client(overrides=pending_create_overlay(["crew", "heists"])).modules_admin
>>> admin.pending_changes()
[PendingChange(module='crew', change='created'), PendingChange(module='heists', change='created')]
```

```python
admin.publish()   # backup + migrate; blocks ~30-60s while /api/3 is down
```

Now the tables exist and you can populate the caper. Create the crew, then a
heist that references them -- the link is just a list of record IRIs:

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

Because the reverse field exists, the relationship reads **both ways** for free --
ask a heist for its crew, or a crew member for their heists:

```python
client.records("heists").get(job["uuid"], relationships=True)["crew"]
# -> [{'alias': 'The Brains', ...}, {'alias': 'Light Fingers', ...}]

client.records("crew").get(danny["uuid"], relationships=True)["heists"]
# -> [{'codename': 'Operation Cannoli', ...}]
```

That's the full loop: **two `create_module` calls, one relationship, one
publish** -- and a bidirectional link you only had to describe once. The rest of
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

For a quick human-readable summary of any module's fields, use
`client.modules.describe()` (read-only) or `format_module()`:

```{doctest}
>>> d = client.modules.describe("alerts")
>>> (d["module"], d["field_count"])
('alerts', 4)
>>> [f["name"] for f in d["fields"]]
['name', 'description', 'severity', 'status']
>>> print(client.modules.format_module("alerts"))  # doctest: +SKIP
Module: Alert  (type=alerts, plural=alerts)
Fields: 4
  NAME                         TYPE               REQ  TITLE
  ...
  severity                     picklists               Severity  [picklist: Severity]
                                                               valid: Minimal, Low, Medium, High, Critical
```

```{note}
`is_published()` reports presence in `model_metadatas`. A freshly created module is
**staging-only** until you publish, so it reads `False` until then.
```

## Building fields

```{tip}
For the **full field-type catalogue** -- every display type, its storage type, properties,
and relationship/reverse-field semantics -- see {doc}`module-field-schema`. This section is a
quick start; that page is the authoring reference.
```

Prefer the **typed builders**, which set the storage `type` and `formType` (display type)
to a matching pair for you (e.g. a `datetime` field must store `integer`; a `text` field
must store `string`):

```{doctest}
>>> admin = demo_client().modules_admin
>>> f = admin.text_field("summary", area=True)
>>> (f.name, f.type, f.form_type)
('summary', 'string', 'textarea')
>>> f = admin.integer_field("score")
>>> (f.name, f.type, f.form_type)
('score', 'integer', 'integer')
>>> f = admin.datetime_field("detectedOn")
>>> (f.name, f.type, f.form_type)
('detectedOn', 'integer', 'datetime')
>>> f = admin.checkbox_field("isExternal")
>>> (f.name, f.type, f.form_type)
('isExternal', 'boolean', 'checkbox')
>>> f = admin.object_field("payload", label="Payload")
>>> (f.name, f.type, f.form_type)
('payload', 'object', 'object')
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
for scalar, lookup and picklist fields -- they show in the module's list/grid view without
opting each one in -- and **off** for `password`, `object`/`json`/`array` and collection
relationships (`manyToMany`/`oneToMany`), the types that are never grid columns in
practice. Override either way with `grid_column=True/False`:

```{doctest}
>>> f = admin.field(
...     "secret",
...     label="API Secret",          # Field Title (name is the immutable API Key)
...     editable=True,               # UI "Editable"  -> writeable
...     searchable=False,            # Field Options row...
...     grid_column=False,           # "Default Grid Column" -- text defaults visible; hide this one
...     encrypted=True,              # "Encrypted" (mutually exclusive with searchable)
...     required=True,               # or a condition dict for "Required by condition"
...     visibility=True,             # or a condition dict for "Visible by Condition"
...     default_value="",
...     tooltip="Stored encrypted",
...     minlength=0, maxlength=1024, enable_range=True,   # Length Constraints
...     bulk_edit=True,              # "Allow Bulk Edit" -> bulkAction.allow
... )
>>> (f.name, f.encrypted, f.searchable, f.grid_column)
('secret', True, False, False)
>>> (f.validation.required, f.validation.maxlength, f.bulk_action.allow)
(True, 1024, True)
```

The default picks a sensible `grid_column` per type, so most fields need no
override:

```{doctest}
>>> admin.password_field("apiKey").grid_column   # False -- password never grids
False
>>> admin.text_field("notes", grid_column=False).grid_column
False
>>> admin.text_field("summary").grid_column      # True -- scalar defaults visible
True
```

### Picklist and relationship fields

```{doctest}
>>> f = admin.picklist_field("severity", "AlertSeverity")
>>> (f.name, f.type, f.form_type, f.collection)
('severity', 'picklists', 'picklist', False)
>>> f = admin.picklist_field("tags", "AlertType", multi=True)
>>> (f.name, f.type, f.form_type, f.collection)
('tags', 'picklists', 'multiselectpicklist', True)
>>> f = admin.lookup_field("owner", "people", label="Owner")
>>> (f.name, f.type, f.form_type, f.collection)
('owner', 'people', 'lookup', False)
>>> f = admin.relationship_field("relatedalerts", "alerts", label="Related Alerts")
>>> (f.name, f.type, f.form_type, f.collection)
('relatedalerts', 'alerts', 'manyToMany', True)
```

```{note}
`add_field` keeps both sides of a relationship valid: it creates the reverse field on the
target when the platform won't (the `oneToMany` target lookup, the custom-inverse
`manyToMany` mirror). Pass `create_reverse=False` to manage the target side yourself. See
{doc}`module-field-schema` for the per-relationship rules and `reverse_field()` verification.
```

## Creating a module

`create_module` posts to staging and -- matching the in-product editor -- also creates
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
# ['detail', 'form', 'list']
```

Edit staged fields before publishing:

```python
admin.add_field("widgets", admin.email_field("reporter"))
admin.set_field_type("widgets", "payload", db_type="object", form_type="object")

[(a["name"], a["type"], a["formType"]) for a in admin.get_staging("widgets")["attributes"]]
# [('name', 'string', 'text'), ('payload', 'object', 'object'), ('reporter', 'string', 'email')]
```

`remove_field` is the inverse -- drops a field from staging (not live until you
publish):

```python
admin.remove_field("widgets", "reporter")                 # -> staging dict
admin.remove_field("widgets", "nonexistent", missing_ok=True)  # no error
```

`ensure_field` is the idempotent wrapper around `add_field` -- no-op if the
field already exists, otherwise adds it. Safe to re-run from a deploy script:

```python
admin.ensure_field("widgets", admin.email_field("reporter"))   # -> staging dict (first call)
admin.ensure_field("widgets", admin.email_field("reporter"))   # -> None (already exists)
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
# Re-read staging to confirm:
#   taggable=False  ownable=True  softDeleteable=True  displayName='{{ name }}'
```

`get_or_create_module` is the idempotent front door -- returns the existing
module's metadata with `created=False` if it already exists, otherwise creates
and optionally publishes it:

```python
meta, created = admin.get_or_create_module(
    "widgets", label="Widget", plural="Widgets",
    fields=[admin.text_field("name", required=True)],
    publish=True,       # publish immediately after creating (default)
)
created                 # True -- the wizard built it; False if it already existed
```

```{note}
**Auto-mirror appliances.** Some builds (e.g. with the dev-mode schema toggle on)
re-sync `staging_model_metadatas` into `model_metadatas` on *every* write -- so a staged
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
# [PendingChange(module='widgets', change='created')]
#   change is one of: 'created' | 'modified' | 'deleted'
```

An empty list means the appliance is fully published -- nothing for `publish()` to do.

A single illegally-named staged module or field (e.g. `9probe` or a field with a
space) makes the whole publish fail mid-migrate with a cryptic Postgres error that
doesn't name the offender. `find_invalid_drafts` finds them before you publish:

```python
admin.find_invalid_drafts()        # shallow: check module names only
# [InvalidDraft(module='9probe', uuid='...', problem='invalid module name')]

admin.find_invalid_drafts(deep=True)  # also check every field name
# []
```

## Publishing

```python
admin.publish()   # appliance-wide commit; blocks until the migrate cycle finishes
```

`PUT /api/publish` only *starts* the publish -- its response is `{"status": "started"}` --
and the backup + DB migrate then runs asynchronously, during which the **whole API
(`/api/3`) returns 503** for ~30-60s. By default `publish()` is synchronous: it waits out
that outage and confirms the result via `/api/publish/error` (a fresh `last_publish_time`
with `status: "Success"`), returning that body so you can read the published schema
immediately. It is always synchronous -- during the migrate the whole appliance is down, so
there is nothing else to do but wait.

```{note}
**Validation errors are raised synchronously, before any migrate.** A field whose `type`
does not exist, or a `oneToMany` with no matching lookup on its target, comes back as an
{class}`~pyfsr.exceptions.APIError` (HTTP 400) on the PUT itself -- its message is the
appliance's own (e.g. *"there is no lookup field present in 'alerts' module"*), so surface
it to the user. If the *async* publish fails instead, `publish()` raises
{class}`~pyfsr.exceptions.FortiSOARException` with the status from `/api/publish/error`; a
publish that never reports back raises `TimeoutError`.
```

## Reverting all staged changes

`revert()` is the nuclear option -- discard **every** pending staged change across
the whole appliance (not just one module like `discard_staging_draft`). No DB migrate,
so it returns immediately:

```python
admin.revert()   # every staged draft is gone; staging matches published again
```

⚠️ Appliance-wide: on a shared box, confirm nothing else is mid-edit first.

## Discarding an unpublished draft

`discard_staging_draft` fires the same `DELETE` the editor's **Revert** button uses, and
additionally cleans up the module's view templates (which the UI's own revert leaves
orphaned):

```python
admin.discard_staging_draft("widgets")   # -> True
admin.get_view_templates("widgets")      # -> []   (cleaned up)
```

For a clean throwaway module that was **never published**, this is all you need.

## Deleting a published module

`delete_module` is the API path the UI doesn't expose -- it discards the staging
draft, runs an appliance-wide publish, and the module disappears from both
`staging_model_metadatas` and `model_metadatas`:

```python
result = admin.delete_module("widgets", publish=True)
# {'module': 'widgets', 'detached': [], 'orphan_table': 'widgets',
#  'published': {...}, 'dropped_tables': None, 'nav_removed': None}
admin.is_published("widgets")     # False -- gone
```

```{warning}
**The physical Postgres tables are NOT dropped.** The base table and relationship
join tables remain as orphans. They are harmless unless you later create a module
reusing the same `tableName` -- then the leftover indexes collide (Postgres `42P07`)
and wedge that publish. Reclaim them with `drop_orphan_tables=True` (which needs
appliance SSH access) or a backend `DROP TABLE ... CASCADE`.
```

```{warning}
**Detach reverse relationships first.** If other modules have relationship fields
pointing at this one, the publish fails synchronously with "Attribute type '<module>'
does not exist as core or custom model metadata". Pass `detach_relationships=True`
to auto-remove them, or remove them yourself with `remove_field`. The method
**refuses** (listing the referrers) when referrers exist and `detach_relationships`
is left at its default `False`.
```
