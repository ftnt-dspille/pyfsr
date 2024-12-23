"""Enhanced Export Configuration API with user-friendly lookup capabilities"""

import os
import time
from typing import Dict, Any, Optional, List


class ExportConfigAPI:
    """Class to handle FortiSOAR export configuration operations"""

    def __init__(self, client):
        self.client = client
        self._picklist_cache = {}
        self._connector_cache = {}
        self._playbook_cache = {}
        self._template_cache = {}

    def _get_picklist_iri(self, picklist_name: str) -> str:
        """Look up picklist IRI by name"""
        if picklist_name not in self._picklist_cache:
            # Query picklist by name
            response = self.client.get('/api/3/picklist_names', params={'name': picklist_name})
            if response['hydra:member']:
                self._picklist_cache[picklist_name] = response['hydra:member'][0]['@id']
            else:
                raise ValueError(f"Picklist not found: {picklist_name}")
        return self._picklist_cache[picklist_name]

    def _get_connector_info(self, connector_name: str) -> Dict[str, Any]:
        """Look up connector details by name"""
        if connector_name not in self._connector_cache:
            # Query connector info
            response = self.client.get('/api/integration/connectors/')
            for connector in response['hydra:member']:
                if connector['label'] == connector_name:
                    self._connector_cache[connector_name] = {
                        'value': f"cyops-connector-{connector['name']}-{connector['version']}",
                        'version': connector['version'],
                        'label': connector['label']
                    }
                    break
            if connector_name not in self._connector_cache:
                raise ValueError(f"Connector not found: {connector_name}")
        return self._connector_cache[connector_name]

    def _get_playbook_collection_info(self, collection_name: str) -> Dict[str, Any]:
        """Look up playbook collection details by name"""
        if collection_name not in self._playbook_cache:
            # Query playbook collections
            response = self.client.get('/api/3/workflow_collections', params={'name': collection_name})
            if response['hydra:member']:
                collection = response['hydra:member'][0]
                self._playbook_cache[collection_name] = {
                    'label': collection['name'],
                    'value': collection['@id'].split('/')[-1]
                }
            else:
                raise ValueError(f"Playbook collection not found: {collection_name}")
        return self._playbook_cache[collection_name]

    def _get_template_uuid(self, template_name: str) -> str:
        """Look up template UUID by name"""
        if template_name not in self._template_cache:
            response = self.client.get('/api/3/export_templates', params={'name': template_name})
            templates = response.get('hydra:member', [])
            matching_templates = [t for t in templates if t['name'] == template_name]

            if not matching_templates:
                raise ValueError(f"Export template not found: {template_name}")

            # Get the most recently created template if multiple exist
            template = sorted(matching_templates,
                              key=lambda x: x.get('createDate', 0),
                              reverse=True)[0]
            self._template_cache[template_name] = template['@id'].split('/')[-1]

        return self._template_cache[template_name]

    def _trigger_export(self, template_uuid: str, filename: str) -> Dict[str, Any]:
        """Trigger the export process using a template"""
        if not filename.endswith('.zip'):
            raise ValueError("Filename must end in .zip")
        return self.client.put(f'/api/export?fileName={filename}&template={template_uuid}')

    def _get_export_status(self, job_uuid: str) -> Dict[str, Any]:
        """Get the status of an export job"""
        return self.client.get(f'/api/3/export_jobs/{job_uuid}')

    def _download_export(self, file_iri: str, download_path: Optional[str] = None) -> str:
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
        content = self.client.get(file_iri)  # Will return bytes due to content type

        if not download_path:
            filename = file_iri.split('/')[-1]
            download_path = os.path.join(os.getcwd(), filename)

        with open(download_path, 'wb') as f:
            if isinstance(content, bytes):
                f.write(content)
            else:
                raise TypeError(f"Expected bytes response, got {type(content)}")

        return download_path

    def _poll_export_completion(self, job_uuid: str, poll_interval: int = 5) -> Dict[str, Any]:
        """Poll until export is complete"""
        while True:
            status = self._get_export_status(job_uuid)
            if status['status'] == 'Export Complete':
                return status
            time.sleep(poll_interval)

    def _export_with_template(
            self,
            template_uuid: str,
            output_path: Optional[str] = None,
            filename: Optional[str] = None,
            poll_interval: int = 5
    ) -> str:
        """Common export workflow using template UUID"""
        if not filename:
            filename = f"export_{template_uuid}.zip"

        # Trigger export
        export_job = self._trigger_export(template_uuid, filename)
        job_uuid = export_job['jobUuid']

        # Poll until complete
        status = self._poll_export_completion(job_uuid, poll_interval)

        # Download file
        file_iri = status['file']['@id']
        return self._download_export(file_iri, output_path)

    def export_by_template_uuid(
            self,
            template_uuid: str,
            output_path: Optional[str] = None,
            poll_interval: int = 5
    ) -> str:
        """
        Export configuration using template UUID directly.

        Args:
            template_uuid: UUID of existing export template
            output_path: Optional path to save exported file
            poll_interval: How often to check export status in seconds

        Returns:
            Path where exported file was saved

        Example:
            >>> client = FortiSOAR('fortisoar.company.com', '<your-api-token>')
            >>> output_file = client.export_config.export_by_template_uuid(
            ...     template_uuid="123e4567-e89b-12d3-a456-426655440000",
            ...     output_path="exports/config.zip"
            ... )
        """
        return self._export_with_template(
            template_uuid=template_uuid,
            output_path=output_path,
            poll_interval=poll_interval
        )

    def export_by_template_name(
            self,
            template_name: str,
            output_path: Optional[str] = None,
            poll_interval: int = 5
    ) -> str:
        """
        Export configuration using template name.

        Args:
            template_name: Name of existing export template
            output_path: Optional path to save exported file
            poll_interval: How often to check export status in seconds

        Returns:
            Path where exported file was saved

        Example:
            >>> client = FortiSOAR('fortisoar.company.com', '<your-api-token>')
            >>> output_file = client.export_config.export_by_template_name(
            ...     template_name="Alert Configuration",
            ...     output_path="exports/alert_config.zip"
            ... )
        """
        template_uuid = self._get_template_uuid(template_name)
        filename = f"{template_name.lower().replace(' ', '_')}.zip" if not output_path else None

        return self._export_with_template(
            template_uuid=template_uuid,
            output_path=output_path,
            filename=filename,
            poll_interval=poll_interval
        )

    def create_simplified_template(
            self,
            name: str,
            modules: Optional[List[str]] = None,
            module_attributes: Optional[Dict[str, List[str]]] = None,
            picklists: Optional[List[str]] = None,
            connectors: Optional[List[str]] = None,
            playbook_collections: Optional[List[str]] = None,
            view_templates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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
            >>> client = FortiSOAR('fortisoar.company.com', '<your-api-token>')

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
                    "includedAttributes": module_attributes.get(module, []) if module_attributes else []
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
                connector_configs.append({
                    "label": info['label'],
                    "value": info['value'],
                    "rpm": True,
                    "configurations": True,
                    "configCount": 1,
                    "version": info['version'],
                    "include": True,
                    "recordCount": 0
                })

        # Look up playbook collection details
        playbook_config = {"collections": [], "globalVariables": []}
        if playbook_collections:
            for collection in playbook_collections:
                info = self._get_playbook_collection_info(collection)
                playbook_config["collections"].append({
                    "label": info['label'],
                    "value": info['value'],
                    "includeGlobalVariables": True,
                    "includeSchedules": True,
                    "includeVersions": True,
                    "include": True,
                    "recordCount": 0
                })

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
            "playbookBlocks": {"blocks": [], "includeGlobalVariables": True}
        }

        metadata = {"autoSelectPicklists": True}

        return self.create_export_template(
            name=name,
            options=options,
            metadata=metadata
        )

    def create_export_template(self, name: str, options: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> \
            Dict[str, Any]:
        """Create the actual export template - internal method"""
        template_data = {
            "name": name,
            "options": options,
            "metadata": metadata or {"autoSelectPicklists": True}
        }
        return self.client.post('/api/3/export_templates', data=template_data)
