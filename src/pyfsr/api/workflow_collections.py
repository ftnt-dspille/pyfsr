"""Workflow-collection CRUD (``/api/3/workflow_collections``).

A *workflow collection* is the container FortiSOAR groups playbooks (workflows) under -- the
top-level rows in **Automation → Playbooks**. This wraps the lifecycle operations so callers
(notably a playbook compiler/emitter) stop hand-rolling raw ``client.*`` calls and stepping on
the load-bearing gotchas below. Accessed as ``client.workflow_collections``.

Key behaviours that match the appliance (and differ from naive expectations):

- **create / update** POSTs or PUTs a **bare collection object** directly to
  ``/api/3/workflow_collections``. The nested ``workflows`` array is accepted inline.
- **upsert / bulk_upsert** hit the appliance's true re-push path and avoid the recycle-bin
  duplicate problem by restoring soft-deleted rows when needed.
- **delete** must send **no body** and ``$hardDelete=true&$showDeleted=true``. A ``{}`` body
  silently no-ops and leaks the collection.

Use :meth:`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export` to replay a
FortiSOAR export file (the ``{"type": "workflow_collections", "data": [...]}`` envelope produced
by the UI's Export button) -- it extracts the inner collection objects and posts each bare.
Pass ``replace=True`` to hard-delete any existing collection with the same uuid first.

Example::

    cols = client.workflow_collections.list()                 # all collections
    col  = client.workflow_collections.get("<uuid>")
    client.workflow_collections.create_collection("My Pack", workflows=[...])
    client.workflow_collections.upsert({...})                 # re-push one collection
    client.workflow_collections.create_collections([...])            # re-push many
    client.workflow_collections.import_export(data)           # replay an export dict
    client.workflow_collections.import_from_file("export.json")
    client.workflow_collections.import_from_yaml("pb.yaml")    # compile YAML then import
    client.workflow_collections.update("<uuid>", name="Renamed")
    client.workflow_collections.delete("<uuid>")              # hard delete, no recycle bin

``import_from_yaml`` (optional ``pyfsr[playbooks]`` compiler) authors a playbook
from YAML: it compiles the YAML to the same export envelope and replays it through
``import_export``. Use ``compile_yaml`` to compile without deploying.
"""

from __future__ import annotations

import json
import uuid as _uuid
from pathlib import Path
from typing import Any

from ..exceptions import ResourceNotFoundError
from ..models import ReusableBlock, WorkflowCollection
from ..pagination import extract_members
from ..records import RecordSet
from ..utils.validation import is_uuid
from .base import BaseAPI

_BASE = "/api/3/workflow_collections"
_REUSABLE_BLOCKS = "/api/3/workflow_groups"
# A hard delete must also reach already-recycled rows; together these skip the recycle bin.
_HARD_DELETE = {"$hardDelete": "true", "$showDeleted": "true"}


def _wf_fingerprint(wf: dict[str, Any]) -> str:
    """A stable hash of a workflow's shape -- name/active-flag plus each step's
    name, type, and arguments -- used to tell whether a live copy DIFFERS from
    the local one without drowning in uuid / timestamp / ordering noise.

    Steps are sorted by name so ordering does not register as a change; step
    ``stepType`` is normalised to a bare uuid (the live row carries an IRI, the
    compiled row a bare uuid or IRI) so the same step type on both sides matches.
    """
    import hashlib

    def _type(s: dict[str, Any]) -> str:
        t = s.get("stepType") or s.get("type") or ""
        return t.rsplit("/", 1)[-1] if isinstance(t, str) else str(t)

    steps = [
        {"name": s.get("name"), "type": _type(s), "args": s.get("arguments")}
        for s in sorted(wf.get("steps") or [], key=lambda x: str(x.get("name", "")))
    ]
    shape = {"name": wf.get("name"), "isActive": wf.get("isActive"), "steps": steps}
    return hashlib.sha256(json.dumps(shape, sort_keys=True, default=str).encode()).hexdigest()[:16]


class WorkflowCollectionsAPI(BaseAPI):
    """CRUD for playbook (workflow) collections."""

    def list(self, *, limit: int = 2147483647, relationships: bool = False) -> list[WorkflowCollection]:
        """List workflow collections (the ``hydra:member`` array).

        ``relationships=True`` adds ``$relationships=true`` so each collection's nested
        ``workflows`` come back inline (heavier; off by default). Returns typed,
        dict-compatible :class:`~pyfsr.models.WorkflowCollection` records.
        """
        params: dict[str, Any] = {"$limit": limit}
        if relationships:
            params["$relationships"] = "true"
        return [_as_collection(m) for m in extract_members(self.client.get(_BASE, params=params))]

    def list_reusable_blocks(self, *, limit: int = 2147483647) -> list[ReusableBlock]:
        """List **reusable playbook blocks** (``workflow_groups`` with ``reusable=true``).

        These are the saved, re-droppable step groups the playbook editor and the
        Configuration Export wizard's *Playbook Blocks* category work with. Returns
        typed, dict-compatible :class:`~pyfsr.models.ReusableBlock` records from
        ``GET /api/3/workflow_groups?reusable=true`` (live-verified 8.0).
        """
        params = {"reusable": "true", "$limit": limit}
        return [
            ReusableBlock.model_validate(m) for m in extract_members(self.client.get(_REUSABLE_BLOCKS, params=params))
        ]

    def get(self, uuid: str, *, relationships: bool = True) -> WorkflowCollection:
        """Fetch one collection by uuid. ``relationships=True`` (default) inlines its
        ``workflows`` -- the usual reason to fetch a single collection.

        Returns a typed, dict-compatible :class:`~pyfsr.models.WorkflowCollection`.
        """
        uuid = _require_uuid(uuid, "get")
        params = {"$relationships": "true"} if relationships else None
        return _as_collection(self.client.get(f"{_BASE}/{uuid}", params=params))

    def export_to_yaml(
        self,
        collection: str,
        *,
        db_path: str | Path | None = None,
    ) -> str:
        """Decompile a live collection into authored-style playbook YAML.

        The inverse of :meth:`import_from_yaml` -- pull a playbook collection off
        the appliance and get back the friendly YAML source, so live edits made
        in the UI can be captured into version control. ``collection`` is the
        collection's uuid, or its name (resolved against :meth:`list`).

        Catalog resolution is seamless (warmed from this client) so connector,
        team, and picklist IRIs render back as friendly names -- including custom
        connectors like ``code-runner``. Pass ``db_path`` to use a specific
        pre-warmed catalog instead.

        Requires the compiler extra (``pip install "pyfsr[playbooks]"``).
        """
        from ..authoring import decompile_playbook_yaml

        coll = self._resolve_collection(collection)
        envelope = {"type": "workflow_collections", "data": [coll.to_dict()]}
        return decompile_playbook_yaml(envelope, client=self.client, db_path=db_path)

    def _resolve_collection(self, collection: str) -> WorkflowCollection:
        """Resolve a collection by uuid, else by exact name via :meth:`list`."""
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError("export_to_yaml() requires a collection uuid or name")
        ident = collection.strip()
        if is_uuid(ident):
            return self.get(ident, relationships=True)
        # Resolve the NAME against a cheap listing, then fetch only the one
        # collection with relationships. Listing with `relationships=True`
        # inlines every workflow of every collection so that all but one can be
        # thrown away: on a box with 209 collections that is ~0.5-1s each, or
        # 105-240s, against a 30s read timeout -- the call cannot complete, and
        # it gets slower as content grows. Name resolution needs `name` and
        # `uuid` and nothing else.
        matches = [c for c in self.list(relationships=False) if (c.get("name") or "") == ident]
        if not matches:
            raise ResourceNotFoundError(
                f"no workflow collection named {ident!r} (pass its uuid if the name is ambiguous)",
                None,
            )
        if len(matches) > 1:
            raise ValueError(f"{len(matches)} collections named {ident!r}; pass the uuid to disambiguate")
        # Same path the uuid branch takes -- the caller needs `workflows` inlined.
        return self.get(matches[0].get("uuid"), relationships=True)

    def create_collection(
        self,
        name: str,
        *,
        description: str = "",
        visible: bool = True,
        workflows: list[dict[str, Any]] | None = None,
        uuid: str | None = None,
        record_tags: list[str] | None = None,
        image: str | None = None,
    ) -> WorkflowCollection:
        """Create a collection, optionally with workflows (``POST /api/3/workflow_collections``).

        Nested ``workflows`` (full Workflow objects with ``steps``/``routes``) are accepted
        inline. ``uuid`` is generated if omitted. Returns the created collection record
        as a typed :class:`~pyfsr.models.WorkflowCollection`.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create_collection() requires a non-empty collection name")
        collection = {
            "@type": "WorkflowCollection",
            "name": name,
            "description": description,
            "visible": visible,
            "image": image,
            "uuid": uuid or str(_uuid.uuid4()),
            "recordTags": list(record_tags or []),
            "workflows": list(workflows or []),
        }
        return _as_collection(self.client.post(_BASE, data=collection))

    def upsert(self, data: dict[str, Any]) -> WorkflowCollection:
        """Insert-or-update one collection via ``POST /api/3/upsert/workflow_collections``.

        FortiSOAR matches on the collection's natural key, restoring a soft-deleted row
        instead of creating a duplicate. This is the safest write path when a collection
        may already exist in the recycle bin. Returns the persisted
        :class:`~pyfsr.models.WorkflowCollection`.
        """
        return _as_collection(self.client.post("/api/3/upsert/workflow_collections", data=data))

    def create_collections(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Create or re-push many collections (``POST /api/3/bulkupsert/workflow_collections``)."""
        return self.client.post("/api/3/bulkupsert/workflow_collections", data=rows)

    # Keys present in FSR export payloads that must not be forwarded on import.
    # ``@context`` is the main offender: its presence tells the API layer this is an
    # existing-resource reference and routes the POST into an update path instead of a
    # create -- producing a "null value in column name" constraint error. The audit
    # timestamps (``createDate``/``modifyDate``) are server-assigned and rejected on
    # write; the appliance ignores ``id``/``deletedAt``/``importedBy`` but we strip
    # them for cleanliness too.
    _STRIP_KEYS: frozenset[str] = frozenset({"@context", "createDate", "modifyDate", "deletedAt", "importedBy", "id"})

    @classmethod
    def _clean_item(cls, obj: Any) -> Any:
        """Recursively strip server-generated / Hydra-meta keys from an export payload."""
        if isinstance(obj, dict):
            return {k: cls._clean_item(v) for k, v in obj.items() if k not in cls._STRIP_KEYS}
        if isinstance(obj, list):
            return [cls._clean_item(i) for i in obj]
        return obj

    def import_export(
        self,
        data: dict[str, Any],
        *,
        replace: bool = False,
    ) -> list[WorkflowCollection]:
        """Import a FortiSOAR export envelope, preserving original UUIDs and structure.

        Accepts the ``{"type": "workflow_collections", "data": [...]}`` envelope produced
        by the UI's Export button. Each ``WorkflowCollection`` object in ``data["data"]``
        (with its nested ``workflows``) is posted as a bare object to
        ``POST /api/3/workflow_collections`` -- mirroring the second call the UI makes
        during an import. Returns a list with one response dict per imported collection.

        ``replace=True`` hard-deletes any existing collection whose uuid matches an item
        in the export before re-importing it (the UI's "Replace existing playbook
        collection" flow: ``DELETE /api/3/workflow_collections/<uuid>?$hardDelete=true``
        then ``POST /api/3/workflow_collections``). Without ``replace=True`` the POST
        raises a ``409 UniqueConstraintViolationException`` if the collection already
        exists.

        Raises:
            ValueError: if ``data`` is not a dict or is missing the ``"data"`` key.

        Returns:
            List of created collection records (one per entry in ``data["data"]``).
        """
        if not isinstance(data, dict):
            raise ValueError("import_export() expects a dict (the export envelope)")
        if "data" not in data:
            raise ValueError(
                "import_export() expects an export envelope with a 'data' key; got keys: " + ", ".join(sorted(data))
            )
        if replace:
            import warnings

            warnings.warn(
                "import_export(replace=True) hard-deletes the whole collection before "
                "re-POSTing it -- a failure (e.g. a workflow uuid that lives in another "
                "collection) leaves the collection GONE, and it discards live UI edits. "
                "Prefer workflow_collections.deploy() (in-place upsert, backup, ownership "
                "pre-flight) for deploys and restores.",
                DeprecationWarning,
                stacklevel=2,
            )
        results: list[WorkflowCollection] = []
        for raw_col in data["data"]:
            col = self._clean_item(raw_col)
            if replace:
                col_uuid = col.get("uuid")
                if col_uuid and self.exists(col_uuid):
                    self._make_playbooks_public(col_uuid)
                    self.delete(col_uuid)
            results.append(_as_collection(self.client.post(_BASE, data=col)))
        return results

    def import_from_file(
        self,
        path: str | Path,
        *,
        replace: bool = False,
    ) -> list[WorkflowCollection]:
        """Load a FortiSOAR export JSON file and import it via :meth:`import_export`.

        ``path`` points to a ``*.json`` file produced by the UI's Export button. Pass
        ``replace=True`` to hard-delete any collection whose uuid already exists before
        re-creating it (the "Replace existing playbook collection" UI flow).

        Raises:
            FileNotFoundError: if ``path`` does not exist.
            ValueError: if the file is not valid JSON or lacks the ``"data"`` key.

        Returns:
            List of created collection records.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"export file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"export file is not valid JSON: {path}: {exc}") from exc
        return self.import_export(data, replace=replace)

    def import_export_zip(
        self,
        zip_path: str | Path,
        *,
        replace: bool = True,
        strip_stale: bool = True,
        create_records: bool = True,
        create_modules: bool = False,
        grant_modules_to: list[str] | str | None = None,
        patch_picklists: bool = False,
    ) -> dict[str, Any]:
        """Import a FortiSOAR UI-export zip bundle in one call.

        This is the programmatic equivalent of the FortiSOAR UI's **Import
        Wizard** (Settings → Application Editor → Import Wizard) -- the flow that
        consumes an Export-button ``.zip``.

        The UI's Export button produces a zip with:
          ``info.json`` + ``modules/<name>/mmd.json`` + ``playbooks/<collection>/*.json``
          + ``records/<module>/*.json`` + ``exportTemplates/*.json``.

        This method handles the full unpack+build+import cycle:

          1. Extracts the zip and builds the ``{"type": "workflow_collections", ...}``
             envelope from ``playbooks/<collection>/`` dirs.
          2. When ``create_modules=True``, creates any custom module the bundle
             defines (from ``modules/<name>/mmd.json``) that isn't already on
             the box, via
             :meth:`~pyfsr.api.modules_admin.ModulesAdminAPI.get_or_create_module_from_metadata`,
             then publishes. Runs **first** so bundled records have a module to
             land in. Idempotent -- a module a solution pack already provides is
             left untouched.
          3. When ``strip_stale=True``, strips server-managed fields
             (``createdAlertsID``, ``createUser``, ``modifyUser``, etc.) from
             bundled records before creating them.
          4. When ``create_records=True``, creates bundled records
             (from ``records/<module>/``) that don't already exist.
          5. When ``patch_picklists=True``, scans all playbook steps for
             hardcoded ``/api/3/picklists/`` IRIs and replaces them with
             Jinja ``picklist`` filter expressions that resolve dynamically
             at runtime -- eliminating the #1 portability bug.
          6. Imports the (optionally patched) envelope via :meth:`import_export`
             with ``replace=True`` (hard-deletes any existing collection with
             the same uuid first).

        .. note::
           ``create_modules`` imports the **module schema** (all attributes and
           flags, posted verbatim from ``mmd.json``). Bundle-level picklists and
           non-default view-template layouts are not yet imported -- for those,
           install the source solution pack or add the picklists first.

        Args:
            zip_path: path to the ``.zip`` file from the UI Export button.
            replace: hard-delete any existing collection with the same uuid
                before re-creating (default ``True`` -- matches the UI's
                "Replace existing" flow).
            strip_stale: strip server-managed fields from bundled records
                (default ``True``).
            create_records: create bundled records that don't already exist
                (default ``True``).
            create_modules: create custom modules the bundle defines (from
                ``modules/<name>/mmd.json``) that aren't already on the box,
                then publish (default ``False``). Idempotent.
            grant_modules_to: role name(s) to grant full access on any module
                created by ``create_modules``. A module's metadata carries no
                RBAC grants, so without this the imported module is inaccessible
                (403) -- pass the role(s) that should own the imported content
                (and that lets the subsequent record import land).
            patch_picklists: replace hardcoded picklist IRIs with Jinja
                ``picklist`` filter expressions (default ``False``). When
                ``True``, calls :meth:`~pyfsr.api.picklists.PicklistsAPI.reverse_resolve`
                on each IRI and builds a ``{{ "Name" | picklist("Value", "@id") }}``
                expression.

        Returns:
            A dict with keys ``collections`` (list of created collections),
            ``modules_created`` (list of module type-names newly created, or
            empty), ``records_created`` (list of created record IRIs),
            and ``picklists_patched`` (int -- number of IRIs replaced, or 0).

        Example::

            result = client.workflow_collections.import_export_zip(
                "~/Downloads/My Scenario.zip",
                patch_picklists=True,
            )
            print(result["collections"][0]["name"])
        """
        import tempfile
        import zipfile

        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(f"export zip not found: {zip_path}")

        # 1. Extract
        tmp_dir = Path(tempfile.mkdtemp(prefix="fsr_export_"))
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)
        # Find the top-level dir inside the zip (usually "<export name>/")
        children = [p for p in tmp_dir.iterdir() if p.is_dir()]
        export_dir = children[0] if children else tmp_dir

        # 2. Build envelope from playbooks/ (optional -- a bundle may carry only
        #    modules and/or records, e.g. an Export-Wizard module backup).
        pb_dir = export_dir / "playbooks"
        collections = []
        for cd in sorted(pb_dir.iterdir()) if pb_dir.exists() else []:
            if not cd.is_dir():
                continue
            meta_path = cd / "collection.metadata.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["workflows"] = [
                json.loads(pf.read_text(encoding="utf-8"))
                for pf in sorted(cd.glob("*.json"))
                if pf.name != "collection.metadata.json"
            ]
            collections.append(meta)
        envelope = {"type": "workflow_collections", "data": collections}

        result: dict[str, Any] = {
            "collections": [],
            "modules_created": [],
            "records_created": [],
            "picklists_patched": 0,
        }

        # 2b. Create custom modules the bundle defines (before records, which
        #     need a module to land in). Idempotent -- skips modules already on
        #     the box (e.g. provided by a solution pack).
        if create_modules:
            modules_dir = export_dir / "modules"
            if modules_dir.exists():
                for module_dir in sorted(modules_dir.iterdir()):
                    mmd_path = module_dir / "mmd.json"
                    if not mmd_path.exists():
                        continue
                    mmd = json.loads(mmd_path.read_text(encoding="utf-8"))
                    _meta, created = self.client.modules_admin.get_or_create_module_from_metadata(
                        mmd, grant_to=grant_modules_to
                    )
                    if created:
                        result["modules_created"].append(mmd.get("type") or module_dir.name)

        # 3. Create bundled records
        if create_records:
            records_dir = export_dir / "records"
            if records_dir.exists():
                _stale_fields = frozenset(
                    {
                        "createdAlertsID",
                        "createUser",
                        "modifyUser",
                        "createDate",
                        "modifyDate",
                    }
                )
                from ..query import Query

                for module_dir in sorted(records_dir.iterdir()):
                    if not module_dir.is_dir():
                        continue
                    module = module_dir.name
                    for rec_file in sorted(module_dir.glob("*.json")):
                        raw = json.loads(rec_file.read_text(encoding="utf-8"))
                        if not isinstance(raw, list):
                            raw = [raw]
                        for rec in raw:
                            if strip_stale:
                                rec = {k: v for k, v in rec.items() if k not in _stale_fields}
                            # Check if record already exists (by title or uuid)
                            rs = self.client.records(module)
                            title = rec.get("title") or rec.get("name")
                            exists = False
                            if title:
                                try:
                                    existing = rs.query(Query().eq("title", title).limit(1))
                                    members = existing.members if hasattr(existing, "members") else []
                                    if members:
                                        exists = True
                                except Exception:
                                    pass
                            if not exists:
                                rec.pop("@type", None)
                                try:
                                    created = rs.create(rec, resolve_picklists=False)
                                    iri = created.get("@id") if isinstance(created, dict) else None
                                    result["records_created"].append(iri or str(rec_file))
                                except Exception:
                                    pass  # record creation is best-effort

        # 4. Patch hardcoded picklist IRIs
        if patch_picklists:
            patched_count = self._patch_picklists_in_envelope(envelope)
            result["picklists_patched"] = patched_count

        # 5. Import (only if the bundle actually carried playbook collections)
        if collections:
            created = self.import_export(envelope, replace=replace)
            result["collections"] = created

        # Cleanup temp dir
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)

        return result

    def _patch_picklists_in_envelope(self, envelope: dict[str, Any]) -> int:
        """Replace hardcoded ``/api/3/picklists/`` IRIs in playbook steps with
        Jinja ``picklist`` filter expressions. Returns the count of IRIs patched.

        Scans all step argument string values for ``/api/3/picklists/<uuid>``
        patterns, reverse-resolves each to ``(picklist_name, item_value)``, and
        replaces the literal IRI with ``{{ "Name" | picklist("Value", "@id") }}``.
        Only patches string values -- dict/list structures are left intact.
        """
        import re

        _IRI_RE = re.compile(r"/api/3/picklists/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

        patched = 0
        for col in envelope.get("data", []):
            for wf in col.get("workflows", []):
                for step in wf.get("steps", []):
                    if not isinstance(step, dict):
                        continue
                    args = step.get("arguments")
                    if not isinstance(args, dict):
                        continue
                    for key, val in list(args.items()):
                        if not isinstance(val, str):
                            continue
                        # Find all picklist IRIs in this string value
                        matches = _IRI_RE.findall(val)
                        if not matches:
                            continue
                        new_val = val
                        for iri in matches:
                            try:
                                info = self.client.picklists.reverse_resolve(iri)
                            except Exception:
                                info = None
                            if info:
                                expr = self.client.picklists.jinja_picklist_expr(info["picklist"], info["itemValue"])
                                new_val = new_val.replace(iri, expr)
                                patched += 1
                        if new_val != val:
                            args[key] = new_val
        return patched

    def compile_yaml(
        self,
        source: str | Path,
        *,
        db_path: str | Path | None = None,
        refresh_catalog: bool = True,
        lax_codes: set | None = None,
    ):
        """Compile playbook YAML into the FortiSOAR import envelope.

        ``source`` is either YAML text or a path (``str``/``Path``) to a ``.yaml``/
        ``.yml`` file. Returns a :class:`~pyfsr.authoring.CompiledPlaybook` whose
        ``fsr_json`` is ready for :meth:`import_export`; inspect ``ok``/``errors``/
        ``warnings`` before deploying.

        ``refresh_catalog`` (default ``True``) re-warms the local reference
        catalog from **this** appliance before compiling, so connector / operation
        / team / picklist tokens resolve against what is *currently* installed --
        including a connector you just imported that the cached catalog has never
        seen. Without a fresh warm, a connector step compiles with no
        ``name``/``version``/``operationTitle`` and the playbook editor renders it
        as "undefined". Set it to ``False`` to skip the network round-trip and
        compile offline (against ``db_path`` if given, else the packaged slim
        catalog) -- faster, but it won't know about connectors added since the last
        warm. ``db_path`` always wins: an explicit catalog is used verbatim with no
        warm regardless of this flag.

        ``lax_codes`` downgrades the given diagnostic codes from error to warning
        so they don't block emission -- accepts the friendly code string
        (``{"unknown_param"}``), the enum name, or the ``ErrorCode`` enum. Use it
        for known false-positives (e.g. a conditional connector param the catalog
        can't model) when you've verified the value is valid at runtime.

        Requires the optional compiler -- install with ``pip install
        "pyfsr[playbooks]"`` (raises
        :class:`~pyfsr.authoring.PlaybooksExtraNotInstalled` otherwise).
        """
        from ..authoring import compile_playbook_yaml

        text = _read_yaml_source(source)
        # db_path pins a catalog (no warm); otherwise refresh_catalog decides
        # whether to warm from this client (True) or compile offline (False).
        client = self.client if (refresh_catalog and db_path is None) else None
        return compile_playbook_yaml(text, client=client, db_path=db_path, lax_codes=lax_codes)

    def import_from_yaml(
        self,
        source: str | Path,
        *,
        replace: bool = False,
        db_path: str | Path | None = None,
        strict_warnings: bool = False,
        refresh_catalog: bool = True,
        lax_codes: set | None = None,
    ) -> list[WorkflowCollection]:
        """Compile playbook YAML and import the result onto the appliance.

        Compiles ``source`` (YAML text or a ``.yaml`` path) via :meth:`compile_yaml`
        then hands the envelope to :meth:`import_export` -- the same write path the
        UI's import uses, with its recycle-bin and clean-key handling.

        ``replace=True`` hard-deletes any existing collection whose uuid matches
        before re-creating it. ``strict_warnings=True`` treats compiler warnings as
        blocking. ``db_path`` overrides the reference catalog.

        ``refresh_catalog`` (default ``True``, forwarded to :meth:`compile_yaml`)
        re-warms the local catalog from this appliance before compiling so a
        just-imported connector is known and connector steps don't render as
        "undefined" in the editor. Set ``False`` to compile offline (skip the warm)
        when you know the cached catalog is current. ``lax_codes`` (forwarded too)
        downgrades specific diagnostic codes from error to warning so a known
        false-positive doesn't block the import.

        Raises:
            ValueError: if compilation produces blocking errors (or warnings when
                ``strict_warnings`` is set).
            pyfsr.authoring.PlaybooksExtraNotInstalled: if the compiler extra is
                not installed.

        Returns:
            List of created collection records (one per compiled collection).
        """
        result = self.compile_yaml(source, db_path=db_path, refresh_catalog=refresh_catalog, lax_codes=lax_codes)
        blocking = list(result.blocking)
        if strict_warnings:
            blocking += result.warnings
        if blocking or not result.fsr_json:
            from ..authoring import format_diagnostic

            detail = "; ".join(format_diagnostic(d) for d in blocking) or "no envelope produced"
            raise ValueError(f"playbook YAML failed to compile: {detail}")
        return self.import_export(result.fsr_json, replace=replace)

    def _live_workflows_by_uuid(self, uuids: list[str]) -> dict[str, dict[str, Any]]:
        """Return the live workflow rows for the given uuids, keyed by uuid.

        Uses ``POST /api/query/workflows`` rather than ``GET /workflows/<uuid>``:
        a bare GET 404s for some framework playbooks (verified live), while the
        query endpoint returns them reliably. Rows carry ``collection`` so the
        caller can check cross-collection ownership.
        """
        if not uuids:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(uuids), 200):
            chunk = uuids[i : i + 200]
            body = {"logic": "AND", "filters": [{"field": "uuid", "operator": "in", "value": chunk}], "limit": 500}
            resp = self.client.post("/api/query/workflows", data=body)
            for m in extract_members(resp):
                out[m["uuid"]] = m
        return out

    def deploy(
        self,
        data: dict[str, Any],
        *,
        prune: bool = False,
        backup_dir: str | Path | None = None,
        dry_run: bool = False,
        overwrite_changed: bool = False,
    ) -> dict[str, Any]:
        """Deploy a collection **non-destructively**, mirroring the editor's save.

        This is the safe replacement for ``import_export(replace=True)``. That
        destructive path hard-deletes the entire collection (purging every
        workflow, bypassing the recycle bin, discarding any live UI edit) and
        then re-POSTs it atomically -- so a single failure (e.g. one workflow
        uuid that already lives in another collection) leaves the collection
        gone. ``deploy`` never deletes the collection and never uses
        ``replace``: it updates each workflow **in place** via
        :meth:`~pyfsr.api.playbooks.PlaybooksAPI.upsert_playbooks` (the same
        PUT-scalars / PUT-or-POST-steps / delete-removed-steps flow the editor
        bundle runs), so uuids, routes, and collection membership survive.

        Safety steps, in order:

        1. **Resolve the target collection by NAME** on the box (falling back to
           the envelope uuid). A collection created by an earlier tool may carry
           a different uuid than the compiler derives; matching by name finds the
           real one so ownership checks and workflow membership use it.
        2. **Back up** the live collection (if it exists) to ``backup_dir`` as a
           re-importable envelope, before any change.
        3. **Ownership pre-flight**: if any workflow uuid already lives in a
           *different* collection, raise ``ValueError`` listing them -- deploying
           would collide on the uuid. Reconcile first (this is exactly the fault
           that can wipe a collection under the old ``replace`` path).
        4. **Divergence guard (directional)**: the YAML being ahead of the box
           (steps you added/edited in source) is the normal update case and is
           allowed. The guarded case is the box being AHEAD -- a live workflow
           that has STEPS the YAML does not (a UI edit not captured in source).
           Because the in-place upsert deletes live steps absent from the new
           definition, that edit would be lost, so the deploy raises
           ``ValueError`` and changes nothing unless ``overwrite_changed=True``.
           Reconcile by exporting the live collection into the YAML first. (The
           backup from step 2 makes an intentional overwrite recoverable.)
        5. **Upsert** each workflow in place (create if new, update if present).
        6. **Prune** orphans (live workflows absent from ``data``) only when
           ``prune=True`` -- and only via a soft (recycle-bin) delete.

        Args:
            data: an export envelope ``{"type": "workflow_collections", "data": [col]}``
                (e.g. from :meth:`compile_yaml` / the compiler's ``fsr_json``).
            prune: soft-delete live workflows that are absent from ``data``.
            backup_dir: directory to write the pre-change backup into (created if
                missing). ``None`` skips the backup (not recommended for --apply).
            dry_run: compute and return the plan without changing anything.
            overwrite_changed: proceed even when the box is ahead -- a live
                workflow has steps the YAML lacks. Default ``False`` fails closed
                so a live UI edit is never deleted unnoticed; ``True`` deletes the
                box-only steps to match the source.

        Returns:
            dict: a report with keys ``collection``, ``collection_uuid``,
            ``backup``, ``created``, ``updated``, ``unchanged``, ``pruned``,
            ``new``, ``changed``, ``diverged``, ``orphans``, ``dry_run``.

        Raises:
            ValueError: if ``data`` is malformed, or a workflow uuid lives in
                another collection (ownership conflict).
        """
        if not isinstance(data, dict) or not data.get("data"):
            raise ValueError("deploy() expects an export envelope with a non-empty 'data' list")
        col = self._clean_item(data["data"][0])
        col_name = col.get("name") or ""
        local_wfs = {w["uuid"]: w for w in (col.get("workflows") or [])}

        # 1) resolve the real target collection by name (fall back to envelope uuid)
        target_uuid = col.get("uuid")
        target_live = None
        try:
            resolved = self._resolve_collection(col_name) if col_name else None
            if resolved is not None:
                target_live = resolved
                target_uuid = resolved.uuid if hasattr(resolved, "uuid") else resolved.get("uuid")
        except Exception:
            target_live = None  # not found by name -> fresh create under envelope uuid

        # 2) backup the live collection before any change
        backup_path = None
        if target_live is not None and backup_dir is not None:
            from datetime import datetime, timezone

            bdir = Path(backup_dir)
            bdir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_name = (col_name or target_uuid).replace("/", "_").replace(" ", "_")
            backup_path = bdir / f"{safe_name}-{stamp}.json"
            live_dict = target_live.to_dict() if hasattr(target_live, "to_dict") else target_live
            backup_path.write_text(
                json.dumps({"type": "workflow_collections", "data": [live_dict]}, default=str, indent=2)
            )

        # 3) ownership pre-flight -- any local uuid living in a DIFFERENT collection?
        live_by_uuid = self._live_workflows_by_uuid(list(local_wfs))
        misowned = []
        for u, row in live_by_uuid.items():
            coll = row.get("collection")
            coll_u = coll.rsplit("/", 1)[-1] if isinstance(coll, str) else (coll or {}).get("uuid")
            if coll_u and coll_u != target_uuid:
                misowned.append((local_wfs[u].get("name"), u, coll_u))
        if misowned:
            lines = "\n".join(f"    - {n!r} ({u}) is in collection {c}" for n, u, c in misowned)
            raise ValueError(
                "deploy aborted -- these workflows already live in a DIFFERENT collection "
                f"(deploying would collide on their uuid):\n{lines}\n"
                "  Reconcile first: move them into the target collection, or remove them "
                "from this source."
            )

        # diff: compare each local workflow against its LIVE copy. The divergence
        # that matters is DIRECTIONAL. The YAML being ahead of the box (new/edited
        # steps we are shipping) is the normal update case -- allowed. The
        # dangerous case is the box being ahead: the live playbook has STEPS the
        # YAML does not, because upsert_playbooks deletes live steps absent from
        # the new definition. That is a live edit not captured in source, so it
        # must be reconciled into the YAML first -- we fail closed on it.
        live_members = []
        if target_live is not None:
            live_members = getattr(target_live, "to_dict", lambda: target_live)().get("workflows") or []
        live_by_uuid_full = {w["uuid"]: w for w in live_members}

        def _step_ids(wf: dict[str, Any]) -> set[str]:
            return {s.get("uuid") for s in (wf.get("steps") or []) if s.get("uuid")}

        new, changed, unchanged = [], [], []
        diverged: list[tuple[str, list[str]]] = []  # (workflow, [box-only step names])
        for u, w in local_wfs.items():
            if u not in live_by_uuid_full:
                new.append(w.get("name"))
                continue
            live = live_by_uuid_full[u]
            local_ids = _step_ids(w)
            box_only = [
                s.get("name") or s.get("uuid")
                for s in (live.get("steps") or [])
                if s.get("uuid") and s.get("uuid") not in local_ids
            ]
            if box_only:  # box has steps the YAML lacks -> upsert would delete them
                diverged.append((w.get("name"), sorted(str(b) for b in box_only)))
            if _wf_fingerprint(w) != _wf_fingerprint(live):
                changed.append(w.get("name"))
            else:
                unchanged.append(w.get("name"))
        orphans = [(w["uuid"], w.get("name")) for w in live_members if w["uuid"] not in local_wfs]

        report: dict[str, Any] = {
            "collection": col_name,
            "collection_uuid": target_uuid,
            "backup": str(backup_path) if backup_path else None,
            "new": sorted(n for n in new if n),
            "changed": sorted(n for n in changed if n),  # live differs (args/steps) -- informational
            "unchanged": sorted(n for n in unchanged if n),
            # box-ahead: the live workflow has steps the YAML does not -> update source first
            "diverged": {n: steps for n, steps in sorted(diverged) if n},
            "orphans": sorted(n or u for u, n in orphans),
            "created": [],
            "updated": [],
            "pruned": [],
            "dry_run": dry_run,
        }
        if dry_run:
            return report

        # divergence guard (directional): fail closed only when the BOX has steps
        # the YAML lacks -- deploying would delete a live edit not captured in
        # source. YAML-ahead changes (our new update) are allowed through.
        if diverged and not overwrite_changed:
            lines = "\n".join(f"    - {n}: box-only steps {steps}" for n, steps in report["diverged"].items())
            where = f" A backup of the live state is at {backup_path}." if backup_path else ""
            raise ValueError(
                "deploy aborted -- the live box copy of these workflows has STEPS that "
                "your source YAML does NOT, so deploying would DELETE them (a live edit not "
                f"captured in source):\n{lines}\n"
                "  Update your source first (export the live collection to YAML and merge), "
                f"or pass overwrite_changed=True to delete them deliberately.{where}"
            )

        # 4) ensure the collection exists, then upsert workflows in place
        if target_live is None:
            self.create_collection(
                col_name,
                description=col.get("description", ""),
                visible=col.get("visible", True),
                uuid=target_uuid,
            )
        for w in local_wfs.values():
            w["collection"] = f"/api/3/workflow_collections/{target_uuid}"
        res = self.client.playbooks.upsert_playbooks(list(local_wfs.values()))
        report["created"] = res.get("created", [])
        report["updated"] = res.get("updated", [])

        # 5) prune orphans (soft delete -> recycle bin, recoverable)
        if prune and orphans:
            for u, _n in orphans:
                try:
                    self.client.playbooks.delete(u, hard=False)
                    report["pruned"].append(u)
                except Exception:
                    pass
        return report

    def restore(self, uuid: str) -> WorkflowCollection:
        """Restore a soft-deleted collection from the recycle bin.

        This mirrors :meth:`pyfsr.records.RecordSet.restore` but keeps the collection-specific
        API self-contained. Returns the restored :class:`~pyfsr.models.WorkflowCollection`.
        """
        return _as_collection(RecordSet(self.client, "workflow_collections").restore(uuid, raw=True))

    def exists(self, uuid: str) -> bool:
        """Return True if a collection with ``uuid`` exists on the appliance.

        Useful as a pre-flight check before :meth:`import_from_file` to avoid
        re-importing a collection that is already present.
        """
        if not isinstance(uuid, str) or not is_uuid(uuid.strip()):
            raise ValueError(f"exists() requires a valid uuid, got {uuid!r}")
        try:
            self.get(uuid.strip(), relationships=False)
            return True
        except Exception:
            return False

    def update(self, uuid: str, **fields: Any) -> WorkflowCollection:
        """Partially update a collection (``PUT``); pass only the keys you want changed
        (e.g. ``name=...``, ``visible=False``, ``description=...``).

        Returns the updated :class:`~pyfsr.models.WorkflowCollection`.
        """
        uuid = _require_uuid(uuid, "update")
        if not fields:
            raise ValueError("update() requires at least one field to change")
        return _as_collection(self.client.put(f"{_BASE}/{uuid}", data=fields))

    def _make_playbooks_public(self, col_uuid: str) -> None:
        """Set every private playbook in a collection public, so it can be deleted.

        The appliance refuses to delete a collection that still contains private
        playbooks ("make all playbooks ... public"). A ``replace`` import has to
        clear that first. Best-effort: a collection with no private playbooks is
        a no-op, and individual update failures are swallowed so the subsequent
        delete still gets its chance to surface the real error.
        """
        try:
            detail = self.get(col_uuid.strip(), relationships=True)
        except Exception:  # noqa: BLE001 -- let delete() report the real problem
            return
        for wf in detail.get("workflows") or []:
            if not wf.get("isPrivate"):
                continue
            wf_uuid = wf.get("uuid")
            if not wf_uuid:
                continue
            try:
                self.client.put(f"/api/3/workflows/{wf_uuid}", data={"isPrivate": False, "owners": []})
            except Exception:  # noqa: BLE001 -- best-effort; delete() will report if this mattered
                pass

    def delete(self, uuid: str, *, hard: bool = True) -> None:
        """Delete a collection. ``hard=True`` (default) bypasses the recycle bin.

        Sends **no request body** -- the appliance silently no-ops a delete with a ``{}``
        body and leaks the collection, so this never passes one. ``hard=False`` does a soft
        (recycle-bin) delete.
        """
        uuid = _require_uuid(uuid, "delete")
        params = dict(_HARD_DELETE) if hard else None
        self.client.delete(f"{_BASE}/{uuid}", params=params)


def _as_collection(resp: Any) -> WorkflowCollection:
    """Coerce a raw collection response into a typed, dict-compatible record."""
    return WorkflowCollection.model_validate(resp if isinstance(resp, dict) else {"result": resp})


def _require_uuid(uuid: str, op: str) -> str:
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError(f"{op}() requires a non-empty collection uuid")
    return uuid.strip()


def _read_yaml_source(source: str | Path) -> str:
    """Return YAML text from ``source`` -- a ``Path``, a ``*.yaml``/``*.yml`` path
    string (read from disk), or raw YAML text (returned as-is)."""
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if isinstance(source, str):
        stripped = source.strip()
        if "\n" not in stripped and stripped.lower().endswith((".yaml", ".yml")):
            path = Path(source)
            if path.exists():
                return path.read_text(encoding="utf-8")
            raise FileNotFoundError(f"YAML file not found: {source}")
        return source
    raise TypeError(f"expected YAML text or a path, got {type(source).__name__}")
