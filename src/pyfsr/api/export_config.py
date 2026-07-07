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
from ..models._integration import ExportJobResult
from ..pagination import extract_members
from .base import BaseAPI
from .content_hub import ContentHubSearch

__all__ = ["ExportConfigAPI", "ExportTemplate"]


class ExportTemplate:
    """Typed builder for a Configuration Export template's ``options`` payload.

    Composes the same content the export wizard's steps do, then hands the
    result to :meth:`ExportConfigAPI.create_template`. Start with the module
    schema + record-data pieces; each ``add_*`` returns ``self`` so calls chain::

        from pyfsr import Query
        from pyfsr.api.export_config import ExportTemplate

        tmpl = (
            ExportTemplate("Open alerts backup")
            .add_module("alerts", fields=["name", "status", "severity"])
            .add_record_set(
                "alerts",
                query=Query(module="alerts").eq("status", "Open"),
                include_correlations=True,
            )
        )
        client.export_config.create_template(tmpl)

    ``add_module`` exports a module's **schema** (optionally limited to specific
    fields); ``add_record_set`` exports the module's **records** (data),
    optionally filtered by a query — the wizard's Modules-step record set. The
    ``recordSets`` entry shape (``label`` / ``type`` / ``includeCorrelations`` /
    ``include`` / ``query``) and its query structure are verified against the
    8.0.0 editor bundle; the ``query`` is the same dict :meth:`pyfsr.query.Query.to_body`
    produces, so a :class:`~pyfsr.query.Query` drops straight in.
    """

    def __init__(self, name: str, *, auto_select_picklists: bool = True) -> None:
        self.name = name
        self._auto_select_picklists = auto_select_picklists
        self._modules: list[dict[str, Any]] = []
        self._record_sets: list[dict[str, Any]] = []

    def add_module(self, module: str, *, fields: list[str] | None = None) -> "ExportTemplate":
        """Export a module's schema, optionally limited to ``fields`` (all fields if omitted)."""
        self._modules.append({"value": module, "includedAttributes": list(fields or [])})
        return self

    def add_record_set(
        self,
        module: str,
        *,
        query: Any = None,
        include_correlations: bool = False,
        label: str | None = None,
    ) -> "ExportTemplate":
        """Export a module's records (data), optionally filtered.

        Args:
            module: the module whose records to export (e.g. ``"alerts"``).
            query: a :class:`~pyfsr.query.Query`, a raw ``Query.to_body()`` dict,
                or ``None`` to export every record.
            include_correlations: also pull records correlated/linked to the
                matched records (the wizard's *Include Correlations* toggle).
            label: friendly name for the record set (defaults to ``module``).
        """
        if hasattr(query, "to_body"):
            q = query.to_body()
        elif query is None:
            q = {"logic": "AND", "filters": []}
        else:
            q = query
        self._record_sets.append(
            {
                "label": label or module,
                "type": module,
                "includeCorrelations": bool(include_correlations),
                "include": True,
                "query": q,
            }
        )
        return self

    def build(self) -> dict[str, Any]:
        """Return the template ``options`` dict — only the categories that were added."""
        options: dict[str, Any] = {}
        if self._modules:
            options["modules"] = self._modules
        if self._record_sets:
            options["recordSets"] = self._record_sets
        return options

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

        Example:
            >>> from pyfsr import Query
            >>> from pyfsr.api.export_config import ExportTemplate
            >>> tmpl = ExportTemplate("Open alerts").add_record_set(
            ...     "alerts", query=Query(module="alerts").eq("status", "Open")
            ... )
            >>> created = client.export_config.create_template(tmpl)  # doctest: +SKIP
        """
        return self.create_export_template(name=template.name, options=template.build(), metadata=template.metadata)

    def export_record_data(
        self,
        module: str,
        *,
        query: Any = None,
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
                or ``None`` to export every record.
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
            ...     include_correlations=True,
            ...     output_path="open_alerts.zip",
            ... )
        """
        self._check_auth_support(operation=BaseAuth.OPERATION_CONFIG_EXPORT)

        template = ExportTemplate(f"pyfsr_records_{module}").add_record_set(
            module, query=query, include_correlations=include_correlations, label=label
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
