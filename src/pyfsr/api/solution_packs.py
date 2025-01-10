from typing import Dict, Any, Optional, List


class SolutionPackAPI:
    """
    API implementation for FortiSOAR Solution Pack operations
    """

    def __init__(self, client, export_config):
        self.client = client
        self.export_config = export_config

    def _get_pack_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a solution pack by its exact name."""

        pack = self.find_installed_pack(name)
        if pack and pack['name'] == name:
            return pack

        pack = self.find_available_pack(name)
        if pack and pack['name'] == name:
            return pack

        return None

    def find_installed_pack(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find a single installed solution pack by name, label, or description. Returns only the first
        matching pack found. For multiple results, use search_installed_packs() instead.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching solution pack object, or None if no matches

        Example:
            .. code-block:: python

                # Find single installed pack
                pack = client.solution_packs.find_installed_pack("SOAR Framework")
                if pack:
                    print(f"Found pack: {pack['name']}")
        """
        packs = self.search_installed_packs(search_term, limit=1)
        if not packs:
            return None

        pack = packs[0]
        return pack

    def search_installed_packs(
            self,
            search_term: str = "",
            limit: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Search for all installed solution packs matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return (default 30)

        Returns:
            List[Dict[str, Any]]: List of matching solution pack objects

        Example:
            .. code-block:: python

                # Search for multiple installed packs
                packs = client.solution_packs.search_installed_packs(
                    search_term="SOAR",
                    limit=10
                )
                for pack in packs:
                    print(f"Found pack: {pack['name']}")
        """
        query = {
            "sort": [{"field": "label", "direction": "ASC"}],
            "limit": limit,
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

        response = self.client.post(
            f'/api/query/solutionpacks?$limit={limit}&$page=1&$search={search_term}',
            data=query
        )
        return response.get('hydra:member', [])

    def find_available_pack(self, search_term: str = "") -> Optional[Dict[str, Any]]:
        """
        Find a single available solution pack by name, label, or description. Returns only the first
        matching pack found. For multiple results, use search_available_packs() instead.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching solution pack object, or None if no matches

        Example:
            .. code-block:: python

                # Find single available pack
                pack = client.solution_packs.find_available_pack("SOAR Framework")
                if pack:
                    print(f"Found pack: {pack['name']}")
        """
        packs = self.search_available_packs(search_term, limit=1)
        if not packs:
            return None

        pack = packs[0]
        return pack

    def search_available_packs(
            self,
            search_term: str = "",
            limit: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Search for all available solution packs matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return (default 30)

        Returns:
            List[Dict[str, Any]]: List of matching solution pack objects

        Example:
            .. code-block:: python

                # Search for multiple available packs
                packs = client.solution_packs.search_available_packs(
                    search_term="SOAR",
                    limit=10
                )
                for pack in packs:
                    print(f"Found pack: {pack['name']}")
        """
        query = {
            "sort": [
                {"field": "featured", "direction": "DESC"},
                {"field": "label", "direction": "ASC"}
            ],
            "limit": limit,
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

        response = self.client.post(f'/api/query/solutionpacks?$limit={limit}&$page=1&$search={search_term}',
                                    data=query)
        return response.get('hydra:member', [])

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
