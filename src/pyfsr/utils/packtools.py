"""Solution-pack toolkit: assemble a pack from live appliance content and
bundled installers, without hand-knowing the wire formats.

Every convention encoded here was established against a live 8.0.x appliance
(most of it the hard way, building the FortiEDR demo pack):

- ``info.json`` needs ``type: "solutionpack"`` and a ``contents`` map claiming
  every section directory; connectors' contents entries key on ``apiName``,
  recordSets' on ``apiName`` with a ``count``, everything else on ``name``.
- A bundled connector/widget tgz is declared in its section's ``data.json``
  with ``install_mode: "tgz"`` and ``installer_path`` pointing at the archive
  file inside the same section directory. Such a pack installs ONLY through
  the Import Wizard path (``import_config.import_file``); the Content Hub path
  silently installs none of them. Bundled widgets install live (not drafts).
- Dashboards export as one file per dashboard (exporter field set, uuid kept),
  fetched from the hidden system entity at ``/api/3/dashboard`` (singular).
- Records export server-side as ``records/<apiName>/<file>.json`` (a list of
  records); the export engine emits records only when the record-set query
  carries a ``limit``.
- Playbooks export as record-shaped JSON per workflow plus a
  ``collection.metadata.json`` sidecar per collection directory (and a
  ``tags.json`` when the collection has tags).
- The SOC-simulator demo pattern (Vulnerability Management / Threat Intel
  Management packs): ship demo-creator playbooks plus a ``scenario`` recordSet
  whose ``steps.Steps[].playbookIRI`` references them; the scenario playbooks
  are *abstract* ("Referenced") triggers, which is what a start step without a
  ``module:`` compiles to.
"""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path

from ..exports import Export

#: Section directory name for each contents key that ships files.
SECTION_DIRS = {
    "connectors": "connectors",
    "widgets": "widgets",
    "dashboards": "dashboards",
    "recordSets": "records",
    "playbooks": "playbooks",
}

#: RFC 4122 URL namespace -- deterministic ids so re-running a build produces
#: byte-identical packs and re-importing updates in place.
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


#: Fields the SOC Simulator's ``create_simulated_alert`` should strip from a
#: pasted record: record identity (a fresh record must not claim the source's
#: uuid/sourceId or it would upsert-merge into the live alert), SLA/clock and
#: lifecycle fields the platform recomputes, and ownership/tenant linkage.
DEFAULT_SIMULATED_IGNORE = (
    "uuid,@id,@type,@context,id,sourceId,state,respDueDate,ackDate,respDate,dueBy,"
    "createDate,modifyDate,createUser,modifyUser,assignedTo,assigneddate,"
    "escalatedtoincident,escalationReason,alertRemainingAckSLA,alertRemainingRespSLA,"
    "ackSLApausedon,respSLApausedon,ackSlaStatus,respSlaStatus,fsrQueue,tenant,"
    "tenantRecordId,priorityWeight,conflict,resolvedAutomatedly,resolveddate,"
    "closureNotes,closureReason,triagetaskid,recordTags,attachmentNames,bucketName"
)


class PackError(Exception):
    pass


class Pack:
    """A solution pack directory on disk (``info.json`` + section dirs)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        info_path = self.path / "info.json"
        if info_path.exists():
            self.info = json.loads(info_path.read_text())
        else:
            raise PackError(f"{self.path}: no info.json -- run `pyfsr pack init` first (or point at the pack dir)")

    # -- construction ------------------------------------------------------

    @classmethod
    def init(
        cls,
        path: str | Path,
        *,
        name: str,
        label: str,
        version: str,
        description: str = "",
        publisher: str = "",
    ) -> Pack:
        p = Path(path)
        info_path = p / "info.json"
        if info_path.exists():
            raise PackError(f"{info_path} already exists")
        p.mkdir(parents=True, exist_ok=True)
        info = {
            "type": "solutionpack",
            "name": name,
            "label": label,
            "version": version,
            "description": description,
            "publisher": publisher,
            "dependencies": [
                {"name": "sOARFramework", "type": "solutionpack", "label": "SOAR Framework", "minVersion": None}
            ],
            "contents": {},
        }
        info_path.write_text(json.dumps(info, indent=2) + "\n")
        return cls(p)

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        (self.path / "info.json").write_text(json.dumps(self.info, indent=2) + "\n")

    # -- bundled installers --------------------------------------------------

    def add_installer(self, category: str, tgz_path: str | Path) -> dict:
        """Declare a bundled connector/widget tgz: data.json row + file copy.

        Reads the archive's own ``info.json`` for the identity fields, so the
        row cannot drift from what will actually install.
        """
        if category not in ("connectors", "widgets"):
            raise PackError(f"installer category must be 'connectors' or 'widgets', not {category!r}")
        tgz = Path(tgz_path)
        inner = _tgz_info(tgz)
        section = self.path / category
        section.mkdir(exist_ok=True)
        shutil.copy2(tgz, section / tgz.name)
        rows = self.data_manifest(category)
        row = {
            "name": inner.get("name"),
            "label": inner.get("label"),
            "version": inner.get("version"),
            "install_mode": "tgz",
            "installer_path": tgz.name,
        }
        if category == "widgets":
            row["title"] = inner.get("title") or inner.get("label")
        else:
            row["category"] = inner.get("category") or []
            row["description"] = inner.get("description", "")
            row["publisher"] = inner.get("publisher", "")
            row["operation_roles"] = []
            row["configurations"] = []
            row["dataImports"] = []
        rows = [r for r in rows if r.get("name") != row["name"]] + [row]
        (section / "data.json").write_text(json.dumps(rows, indent=2) + "\n")
        return row

    def data_manifest(self, category: str) -> list[dict]:
        data = self.path / SECTION_DIRS[category] / "data.json"
        if not data.exists():
            return []
        rows = json.loads(data.read_text())
        return rows if isinstance(rows, list) else [rows]

    # -- exported content ----------------------------------------------------

    def absorb_export(self, zip_path: str | Path, sections: list[str] | None = None) -> dict[str, int]:
        """Copy sections out of an appliance export zip into the pack.

        ``sections`` are contents keys (``dashboards``, ``recordSets``,
        ``playbooks``); anything the export carries for them is copied under
        this pack's section directory, preserving subpaths. With ``None``,
        every top-level directory the export carries is absorbed -- the
        general path for wizard-surface content (``modules/`` view-template
        layouts, ``picklists/``, ``roles/``, ...).

        The export's own ``info.json`` ``contents`` map is merged in either
        way: the exporter is the authority on how what-it-exported must be
        claimed (dict-shaped for ``modules`` / ``viewTemplates`` / ``views``,
        list-shaped elsewhere). Categories whose contents this pack derives
        from its directories (connectors, widgets, dashboards, recordSets,
        playbooks) are re-derived on the next :meth:`sync_contents`, so a
        merge cannot go stale for them.
        """
        counts: dict[str, int] = {}
        with Export.open(zip_path) as ex:
            wanted = sections if sections is not None else sorted(ex.directories())
            for section in wanted:
                directory = SECTION_DIRS.get(section, section)
                n = 0
                for member in ex.members:
                    if member == directory or member.startswith(directory + "/"):
                        dest = self.path / member
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with ex.zf.open(ex.root + member) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
                        if member.endswith(".json"):
                            n += 1
                counts[section] = n
            self._merge_export_contents(ex.info)
        return counts

    def _merge_export_contents(self, export_info: dict) -> None:
        """Merge an export's ``contents`` claims into this pack's info.json.

        List categories dedupe by identity (``apiName`` or ``name``); dict
        categories (``modules`` / ``viewTemplates`` / ``views``) merge by key.
        """
        merged = dict(self.info.get("contents") or {})
        for key, val in (export_info.get("contents") or {}).items():
            if isinstance(val, dict):
                base = dict(merged.get(key)) if isinstance(merged.get(key), dict) else {}
                base.update(val)
                merged[key] = base
            elif isinstance(val, list):
                base = list(merged.get(key) or [])
                seen = {(e.get("apiName") or e.get("name")) if isinstance(e, dict) else e for e in base}
                for entry in val:
                    ident = (entry.get("apiName") or entry.get("name")) if isinstance(entry, dict) else entry
                    if ident not in seen:
                        base.append(entry)
                        seen.add(ident)
                merged[key] = base
            elif val is not None:
                merged[key] = val
        self.info["contents"] = merged

    # -- contents + build ------------------------------------------------------

    def sync_contents(self) -> dict:
        """Re-derive ``info.json``'s ``contents`` from what the pack actually ships.

        The directory is the truth; the contents map must claim it or the
        importer ignores the files (and the pack validator flags them
        unclaimed). Keys the pack carries that this derives nothing for
        (``modules``, ``roles``, ...) are left untouched.
        """
        contents = dict(self.info.get("contents") or {})
        # An empty claim is noise (the exporter emits them when a category was
        # selected but carried nothing, e.g. view templates without module
        # schemas); drop them so the validator does not flag empty categories.
        contents = {k: v for k, v in contents.items() if v}

        connectors = [
            {"apiName": r.get("name"), "name": r.get("label") or r.get("name")}
            for r in self.data_manifest("connectors")
        ]
        if connectors or (self.path / "connectors").exists():
            contents["connectors"] = connectors

        widgets = [{"name": r.get("name")} for r in self.data_manifest("widgets")]
        if widgets or (self.path / "widgets").exists():
            contents["widgets"] = widgets

        dash_dir = self.path / "dashboards"
        if dash_dir.exists():
            dashes = []
            for f in sorted(dash_dir.glob("*.json")):
                d = json.loads(f.read_text())
                dashes.append({"name": d.get("displayName") or f.stem})
            contents["dashboards"] = dashes

        rec_dir = self.path / "records"
        if rec_dir.exists():
            record_sets = []
            for sub in sorted(p for p in rec_dir.iterdir() if p.is_dir()):
                count = 0
                for f in sub.glob("*.json"):
                    payload = json.loads(f.read_text())
                    count += len(payload) if isinstance(payload, list) else 1
                record_sets.append({"name": None, "apiName": sub.name, "count": count})
            contents["recordSets"] = record_sets

        pb_dir = self.path / "playbooks"
        if pb_dir.exists():
            collections = []
            for meta in sorted(pb_dir.glob("*/collection.metadata.json")):
                m = json.loads(meta.read_text())
                collections.append({"name": m.get("name") or meta.parent.name})
            if collections:
                contents["playbooks"] = collections

        self.info["contents"] = contents
        return contents

    def build(self, out: str | Path | None = None, *, strict: bool = True) -> Path:
        """Zip the pack in the appliance exporter's layout and validate it.

        Validation always runs. With ``strict=True`` (the default) ERROR
        findings fail the build; WARNINGs are reported either way -- the
        expected ``pack.bundled_installers_need_import_wizard`` advisory is a
        WARNING, so a normal bundled-installer pack builds clean. With
        ``strict=False`` the build never fails; findings are report-only.
        """
        from ..exports import ExportValidationError

        self.sync_contents()
        self.save()
        name = self.info.get("name") or self.path.name
        version = self.info.get("version") or "0.0.0"
        out = (
            Path(out) if out else self.path.parent / f"{name.lower().replace('-', '')}_{version.replace('.', '_')}.zip"
        )
        root = f"export_{stable_id(name, version, 'ziproot')}/"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(self.path.rglob("*")):
                if f.is_file() and f.name != out.name:
                    zf.write(f, root + f.relative_to(self.path).as_posix())
        findings: list = []
        try:
            findings = _validate_zip(out)
        except ExportValidationError as exc:
            if strict:
                raise
            findings = list(exc.findings)
        for f in findings:
            print(f"  [{f.severity.value}] {f.code}: {f.message}")
        return out


def _validate_zip(zip_path: Path) -> list:
    """Validate an export zip; ERRORs raise, WARNINGs come back as findings."""
    ex = Export.open(zip_path)
    try:
        return ex.validate(strict=False)
    finally:
        ex.close()


def _tgz_info(tgz: Path) -> dict:
    """The ``info.json`` inside a connector/widget tgz (single root dir)."""
    with tarfile.open(tgz, "r:gz") as tf:
        candidates = [
            m for m in tf.getmembers() if m.isfile() and m.name.count("/") == 1 and m.name.endswith("/info.json")
        ]
        if len(candidates) != 1:
            raise PackError(f"{tgz.name}: expected exactly one <root>/info.json, found {len(candidates)}")
        return json.loads(tf.extractfile(candidates[0]).read())


# ---------------------------------------------------------------------------
# Live appliance helpers (need a connected FortiSOAR client)
# ---------------------------------------------------------------------------


def export_live(client, template, tag: str) -> tuple[Path, dict]:
    """Create an export template, run the export, delete the template.

    Returns ``(zip_path, exported_info)``. The template is deleted so repeated
    runs do not litter the appliance's export-template list.
    """
    template.name = f"pyfsr-packtools {tag} {uuid.uuid4().hex[:8]}"
    created = client.export_config.create_template(template)
    template_uuid = created.get("uuid") if isinstance(created, dict) else None
    if not template_uuid:
        raise PackError(f"export template creation returned no uuid: {created!r}")
    try:
        tmp = Path(tempfile.mkdtemp(prefix="pyfsr-packtools-"))
        zip_path = tmp / f"{tag}.zip"
        client.export_config.export_by_template_uuid(template_uuid, output_path=str(zip_path))
        with Export.open(zip_path) as ex:
            info = dict(ex.info)
        return zip_path, info
    finally:
        try:
            client.export_config.delete_template(template_uuid)
        except Exception:
            pass


def add_dashboard(client, pack: Pack, uuid_or_name: str) -> dict:
    """Export one dashboard (by uuid or exact displayName) into the pack."""
    dash_uuid, display_name = _resolve_dashboard(client, uuid_or_name)
    zip_path, info = export_live(client, _new_template().add_dashboard(dash_uuid), f"dashboard-{display_name}")
    counts = pack.absorb_export(zip_path, ["dashboards"])
    return {
        "uuid": dash_uuid,
        "name": display_name,
        "files": counts["dashboards"],
        "export_contents": info.get("contents"),
    }


def add_records(
    client, pack: Pack, module: str, *, record_uuid: str | None = None, filter_json: str | None = None, limit: int = 100
) -> dict:
    """Export a module's records as a record set.

    Exactly one of ``record_uuid`` / ``filter_json`` selects the records. The
    query MUST carry a limit -- the export engine emits nothing without one
    (live-verified on 8.0.0).
    """
    from ..query import Query

    query = Query(module=module)
    if record_uuid:
        query = query.eq("uuid", record_uuid)
    elif filter_json:
        raw = json.loads(filter_json)
        query = _query_from_body(module, raw)
    else:
        raise PackError("give --uuid or --filter-json")
    template = _new_template().add_record_set(module, query=query, limit=limit)
    zip_path, info = export_live(client, template, f"records-{module}")
    counts = pack.absorb_export(zip_path, ["recordSets"])
    return {"module": module, "files": counts["recordSets"], "limit": limit, "export_contents": info.get("contents")}


def _query_from_body(module: str, body: dict):
    """Wrap a raw query body for ``add_record_set`` (duck-typed to_body)."""
    from ..query import Query

    q = Query(module=module)
    q._body = body  # the exporter reads to_body(); hand it through verbatim
    q.to_body = lambda: body  # type: ignore[method-assign]
    return q


def _resolve_dashboard(client, uuid_or_name: str) -> tuple[str, str]:
    if _looks_like_uuid(uuid_or_name):
        return uuid_or_name, uuid_or_name
    rows = client.request("GET", "/api/3/dashboard").json()["hydra:member"]
    for d in rows:
        if d.get("displayName") == uuid_or_name:
            return d["uuid"], d["displayName"]
    raise PackError(f"no dashboard named {uuid_or_name!r}")


def _looks_like_uuid(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


def _new_template():
    from ..api.export_config import ExportTemplate

    return ExportTemplate("packtools")


def add_playbook_collection(client, pack: Pack, collection: str) -> dict:
    """Export a playbook collection (with its playbooks) into the pack."""
    template = _new_template().add_playbook_collection(collection)
    zip_path, info = export_live(client, template, f"playbooks-{collection[:40]}")
    counts = pack.absorb_export(zip_path, ["playbooks"])
    return {"collection": collection, "files": counts["playbooks"], "export_contents": info.get("contents")}


#: (cli flag kind, builder call). The export wizard's content surface, as the
#: typed :class:`~pyfsr.api.export_config.ExportTemplate` builder covers it.
_SELECTOR_KINDS = {
    "module": lambda t, v, c: t.add_module(v),
    "module-fields": lambda t, v, c: t.add_module(*_split_fields(v)),
    "view-template": lambda t, v, c: _add_view_template(t, v),
    "picklist": lambda t, v, c: t.add_picklist(v),
    "connector": lambda t, v, c: t.add_connector(v),
    "widget": lambda t, v, c: t.add_widget(v),
    "dashboard": lambda t, v, c: t.add_dashboard(_resolve_dashboard(c, v)[0]),
    "playbook-collection": lambda t, v, c: t.add_playbook_collection(v),
    "global-variable": lambda t, v, c: t.add_global_variable(v),
    "playbook-block": lambda t, v, c: t.add_playbook_block(v),
    "role": lambda t, v, c: t.add_role(v),
    "team": lambda t, v, c: t.add_team(v),
    "actor": lambda t, v, c: t.add_actor(v),
    "navigation": lambda t, v, c: _add_navigation(t, v),
    "report": lambda t, v, c: t.add_report(v),
    "rule": lambda t, v, c: t.add_rule(v),
    "rule-channel": lambda t, v, c: t.add_rule_channel(v),
    "preprocessing-rule": lambda t, v, c: t.add_preprocessing_rule(v),
    "app-setting": lambda t, v, c: t.add_app_setting(v),
    "export-template": lambda t, v, c: t.add_export_template(v),
    "ai-agent": lambda t, v, c: t.add_ai_agent(v),
    "mcp-configuration": lambda t, v, c: t.add_mcp_configuration(v),
}


def _split_fields(value: str) -> tuple[str, list[str] | None]:
    """``NAME`` or ``NAME=f1,f2`` for add_module(fields=...)."""
    if "=" in value:
        name, _, fields = value.partition("=")
        return name, [f for f in fields.split(",") if f]
    return value, None


def _add_view_template(template, value: str):
    """``MODULE`` exports all three layouts; ``MODULE:detail,form`` a subset."""
    module, _, kinds = value.partition(":")
    if not module:
        raise PackError("--view-template needs MODULE[:detail,form,list]")
    known = ("list", "detail", "form")
    kwargs: dict[str, bool] = {"list_view": False, "detail": False, "form": False}
    for kind in kinds.split(",") if kinds else known:
        kind = kind.strip()
        if kind not in known:
            raise PackError(f"view-template kind {kind!r} not one of {', '.join(known)}")
        kwargs[{"list": "list_view"}.get(kind, kind)] = True
    return template.add_view_templates(module, **kwargs)


def _add_navigation(template, value: str):
    sections, _, flag = value.partition(":")
    names = [s for s in sections.split(",") if s]
    if not names:
        raise PackError("--navigation needs SECTION[,SECTION][:replace]")
    return template.add_navigation(*names, replace=flag == "replace")


def add_content(client, pack: Pack, selectors: list[tuple[str, str]]) -> dict:
    """Add anything the export wizard supports, then absorb everything it emits.

    ``selectors`` are ``(kind, value)`` pairs -- see ``_SELECTOR_KINDS`` for the
    covered surface (modules, view templates, picklists, connectors, widgets,
    dashboards, playbook collections, roles, teams, actors, navigation, reports,
    rules, AI agents, app settings, export templates, MCP configurations).
    Record sets go through :func:`add_records`, which carries the query and
    limit handling. Whatever the export engine emits for the selection is
    absorbed whole (including the module layout files a view template ships
    as), and the export's own ``contents`` map is merged into the pack, so a
    view-template addition claims itself correctly.
    """
    if not selectors:
        raise PackError("nothing selected -- pass at least one --<kind> flag")
    template = _new_template()
    for kind, value in selectors:
        handler = _SELECTOR_KINDS.get(kind)
        if not handler:
            raise PackError(f"unknown content kind {kind!r}")
        handler(template, value, client)
    zip_path, info = export_live(client, template, "content")
    counts = pack.absorb_export(zip_path)
    return {"files": counts, "export_contents": info.get("contents")}


# ---------------------------------------------------------------------------
# SOC-simulator demo generation
# ---------------------------------------------------------------------------


_YAML_TEMPLATE = """\
# Generated by `pyfsr pack soc-demo` from a live {module} record.
# Edit freely, then redeploy: `pyfsr playbook deploy {yaml_name} --replace --check-connectors`
# and re-run `pyfsr pack soc-demo ...` (or `pack add-playbooks`) to refresh the pack.
collection: {collection}
description: >-
  {collection_desc}
visible: true
tags:
  - soc-simulator
  - demo

playbooks:
  - name: {playbook_name}
    description: >-
      Creates a simulated {module} record from the baked-in snapshot of a real
      one, through the SOC Simulator connector's create_simulated_alert
      operation. Lets the demo run with no {module} source connected. The
      record snapshot carries its source data, so detail widgets (e.g. a
      command timeline) render fully.
    tags:
      - soc-simulator
      - demo
    is_active: true
    steps:
    - name: Start
      type: start
      next: Create Simulated Record

    - name: Create Simulated Record
      type: connector
      connector: fortisoar-soc-simulator
      operation: create_simulated_alert
      comment: |
        The SOC Simulator strips the ignored fields and creates a fresh record.
        Identity fields (uuid/sourceId) are ignored so the simulated record
        never upsert-merges into the live alert it was cloned from.
      params:
        alert_json: |
{alert_json_block}
        fields_to_ignore: '{fields_to_ignore}'
"""


def soc_demo(
    client,
    pack: Pack,
    module: str,
    record_uuid: str,
    *,
    title: str,
    collection: str,
    playbook_name: str | None = None,
    yaml_out: str | Path | None = None,
    fields_to_ignore: str = DEFAULT_SIMULATED_IGNORE,
    deploy: bool = True,
) -> dict:
    """Generate (and deploy) the SOC-simulator demo playbook + scenario record.

    The full Fortinet pattern, in one command:

    1. fetch the live record (e.g. an alert captured from the real source),
    2. render an abstract-trigger playbook that replays it through the SOC
       Simulator's ``create_simulated_alert`` (identity/SLA fields ignored),
    3. compile and deploy it to the appliance,
    4. export the playbook collection into the pack (record-shaped JSON),
    5. ship a ``scenario`` recordSet whose step runs the playbook -- the
       "Run" button in the SOC Simulator UI.

    Without FortiEDR (or whatever source) connected, importing the pack and
    running the scenario reproduces the demo data.
    """
    from ..authoring import compile_playbook_yaml

    playbook_name = playbook_name or f"Create Simulated {module.title()} Record"
    record = client.get(f"/api/3/{module}/{record_uuid}")
    if hasattr(record, "model_dump"):
        record = record.model_dump()

    alert_json = json.dumps(record, indent=1, default=str)
    block = "\n".join("          " + line for line in alert_json.splitlines())
    yaml_text = _YAML_TEMPLATE.format(
        module=module,
        yaml_name=Path(yaml_out).name if yaml_out else "soc_demo_playbooks.yaml",
        collection=collection,
        collection_desc=f"Demo playbooks for {title}: replay captured records through the SOC Simulator.",
        playbook_name=playbook_name,
        alert_json_block=block,
        fields_to_ignore=fields_to_ignore,
    )
    out = Path(yaml_out) if yaml_out else pack.path.parent / "soc_demo_playbooks.yaml"
    out.write_text(yaml_text)

    result = compile_playbook_yaml(yaml_text, client=client)
    if not result.ok:
        from ..authoring import format_diagnostic

        diagnostics = "\n".join(format_diagnostic(d) for d in result.errors)
        raise PackError(f"generated playbook does not compile:\n{diagnostics}")

    workflow_uuid = _find_workflow_uuid(result.fsr_json, playbook_name)

    if deploy:
        client.workflow_collections.import_export(result.fsr_json, replace=True)

    scenario = {
        "@type": "Scenario",
        "uuid": stable_id(pack.info.get("name"), "scenario", title),
        "title": title,
        "description": (
            f"Replays a captured {module} record through the SOC Simulator to "
            f"recreate the demo data with no live {module} connection."
        ),
        "icon": None,
        "steps": {
            "Steps": [
                {
                    "name": f"Create simulated {module} record",
                    "type": "executePlaybook",
                    "playbookIRI": f"/api/3/workflows/{workflow_uuid}",
                    "preCreateMessage": f"Creating simulated {module} record for {title}",
                    "postCreateMessage": "",
                    "waitTimeforNextStep": "0",
                }
            ]
        },
    }
    scenario_dir = pack.path / "records" / "scenario"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "demo.json").write_text(json.dumps([scenario], indent=1) + "\n")

    added = add_playbook_collection(client, pack, collection)
    pack.sync_contents()
    pack.save()
    return {
        "yaml": str(out),
        "playbook": playbook_name,
        "workflow_uuid": workflow_uuid,
        "deployed": deploy,
        "scenario_title": title,
        "playbooks_in_pack": added["files"],
    }


def _find_workflow_uuid(fsr_json, playbook_name: str) -> str:
    """Pull the deployed playbook's uuid out of the compiled export JSON."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if (
                node.get("name") == playbook_name
                and node.get("uuid")
                and ("steps" in node or "triggerStep" in node or "routes" in node)
            ):
                found.append(node["uuid"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(fsr_json)
    if not found:
        raise PackError(f"compiled JSON carries no workflow named {playbook_name!r}")
    return found[0]
