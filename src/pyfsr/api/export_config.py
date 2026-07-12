"""Configuration export/import — ``client.export_config``.

Export a FortiSOAR configuration (modules, playbooks, picklists, roles, and other
content) to a portable archive and import it into another instance — the basis
for backup/restore and multi-environment provisioning. Some operations depend on
the active auth method; the API checks support before running.
"""

import os
import time
from typing import Any

from ..auth.base import BaseAuth
from ..models._export import (
    ActorSelection,
    AiAgentSelection,
    ConnectorSelection,
    DeliveryRuleSelection,
    ModuleSelection,
    NavigationSelection,
    PlaybookCollectionSelection,
    RecordSet,
    ReportSelection,
    RoleSelection,
    RuleSelection,
    TeamSelection,
)
from ..models._integration import ExportJobResult
from ..pagination import extract_members
from ..utils.validation import is_uuid as _is_uuid
from .base import BaseAPI
from .content_hub import ContentHubSearch

__all__ = ["ExportConfigAPI", "ExportTemplate"]

# Default cap on records emitted per record set. The export engine treats the
# query's ``limit`` as a *required trigger*: a record set whose query has no
# ``limit`` exports zero records (live-verified on 8.0.0). There is no
# "unlimited"; callers raise this via ``add_record_set(limit=...)`` when needed.
_DEFAULT_RECORD_LIMIT = 1000


class ExportTemplate:
    """Typed builder for a Configuration Export template's ``options`` payload.

    Composes the same content the export wizard's steps do, then hands the
    result to :meth:`ExportConfigAPI.create_template`. Each ``add_*`` returns
    ``self`` so calls chain::

        from pyfsr import Query
        from pyfsr.api.export_config import ExportTemplate

        tmpl = (
            ExportTemplate("Open alerts backup")
            .add_module("alerts", fields=["name", "status", "severity"])
            .add_record_set(
                "alerts",
                query=Query(module="alerts").eq("status", "Open"),
                limit=5000,
                include_correlations=True,
            )
        )
        client.export_config.create_template(tmpl)

    ``add_module`` exports a module's **schema**; ``add_record_set`` exports its
    **records** (data), optionally filtered. Entry shapes and their
    required/optional fields are backed by typed models in
    :mod:`pyfsr.models._export`, established against a live 8.0.0 appliance — in
    particular a record set only emits records when its query carries a ``limit``.

    The name-based categories (``add_picklist`` / ``add_connector`` /
    ``add_playbook_collection`` / ``add_role`` / ``add_team`` / ``add_actor``)
    take a friendly **name** (an actor's ``title``) and are resolved to their
    IRI/``value`` at :meth:`ExportConfigAPI.create_template` time; the id-based
    categories (``add_view_template`` / ``add_dashboard`` / ``add_widget``) are
    exported by id/name verbatim.
    """

    def __init__(self, name: str, *, auto_select_picklists: bool = True) -> None:
        self.name = name
        self._auto_select_picklists = auto_select_picklists
        self._modules: list[ModuleSelection] = []
        self._record_sets: list[RecordSet] = []
        self._view_templates: list[str] = []
        # Name-based categories that need a live IRI/detail lookup: they are
        # resolved by ExportConfigAPI.create_template (which has the client),
        # not by build() (offline). Each holds the caller's declarative spec.
        self._picklists: list[str] = []
        self._connectors: list[dict[str, Any]] = []
        self._collections: list[dict[str, Any]] = []
        self._roles: list[str] = []
        self._teams: list[str] = []
        self._actors: list[str] = []
        self._reports: list[dict[str, Any]] = []
        self._preprocessing_rules: list[str] = []
        self._rules: list[str] = []
        self._rule_channels: list[str] = []
        self._ai_agents: list[dict[str, Any]] = []
        # Navigation slices (options.views[]). Each spec is {sections, merge};
        # the "app" view uuid is resolved live at create_template time.
        self._navigation: list[dict[str, Any]] = []
        # Id-based UI categories the engine takes verbatim (no lookup): dashboards
        # by uuid, widgets by name. Live-observed on 8.0.0 as bare string lists.
        self._dashboards: list[str] = []
        self._widgets: list[str] = []
        # MCP server configs (options.mcp_configurations[]): a bare uuid list on
        # the wire, but callers may pass a config name (resolved live to its uuid).
        self._mcp_configs: list[str] = []

    def add_module(self, module: str, *, fields: list[str] | None = None) -> "ExportTemplate":
        """Export a module's schema, optionally limited to ``fields`` (all fields if omitted)."""
        self._modules.append(ModuleSelection(value=module, includedAttributes=list(fields or [])))
        return self

    def add_view_template(self, view_template: str) -> "ExportTemplate":
        """Export a view template by id (e.g. ``"modules-alerts-list"``); resolved offline."""
        self._view_templates.append(view_template)
        return self

    def add_picklist(self, name: str) -> "ExportTemplate":
        """Export a picklist by **name** (resolved to its IRI at ``create_template`` time)."""
        self._picklists.append(name)
        return self

    def add_connector(self, name: str, *, include_configurations: bool = True) -> "ExportTemplate":
        """Export a connector by **name**, optionally with its saved configurations.

        The connector's ``value``/``version``/``label`` are looked up at
        :meth:`ExportConfigAPI.create_template` time. Set
        ``include_configurations=False`` to ship the connector without its saved
        configs (secrets).
        """
        self._connectors.append({"name": name, "include_configurations": bool(include_configurations)})
        return self

    def add_playbook_collection(
        self,
        name: str,
        *,
        include_global_variables: bool = True,
        include_schedules: bool = True,
        include_versions: bool = True,
    ) -> "ExportTemplate":
        """Export a playbook collection by **name**, with its dependent content.

        The collection's ``value`` is resolved at
        :meth:`ExportConfigAPI.create_template` time. The three flags mirror the
        wizard's Playbooks-step toggles for pulling the collection's global
        variables, schedules, and version history.
        """
        self._collections.append(
            {
                "name": name,
                "includeGlobalVariables": bool(include_global_variables),
                "includeSchedules": bool(include_schedules),
                "includeVersions": bool(include_versions),
            }
        )
        return self

    def add_role(self, name: str) -> "ExportTemplate":
        """Export an RBAC role by **name** (resolved to its IRI at ``create_template`` time).

        The role's IRI/label/uuid are looked up at
        :meth:`ExportConfigAPI.create_template` time, mirroring
        :meth:`add_connector`. Use this to carry role definitions into another
        appliance alongside the modules/playbooks that reference them.
        """
        self._roles.append(name)
        return self

    def add_team(self, name: str) -> "ExportTemplate":
        """Export a team by **name** (resolved to its ``/api/3/teams/<uuid>`` IRI)."""
        self._teams.append(name)
        return self

    def add_actor(self, title: str) -> "ExportTemplate":
        """Export an actor (person) by **title** (resolved to its ``/api/3/people/<uuid>`` IRI).

        Actors are people, so the identity field is ``title`` and the resolved
        ``value`` is a people IRI.
        """
        self._actors.append(title)
        return self

    def add_navigation(self, *sections: str, replace: bool = False) -> "ExportTemplate":
        """Export navigation-menu sections (``options.views[]``).

        Ships the named top-level navigation sections (e.g. ``"Threat
        Intelligence"``, ``"Resources"``) so the target appliance's left-hand nav
        gains them on import. Section titles are validated against the live "app"
        navigation at :meth:`ExportConfigAPI.create_template` time; the view's
        uuid is resolved there too.

        Args:
            *sections: top-level navigation section titles to export. Passing none
                exports every section of the live navigation.
            replace: ``mergeType`` for the export — ``False`` (default) layers the
                sections onto the target's existing nav, ``True`` overwrites it.
        """
        self._navigation.append({"sections": list(sections), "merge": not replace})
        return self

    def add_report(self, name: str, *, include_schedules: bool = True) -> "ExportTemplate":
        """Export a report by **display name** (``options.reports[]``).

        The report's uuid/label are resolved from ``/api/3/reporting`` (matched on
        ``displayName``) at :meth:`ExportConfigAPI.create_template` time. Set
        ``include_schedules=False`` to ship the report without its schedules.
        """
        self._reports.append({"name": name, "includeSchedules": bool(include_schedules)})
        return self

    def add_rule(self, name: str) -> "ExportTemplate":
        """Export a delivery rule by **name** (``options.rules[]``).

        Delivery rules (the SOAR UI's *Rules* / notification rules) live in the
        rule-engine app. The name is resolved to its uuid at
        :meth:`ExportConfigAPI.create_template` time and emitted as the Export
        Wizard's ``{type: "rule", value, label, include}`` entry — the shape the
        export engine requires to actually write the rule into the archive
        (live-verified: emits ``rules/<name>.json``).
        """
        self._rules.append(name)
        return self

    def add_ai_agent(self, name: str, *, install: bool = True, include_configurations: bool = True) -> "ExportTemplate":
        """Export an AI agent by **name** or **label** (``options.ai_agents[]``).

        AI agents are Content Hub items (``type: "ai_agent"``); the agent's
        id/label/version are resolved from the hub at
        :meth:`ExportConfigAPI.create_template` time. ``install`` toggles
        install-on-import; ``include_configurations`` ships the agent's saved
        configs.
        """
        self._ai_agents.append({"name": name, "install": bool(install), "configurations": bool(include_configurations)})
        return self

    def add_rule_channel(self, name: str) -> "ExportTemplate":
        """Export a delivery-rule **channel** by name (``options.ruleChannels[]``).

        Channels (email / in-app / playbook-failure notifications) live alongside
        delivery rules in the rule-engine app at ``.../api/channel/``. The name is
        resolved to its uuid and emitted as the Export Wizard's
        ``{type: "channel", value, label, include}`` entry (live-verified: emits
        ``ruleChannels/<name>.json`` into the archive).
        """
        self._rule_channels.append(name)
        return self

    def add_preprocessing_rule(self, name: str) -> "ExportTemplate":
        """Export a preprocessing rule by **name** (``options.preprocessingRules[]``).

        The rule's uuid is resolved from ``/api/3/preprocessing_rules`` (matched on
        ``name``) at :meth:`ExportConfigAPI.create_template` time.
        """
        self._preprocessing_rules.append(name)
        return self

    def add_dashboard(self, uuid: str) -> "ExportTemplate":
        """Export a dashboard by **uuid** (taken verbatim; ``options.dashboards`` is a uuid list)."""
        self._dashboards.append(uuid)
        return self

    def add_mcp_configuration(self, name_or_uuid: str) -> "ExportTemplate":
        """Export an MCP server configuration by **name** or **uuid**.

        ``options.mcp_configurations`` is a bare uuid list on the wire. A name is
        resolved to its uuid via ``/api/3/mcp_configurations`` at
        :meth:`ExportConfigAPI.create_template` time; a uuid is used verbatim.
        """
        self._mcp_configs.append(name_or_uuid)
        return self

    def add_widget(self, name: str) -> "ExportTemplate":
        """Export a widget by **name** (taken verbatim; ``options.widgets`` is a name list)."""
        self._widgets.append(name)
        return self

    def add_record_set(
        self,
        module: str,
        *,
        query: Any = None,
        limit: int = _DEFAULT_RECORD_LIMIT,
        include_correlations: bool = False,
        label: str | None = None,
    ) -> "ExportTemplate":
        """Export a module's records (data), optionally filtered.

        Args:
            module: the module whose records to export (e.g. ``"alerts"``).
            query: a :class:`~pyfsr.query.Query`, a raw ``Query.to_body()`` dict,
                or ``None`` to export every record (up to ``limit``).
            limit: max records to emit. **Required trigger** — the export engine
                emits records only when the query carries a ``limit`` (live-verified
                on 8.0.0); a record set without one exports nothing. There is no
                "unlimited", so raise this for large sets.
            include_correlations: also pull records correlated/linked to the
                matched records (the wizard's *Include Correlations* toggle).
            label: friendly name for the record set (defaults to ``module``).
        """
        if hasattr(query, "to_body"):
            q = dict(query.to_body())
        elif query is None:
            q = {"logic": "AND", "filters": []}
        else:
            q = dict(query)
        q["limit"] = int(limit)
        self._record_sets.append(
            RecordSet(type=module, query=q, label=label or module, includeCorrelations=include_correlations)
        )
        return self

    def build(self) -> dict[str, Any]:
        """Return the **offline** template ``options`` — the categories needing no lookup.

        Covers modules, record sets, and view templates. Name-based categories
        (picklists / connectors / playbook collections) require a live lookup and
        are merged in by :meth:`ExportConfigAPI.create_template`; see
        :attr:`needs_resolution`.
        """
        options: dict[str, Any] = {}
        if self._modules:
            options["modules"] = [m.wire() for m in self._modules]
        if self._record_sets:
            options["recordSets"] = [r.wire() for r in self._record_sets]
        if self._view_templates:
            options["viewTemplates"] = list(self._view_templates)
        if self._dashboards:
            options["dashboards"] = list(self._dashboards)
        if self._widgets:
            options["widgets"] = list(self._widgets)
        return options

    @property
    def needs_resolution(self) -> bool:
        """True if this template has name-based categories awaiting a live lookup."""
        return bool(
            self._picklists
            or self._connectors
            or self._collections
            or self._roles
            or self._teams
            or self._actors
            or self._reports
            or self._preprocessing_rules
            or self._rules
            or self._rule_channels
            or self._ai_agents
            or self._navigation
            or self._mcp_configs
        )

    @property
    def metadata(self) -> dict[str, Any]:
        """The template ``metadata`` (currently the ``autoSelectPicklists`` flag)."""
        return {"autoSelectPicklists": self._auto_select_picklists}


class ExportConfigAPI(BaseAPI):
    """Class to handle FortiSOAR export configuration operations"""

    def __init__(self, client):
        super().__init__(client)
        self.content_hub = ContentHubSearch(client)

    def _check_auth_support(self, operation: str | None = None) -> None:
        """Verify if the current auth method supports a specific operation"""
        self.client.auth.check_operation_supported(operation)

    def _get_picklist_iri(self, picklist_name: str) -> str:
        """Look up picklist IRI by name"""
        # Query picklist by name
        response = self.client.get("/api/3/picklist_names", params={"name": picklist_name})
        members = extract_members(response)
        if members:
            return members[0]["@id"]
        else:
            raise ValueError(f"Picklist not found: {picklist_name}")

    def _get_connector_info(self, connector_name: str) -> dict[str, Any]:
        """
        Look up connector details by name using ContentHubSearch.

        Args:
            connector_name: Label/name of the connector to find

        Returns:
            Dict containing connector details in export-compatible format

        Raises:
            ValueError: If connector is not found
        """
        connector = self.content_hub.find_available_connector(connector_name)
        if connector and connector.get("label") == connector_name:
            return {
                "value": f"cyops-connector-{connector['name']}-{connector['version']}",
                "version": connector["version"],
                "label": connector["label"],
            }

        raise ValueError(f"Connector not found: {connector_name}")

    def _get_playbook_collection_info(self, collection_name: str) -> dict[str, Any]:
        """Look up playbook collection details by name"""
        # Query playbook collections
        response = self.client.get("/api/3/workflow_collections", params={"name": collection_name})
        members = extract_members(response)
        if members:
            collection = members[0]
            return {"label": collection["name"], "value": collection["@id"].split("/")[-1]}
        else:
            raise ValueError(f"Playbook collection not found: {collection_name}")

    def _get_template_uuid(self, template_name: str) -> str:
        """Look up template UUID by name"""
        response = self.client.get("/api/3/export_templates", params={"name": template_name})
        templates = extract_members(response)
        matching_templates = [t for t in templates if t["name"] == template_name]

        if not matching_templates:
            raise ValueError(f"Export template not found: {template_name}")

        # Get the most recently created template if multiple exist
        template = sorted(matching_templates, key=lambda x: x.get("createDate", 0), reverse=True)[0]
        return template["@id"].split("/")[-1]

    def _trigger_export(self, template_uuid: str, filename: str) -> dict[str, Any]:
        """Trigger the export process using a template"""
        if not filename.endswith(".zip"):
            raise ValueError("Filename must end in .zip")
        return self.client.put(f"/api/export?fileName={filename}&template={template_uuid}")

    def _get_export_status(self, job_uuid: str) -> ExportJobResult:
        """Get the status of an export job"""
        resp = self.client.get(f"/api/3/export_jobs/{job_uuid}")
        return ExportJobResult.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def _download_export(self, file_iri: str, download_path: str | None = None) -> str:
        """
        Download the exported configuration file

        Args:
            file_iri: File IRI to download
            download_path: Optional path to save the file

        Returns:
            Path where the file was saved

        Note:
            The response will be binary data (application/zip) which is handled
            by the client's get() method.
        """
        # The files endpoint returns JSON metadata by default and only streams the
        # raw archive when asked for octet-stream — without this header the GET
        # comes back as a dict and the write below would fail.
        content = self.client.get(file_iri, headers={"Accept": "application/octet-stream"})

        if not download_path:
            filename = file_iri.split("/")[-1]
            download_path = os.path.join(os.getcwd(), filename)

        with open(download_path, "wb") as f:
            if isinstance(content, bytes):
                f.write(content)
            else:
                raise TypeError(f"Expected bytes response, got {type(content)}")

        return download_path

    def _poll_export_completion(self, job_uuid: str, poll_interval: int = 5) -> ExportJobResult:
        """Poll until export is complete"""
        while True:
            status = self._get_export_status(job_uuid)
            if status.status == "Export Complete":
                return status
            time.sleep(poll_interval)

    def _export_with_template(
        self,
        template_uuid: str,
        output_path: str | None = None,
        filename: str | None = None,
        poll_interval: int = 5,
    ) -> str:
        """Common export workflow using template UUID"""
        if not filename:
            filename = f"export_{template_uuid}.zip"

        # Trigger export
        export_job = self._trigger_export(template_uuid, filename)
        job_uuid = export_job["jobUuid"]

        # Poll until complete
        status = self._poll_export_completion(job_uuid, poll_interval)

        # Download file
        file_iri = status["file"]["@id"]
        return self._download_export(file_iri, output_path)

    def export_by_template_uuid(
        self, template_uuid: str, output_path: str | None = None, poll_interval: int = 5
    ) -> str:
        """
        Export configuration using template UUID directly.

        Args:
            template_uuid: UUID of existing export template
            output_path: Optional path to save exported file
            poll_interval: How often to check export status in seconds

        Returns:
            Path where exported file was saved

        Raises:
            UnsupportedAuthOperationError: If the current auth method does not support
                configuration export

        Example:
            >>> client = FortiSOAR('fortisoar.company.com', token='<your-api-token>')
            >>> output_file = client.export_config.export_by_template_uuid(
            ...     template_uuid="123e4567-e89b-12d3-a456-426655440000",
            ...     output_path="exports/config.zip"
            ... )
        """
        self._check_auth_support(operation=BaseAuth.OPERATION_CONFIG_EXPORT)
        return self._export_with_template(
            template_uuid=template_uuid, output_path=output_path, poll_interval=poll_interval
        )

    def export_by_template_name(
        self, template_name: str, output_path: str | None = None, poll_interval: int = 5
    ) -> str:
        """
        Export configuration using template name.

        Args:
            template_name: Name of existing export template
            output_path: Optional path to save exported file
            poll_interval: How often to check export status in seconds

        Returns:
            Path where exported file was saved

        Raises:
            UnsupportedAuthOperationError: If the current auth method does not support
                configuration export

        Example:
            >>> client = FortiSOAR('fortisoar.company.com', token='<your-api-token>')
            >>> output_file = client.export_config.export_by_template_name(
            ...     template_name="Alert Configuration",
            ...     output_path="exports/alert_config.zip"
            ... )
        """
        self._check_auth_support(operation=BaseAuth.OPERATION_CONFIG_EXPORT)
        template_uuid = self._get_template_uuid(template_name)
        filename = f"{template_name.lower().replace(' ', '_')}.zip" if not output_path else None

        return self._export_with_template(
            template_uuid=template_uuid,
            output_path=output_path,
            filename=filename,
            poll_interval=poll_interval,
        )

    def create_simplified_template(
        self,
        name: str,
        modules: list[str] | None = None,
        module_attributes: dict[str, list[str]] | None = None,
        picklists: list[str] | None = None,
        connectors: list[str] | None = None,
        playbook_collections: list[str] | None = None,
        view_templates: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create an export template with simplified inputs - automatically looks up complex values.

        Args:
            name: Name of the export template
            modules: List of module names to export
            module_attributes: Dict of module name to list of attributes to include
            picklists: List of picklist names
            connectors: List of connector names
            playbook_collections: List of playbook collection names
            view_templates: List of view template names (e.g. ["modules-alerts-list"])

        Returns:
            Dict containing the created export template details

        Example:
            >>> from pyfsr import FortiSOAR
            >>> client = FortiSOAR('fortisoar.company.com', token='<your-api-token>')

            >>> # Simple configuration with automatic lookup
            >>> template = client.export_config.create_simplified_template(
            ...     name="Alert Export",
            ...     modules=["alerts"],
            ...     module_attributes={
            ...         "alerts": ["name", "status", "severity", "description"]
            ...     },
            ...     picklists=["AlertStatus", "AlertSeverity"],
            ...     connectors=["OpenAI", "FortiEDR"],
            ...     playbook_collections=["Incident Response"]
            ... )
        """
        # Build modules configuration
        modules_config = []
        if modules:
            for module in modules:
                module_config = {
                    "value": module,
                    "includedAttributes": module_attributes.get(module, []) if module_attributes else [],
                }
                modules_config.append(module_config)

        # Look up picklist IRIs
        picklist_iris = []
        if picklists:
            picklist_iris = [self._get_picklist_iri(name) for name in picklists]

        # Look up connector configurations
        connector_configs = []
        if connectors:
            for connector in connectors:
                info = self._get_connector_info(connector)
                connector_configs.append(
                    {
                        "label": info["label"],
                        "value": info["value"],
                        "rpm": True,
                        "configurations": True,
                        "configCount": 1,
                        "version": info["version"],
                        "include": True,
                        "recordCount": 0,
                    }
                )

        # Look up playbook collection details
        playbook_config = {"collections": [], "globalVariables": []}
        if playbook_collections:
            for collection in playbook_collections:
                info = self._get_playbook_collection_info(collection)
                playbook_config["collections"].append(
                    {
                        "label": info["label"],
                        "value": info["value"],
                        "includeGlobalVariables": True,
                        "includeSchedules": True,
                        "includeVersions": True,
                        "include": True,
                        "recordCount": 0,
                    }
                )

        # Build complete template
        options = {
            "modules": modules_config,
            "picklistNames": picklist_iris,
            "connectors": connector_configs,
            "playbooks": playbook_config,
            "viewTemplates": view_templates or [],
            # Add empty lists for other optional components
            "recordSets": [],
            "views": [],
            "reports": [],
            "dashboards": [],
            "roles": [],
            "teams": [],
            "actors": [],
            "widgets": [],
            "appSettings": [],
            "showOnlyConfigured": [],
            "preprocessingRules": [],
            "ruleChannels": [],
            "rules": [],
            "playbookBlocks": {"blocks": [], "includeGlobalVariables": True},
        }

        metadata = {"autoSelectPicklists": True}

        return self.create_export_template(name=name, options=options, metadata=metadata)

    def create_export_template(
        self, name: str, options: dict[str, Any], metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create the actual export template - internal method"""
        template_data = {
            "name": name,
            "options": options,
            "metadata": metadata or {"autoSelectPicklists": True},
        }
        return self.client.post("/api/3/export_templates", data=template_data)

    def create_template(self, template: ExportTemplate) -> dict[str, Any]:
        """Create an export template from a typed :class:`ExportTemplate` builder.

        Resolves any name-based categories (picklists / connectors / playbook
        collections) to their IRIs/details before posting, so callers work in
        friendly names rather than IRIs.

        Example:
            >>> from pyfsr import Query
            >>> from pyfsr.api.export_config import ExportTemplate
            >>> tmpl = (
            ...     ExportTemplate("Alert backup")
            ...     .add_record_set("alerts", query=Query(module="alerts").eq("status", "Open"))
            ...     .add_picklist("AlertStatus")
            ...     .add_connector("OpenAI")
            ...     .add_playbook_collection("Incident Response")
            ... )
            >>> created = client.export_config.create_template(tmpl)  # doctest: +SKIP
        """
        options = self._resolve_template_options(template)
        return self.create_export_template(name=template.name, options=options, metadata=template.metadata)

    def _resolve_template_options(self, template: ExportTemplate) -> dict[str, Any]:
        """Build the full ``options`` dict, resolving name-based categories to IRIs/details.

        Starts from the offline :meth:`ExportTemplate.build` output and merges in
        ``picklistNames`` / ``connectors`` / ``playbooks`` resolved via the same
        lookups :meth:`create_simplified_template` uses (so the wire shapes match).
        """
        options = template.build()

        if template._picklists:
            options["picklistNames"] = [self._get_picklist_iri(name) for name in template._picklists]

        if template._connectors:
            connectors: list[dict[str, Any]] = []
            for spec in template._connectors:
                info = self._get_connector_info(spec["name"])
                connectors.append(
                    ConnectorSelection(
                        value=info["value"],
                        label=info["label"],
                        version=info["version"],
                        configurations=spec["include_configurations"],
                        configCount=1,
                    ).wire()
                )
            options["connectors"] = connectors

        if template._collections:
            collections: list[dict[str, Any]] = []
            for spec in template._collections:
                info = self._get_playbook_collection_info(spec["name"])
                collections.append(
                    PlaybookCollectionSelection(
                        value=info["value"],
                        label=info["label"],
                        includeGlobalVariables=spec["includeGlobalVariables"],
                        includeSchedules=spec["includeSchedules"],
                        includeVersions=spec["includeVersions"],
                    ).wire()
                )
            options["playbooks"] = {"collections": collections, "globalVariables": []}

        if template._roles:
            roles: list[dict[str, Any]] = []
            for name in template._roles:
                info = self._get_role_info(name)
                roles.append(
                    RoleSelection(
                        value=info["@id"],
                        label=info.get("label") or info.get("name"),
                        name=info.get("name"),
                        uuid=info.get("uuid"),
                    ).wire()
                )
            options["roles"] = roles

        if template._teams:
            teams: list[dict[str, Any]] = []
            for name in template._teams:
                info = self._get_named_record("/api/3/teams", "name", name, "team")
                teams.append(TeamSelection(value=info["@id"], name=info.get("name"), uuid=info.get("uuid")).wire())
            options["teams"] = teams

        if template._actors:
            actors: list[dict[str, Any]] = []
            for title in template._actors:
                info = self._get_actor_info(title)
                actors.append(ActorSelection(value=info["@id"], title=info.get("title"), uuid=info.get("uuid")).wire())
            options["actors"] = actors

        if template._reports:
            reports: list[dict[str, Any]] = []
            for spec in template._reports:
                info = self._get_report_info(spec["name"])
                reports.append(
                    ReportSelection(
                        value=info["uuid"],
                        label=info.get("displayName"),
                        includeSchedules=spec["includeSchedules"],
                    ).wire()
                )
            options["reports"] = reports

        if template._preprocessing_rules:
            options["preprocessingRules"] = [
                self._rule_entry("/api/3/preprocessing_rules", name, "preprocessing rule")
                for name in template._preprocessing_rules
            ]

        if template._rules:
            rule_index = self._delivery_rule_index()
            rules_out: list[dict[str, Any]] = []
            for name in template._rules:
                rec = rule_index.get(name)
                if not rec:
                    raise ValueError(f"delivery rule {name!r} not found")
                rules_out.append(DeliveryRuleSelection(type="rule", value=rec["uuid"], label=rec["name"]).wire())
            options["rules"] = rules_out

        if template._rule_channels:
            chan_index = self._rule_channel_index()
            channels_out: list[dict[str, Any]] = []
            for name in template._rule_channels:
                rec = chan_index.get(name)
                if not rec:
                    raise ValueError(f"rule channel {name!r} not found")
                channels_out.append(DeliveryRuleSelection(type="channel", value=rec["uuid"], label=rec["name"]).wire())
            options["ruleChannels"] = channels_out

        if template._ai_agents:
            agent_index = self._ai_agent_index()
            agents_out: list[dict[str, Any]] = []
            for spec in template._ai_agents:
                rec = agent_index.get(spec["name"])
                if not rec:
                    raise ValueError(f"AI agent {spec['name']!r} not found in Content Hub")
                agents_out.append(
                    AiAgentSelection(
                        name=rec["name"],
                        label=rec.get("label"),
                        version=rec.get("version"),
                        install=spec["install"],
                        configurations=spec["configurations"],
                    ).wire()
                )
            options["ai_agents"] = agents_out

        if template._navigation:
            nav_view = self._get_navigation_view()
            available = self._navigation_section_titles(nav_view)
            views: list[dict[str, Any]] = []
            for spec in template._navigation:
                sections = spec["sections"] or available
                merge_type = "merge" if spec["merge"] else "replace"
                unknown = [s for s in sections if s not in available]
                if unknown:
                    raise ValueError(f"navigation section(s) {unknown} not found; available: {available}")
                views.append(
                    NavigationSelection(
                        uuid=nav_view["uuid"],
                        mergeType=merge_type,
                        appendNavigation=list(sections),
                        navigationOptions=[{"title": s, "mergeType": merge_type} for s in sections],
                    ).wire()
                )
            options["views"] = views

        if template._mcp_configs:
            options["mcp_configurations"] = [self._resolve_mcp_config_uuid(x) for x in template._mcp_configs]

        return options

    def _resolve_mcp_config_uuid(self, name_or_uuid: str) -> str:
        """Resolve an MCP config name to its uuid (``GET /api/3/mcp_configurations``); uuid passes through."""
        if _is_uuid(name_or_uuid):
            return name_or_uuid
        members = extract_members(self.client.get("/api/3/mcp_configurations", params={"name": name_or_uuid}))
        if not members:
            raise ValueError(f"MCP configuration {name_or_uuid!r} not found")
        return members[0]["uuid"]

    def _get_navigation_view(self) -> dict[str, Any]:
        """Fetch the "app" navigation view record (``GET /api/views/1/app``)."""
        return self.client.get("/api/views/1/app")

    @staticmethod
    def _navigation_section_titles(nav_view: dict[str, Any]) -> list[str]:
        """Top-level navigation section titles from an "app" view record."""
        nav = (nav_view.get("config") or {}).get("navigation") or []
        return [n["title"] for n in nav if isinstance(n, dict) and n.get("title")]

    def _get_actor_info(self, title: str) -> dict[str, Any]:
        """Resolve an actor by ``title`` via a client-side match.

        The ``/api/3/actors`` endpoint is an aggregate that does not accept a
        ``title`` server filter, so fetch the (small) actor list and match the
        title exactly.
        """
        for actor in extract_members(self.client.get("/api/3/actors")):
            if isinstance(actor, dict) and actor.get("title") == title:
                return actor
        raise ValueError(f"actor {title!r} not found")

    def _get_report_info(self, name: str) -> dict[str, Any]:
        """Resolve a report by ``displayName`` to its ``Reporting`` record (``/api/3/reporting``)."""
        return self._get_named_record("/api/3/reporting", "displayName", name, "report")

    def _rule_engine_get(self, subpath: str) -> dict[str, Any]:
        """GET a rule-engine collection, tolerating both front-door route styles.

        The rule engine is proxied at ``/rule/api/...`` on some builds and
        ``/api/rule/api/...`` on others; the unmatched style falls through to the
        SPA (an HTML body that fails JSON parsing). Try each and return the first
        that yields JSON.
        """
        last_exc: Exception | None = None
        for root in ("/rule/api/", "/api/rule/api/"):
            try:
                return self.client.get(root + subpath, params={"limit": 2147483647})
            except Exception as exc:  # SPA fallthrough / 404 on the wrong route
                last_exc = exc
        raise RuntimeError(f"rule-engine app not reachable at /rule/api/ or /api/rule/api/ ({last_exc})")

    def _delivery_rule_index(self) -> dict[str, dict[str, Any]]:
        """``{name: record}`` for all delivery rules (``GET .../api/rules/``).

        The rule-engine app doesn't take a ``name`` server filter, so the (small)
        list is fetched once and matched client-side by ``name``.
        """
        members = extract_members(self._rule_engine_get("rules/"))
        return {r["name"]: r for r in members if isinstance(r, dict) and r.get("name")}

    def _rule_channel_index(self) -> dict[str, dict[str, Any]]:
        """``{name: record}`` for all rule channels (``GET .../api/channel/``)."""
        members = extract_members(self._rule_engine_get("channel/"))
        return {r["name"]: r for r in members if isinstance(r, dict) and r.get("name")}

    def _ai_agent_index(self) -> dict[str, dict[str, Any]]:
        """``{name/label: record}`` for installed AI agents (Content Hub ``type: ai_agent``).

        AI agents are Content Hub items; the same ``/api/query/solutionpacks``
        endpoint the connector/pack search uses serves them under the
        ``ai_agent`` type. Indexed by both ``name`` (id) and ``label`` so callers
        can pass either.
        """
        query = {
            "limit": 2147483647,
            "logic": "AND",
            "filters": [
                {"field": "type", "operator": "in", "value": ["ai_agent"]},
                {"field": "installed", "operator": "eq", "value": True},
            ],
            "search": "",
            "__selectFields": ["uuid", "name", "label", "version", "installed", "type"],
        }
        members = extract_members(self.client.post("/api/query/solutionpacks?$limit=2147483647&$page=1", data=query))
        index: dict[str, dict[str, Any]] = {}
        for rec in members:
            if not isinstance(rec, dict):
                continue
            if rec.get("name"):
                index[rec["name"]] = rec
            if rec.get("label"):
                index.setdefault(rec["label"], rec)
        return index

    def _rule_entry(self, endpoint: str, name: str, kind: str) -> dict[str, Any]:
        """Resolve a rule by ``name`` and render its ``{name, uuid, value, exists, include}`` entry.

        Shared by the ``rules`` and ``preprocessingRules`` categories (identical
        wire shape). ``endpoint`` is the source collection (e.g.
        ``/api/3/preprocessing_rules``).
        """
        info = self._get_named_record(endpoint, "name", name, kind)
        return RuleSelection(name=info["name"], uuid=info["uuid"], value=info["uuid"]).wire()

    def _get_named_record(self, endpoint: str, key: str, value: str, kind: str) -> dict[str, Any]:
        """Resolve a record by a name-like field (``name``/``title``) to its full record."""
        members = extract_members(self.client.get(endpoint, params={key: value}))
        if not members:
            raise ValueError(f"{kind} {value!r} not found")
        return members[0]

    def _get_role_info(self, name: str) -> dict[str, Any]:
        """Resolve an RBAC role by name to its record (``@id``/``label``/``uuid``)."""
        return self._get_named_record("/api/3/roles", "name", name, "role")

    def export_record_data(
        self,
        module: str,
        *,
        query: Any = None,
        limit: int = _DEFAULT_RECORD_LIMIT,
        include_correlations: bool = False,
        output_path: str | None = None,
        label: str | None = None,
        cleanup_template: bool = True,
        poll_interval: int = 3,
    ) -> str:
        """Export a module's **records** (optionally filtered) to a ``.zip`` in one call.

        The SDK equivalent of the export wizard's record-set export: build a
        throwaway template with one filtered record set, run the export,
        download the archive, and — unless ``cleanup_template=False`` — delete the
        temporary template.

        Args:
            module: the module whose records to export (e.g. ``"alerts"``).
            query: a :class:`~pyfsr.query.Query`, a raw ``Query.to_body()`` dict,
                or ``None`` to export every record (up to ``limit``).
            limit: max records to emit (the required export trigger — see
                :meth:`ExportTemplate.add_record_set`). Raise for large sets.
            include_correlations: also pull correlated/linked records.
            output_path: where to write the ``.zip`` (default: cwd, derived name).
            label: friendly record-set name (defaults to ``module``).
            cleanup_template: delete the temporary export template afterwards.
            poll_interval: seconds between export-status polls.

        Returns:
            Path to the downloaded ``.zip``.

        Example:
            >>> from pyfsr import Query
            >>> path = client.export_config.export_record_data(  # doctest: +SKIP
            ...     "alerts",
            ...     query=Query(module="alerts").eq("status", "Open"),
            ...     limit=5000,
            ...     include_correlations=True,
            ...     output_path="open_alerts.zip",
            ... )
        """
        self._check_auth_support(operation=BaseAuth.OPERATION_CONFIG_EXPORT)

        template = ExportTemplate(f"pyfsr_records_{module}").add_record_set(
            module, query=query, limit=limit, include_correlations=include_correlations, label=label
        )
        created = self.create_template(template)
        template_uuid = created["@id"].split("/")[-1]

        try:
            if not output_path:
                output_path = os.path.join(os.getcwd(), f"{module}_records.zip".replace("/", "_"))
            return self._export_with_template(
                template_uuid=template_uuid,
                output_path=output_path,
                filename=f"{template.name}.zip",
                poll_interval=poll_interval,
            )
        finally:
            if cleanup_template:
                try:
                    self.delete_template(template_uuid)
                except Exception:  # pragma: no cover - cleanup is best-effort
                    pass

    def delete_template(self, template_uuid: str) -> None:
        """Delete an export template by uuid (``DELETE /api/3/export_templates/<uuid>``)."""
        self.client.delete(f"/api/3/export_templates/{template_uuid}")

    def export_connector(
        self,
        connector_name: str,
        output_path: str | None = None,
        *,
        include_configurations: bool = True,
        cleanup_template: bool = True,
        poll_interval: int = 3,
    ) -> str:
        """Export a single **installed** connector (with its configs) to a ``.zip``.

        Builds a one-connector export template straight from the installed
        connector record (so it works for installed-only connectors that the
        Content Hub search wouldn't surface), triggers the export, downloads the
        archive, and — unless ``cleanup_template=False`` — deletes the throwaway
        template it created.

        The downloaded ``.zip`` contains ``<file>/connectors/data.json`` whose
        ``configurations[]`` preserve each ``config_id`` and carry secrets in the
        appliance's encrypted form — feed it straight to
        ``client.import_config.import_file`` to restore.

        Args:
            connector_name: connector machine name (e.g. ``"code-snippet"``).
            output_path: where to write the ``.zip`` (default: cwd, derived name).
            include_configurations: include the connector's saved configs
                (default True — the whole point of a backup).
            cleanup_template: delete the temporary export template afterwards.
            poll_interval: seconds between export-status polls.

        Returns:
            Path to the downloaded ``.zip``.

        Raises:
            ValueError: if ``connector_name`` is not installed.
        """
        self._check_auth_support(operation=BaseAuth.OPERATION_CONFIG_EXPORT)

        resp = self.client.get("/api/integration/connectors/", params={"name": connector_name})
        data = (resp or {}).get("data") or []
        record = next((c for c in data if c.get("name") == connector_name), None)
        if record is None:
            raise ValueError(f"{connector_name!r} is not installed")

        version = record["version"]
        entry = {
            "label": record.get("label") or connector_name,
            "value": f"cyops-connector-{connector_name}-{version}",
            "version": version,
            # system/RPM-shipped connectors install via RPM; uploaded ones don't.
            "rpm": bool(record.get("system") or record.get("rpm_installed")),
            "include": True,
            "configurations": include_configurations,
            "configCount": record.get("config_count") or 0,
            "recordCount": 0,
        }

        template_name = f"pyfsr_export_{connector_name}_{version}".replace(".", "_")
        template = self.create_export_template(name=template_name, options={"connectors": [entry]})
        template_uuid = template["@id"].split("/")[-1]

        try:
            if not output_path:
                output_path = os.path.join(os.getcwd(), f"{connector_name}-{version}.zip".replace("/", "_"))
            return self._export_with_template(
                template_uuid=template_uuid,
                output_path=output_path,
                filename=f"{template_name}.zip",
                poll_interval=poll_interval,
            )
        finally:
            if cleanup_template:
                try:
                    self.delete_template(template_uuid)
                except Exception:  # pragma: no cover - cleanup is best-effort
                    pass
