from typing import Dict, Any, Optional


class SolutionPackAPI:
    """
    API implementation for FortiSOAR Solution Pack operations
    """

    def __init__(self, client, export_config):
        self.client = client
        self.export_config = export_config
        self._pack_cache = {}

    def _get_pack_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a solution pack by its exact name."""
        if name in self._pack_cache:
            return self._pack_cache[name]

        pack = self.find_installed_pack(name)
        if pack and pack['name'] == name:
            return pack

        pack = self.find_available_pack(name)
        if pack and pack['name'] == name:
            return pack

        return None

    def find_installed_pack(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find an installed solution pack by name, label, or description. This is similar to the find_available_pack
        method, but only returns installed packs.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The solution pack object

        Example:
            .. code-block:: python
                pack = client.solution_packs.find_installed_pack("SOAR Framework")

        """
        query = {
            "sort": [{"field": "label", "direction": "ASC"}],
            "limit": 30,
            "logic": "AND",
            "filters": [
                {"field": "type", "operator": "in", "value": ["solutionpack"]},
                {"field": "installed", "operator": "eq", "value": True},
                {
                    "logic": "OR",
                    "filters": [
                        {"field": "development", "operator": "eq", "value": False},
                        {"field": "type", "operator": "eq", "value": "widget"},
                        {"field": "type", "operator": "eq", "value": "solutionpack"}
                    ]
                }
            ],
            "search": search_term
        }

        response = self.client.post(f'/api/query/solutionpacks?$limit=30&$page=1&$search={search_term}', data=query)
        packs = response.get('hydra:member', [])

        if not packs:
            return None

        pack = packs[0]
        self._pack_cache[pack['name']] = pack
        return pack

    def find_available_pack(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find an available (not necessarily installed) solution pack.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The solution pack object

        Example:
            .. code-block:: python

                pack = client.solution_packs.find_available_pack("SOAR Framework")
        """
        query = {
            "sort": [
                {"field": "featured", "direction": "DESC"},
                {"field": "label", "direction": "ASC"}
            ],
            "limit": 30,
            "logic": "AND",
            "filters": [
                {"field": "type", "operator": "in", "value": ["solutionpack"]},
                {"field": "version", "operator": "notlike", "value": "%_dev"}
            ],
            "__selectFields": [
                "name", "installed", "type", "display", "label",
                "version", "publisher", "certified", "iconLarge",
                "description", "latestAvailableVersion", "draft",
                "local", "status", "featuredTags", "featured"
            ],
            "search": search_term
        }

        response = self.client.post('/api/query/solutionpacks', data=query)
        packs = response.get('hydra:member', [])

        if not packs:
            return None

        pack = packs[0]
        self._pack_cache[pack['name']] = pack
        return pack

    def export_pack(
            self,
            pack_identifier: str,
            output_path: Optional[str] = None,
            poll_interval: int = 5
    ) -> str:
        """
        Export a solution pack by name, label, or search term.

        Args:
            pack_identifier: Name, label or search term to find the pack
            output_path: Optional path to save exported file
            poll_interval: How often to check export status in seconds

        Returns:
            Path where the exported file was saved

        Example:
            .. code-block:: python

                # Export a solution pack by name
                export_path = client.solution_packs.export_pack("SOAR Framework")
                print(f"Exported to: {export_path}")
        """
        pack = self._get_pack_by_name(pack_identifier)
        if not pack:
            pack = self.find_installed_pack(pack_identifier)

        if not pack:
            raise ValueError(f"An Installed Solution pack was not found with the search term: {pack_identifier}")

        if not pack.get('template'):
            raise ValueError(f"Solution Pack {pack_identifier} has no export template")

        template_uuid = pack['template']['uuid']

        if not output_path:
            output_path = f"{pack['name']}_{pack['version']}.json"

        return self.export_config.export_by_template_uuid(
            template_uuid=template_uuid,
            output_path=output_path,
            poll_interval=poll_interval
        )
