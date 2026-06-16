# Module Schema Administration

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

## Inspecting existing schema (read-only)

```python
admin = client.modules_admin

admin.is_published("alerts")          # -> True
admin.is_published("nonexistentmod")  # -> False

pub = admin.get_published("alerts")
# {'uuid': 'f43192a7-d6ef-498c-8cd2-57521928e500', 'type': 'alerts',
#  'module': 'alerts', 'tableName': 'alerts'}  (+ 126 fields under 'attributes')

admin.get_field("alerts", "name")
# {'name': 'name', 'type': 'string', 'formType': 'text', 'searchable': True}
```

```{note}
`is_published()` reports presence in `model_metadatas`. A freshly created module is
**staging-only** until you publish, so it reads `False` until then.
```

## Building fields

{meth}`~pyfsr.api.modules_admin.ModulesAdminAPI.field` builds an attribute dict with
sane defaults. `db_type` is the storage type (`text`/`json`/`integer`/…); `form_type`
is the UI widget (defaults to `db_type`).

```python
admin.field("payload", db_type="json", form_type="json", label="Payload")
```

```json
{
  "name": "payload",
  "type": "json",
  "formType": "json",
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
exposes the full options surface:

```python
admin.field(
    "secret",
    label="API Secret",          # Field Title (name is the immutable API Key)
    editable=True,               # UI "Editable"  -> writeable
    searchable=False,            # Field Options row...
    grid_column=True,            # "Default Grid Column"
    encrypted=True,              # "Encrypted" (mutually exclusive with searchable)
    required=True,               # or a condition dict for "Required by condition"
    visibility=True,             # or a condition dict for "Visible by Condition"
    default_value="",
    tooltip="Stored encrypted",
    minlength=0, maxlength=1024, enable_range=True,   # Length Constraints
    bulk_edit=True,              # "Allow Bulk Edit" -> bulkAction.allow
)
```

### Picklist and relationship fields

Two builders cover the common rich field types:

```python
# single- or multi-select picklist, bound to a picklist list name
admin.picklist_field("severity", "AlertSeverity", grid_column=True)
admin.picklist_field("tags", "AlertType", multi=True)   # -> multiselectpicklist

# a many-to-many relationship to another module
admin.relationship_field("relatedalerts", "alerts", label="Related Alerts")
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
        admin.field("name", db_type="text", form_type="text", required=True, grid_column=True),
        admin.field("payload", db_type="text", form_type="textarea"),
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
admin.add_field("widgets", admin.field("severity", db_type="text", form_type="select"))
admin.set_field_type("widgets", "payload", db_type="json", form_type="json")

[(a["name"], a["type"], a["formType"]) for a in admin.get_staging("widgets")["attributes"]]
# [('name', 'text', 'text'), ('payload', 'json', 'json'), ('severity', 'text', 'select')]
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

By default `publish()` is synchronous: it tolerates the transient 5xx / "Decrypt
Database" / "Cleaning Up Old Backups" states the appliance returns mid-migrate, then
polls until reads succeed, so you can read the published schema immediately on return.
Pass `wait=False` for fire-and-forget.

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
