from enum import Enum
from typing import Dict, Any, Optional, List


class ContentType(Enum):
    """Types of content that can be searched for in FortiSOAR Content Hub"""
    SOLUTION_PACK = "solutionpack"
    CONNECTOR = "connector"
    WIDGET = "widget"


class ContentHubSearch:
    """
    API implementation for searching FortiSOAR Content Hub items including
    solution packs, connectors, and widgets.
    """

    def __init__(self, client):
        self.client = client

    def _search_content(
            self,
            content_type: ContentType,
            installed: bool = True,
            search_term: str = "",
            limit: int = 30,
            extra_filters: Optional[List[Dict[str, Any]]] = None,
            extra_fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generic search method for Content Hub items.

        Args:
            content_type: Type of content to search for
            installed: Whether to search for installed content only
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return
            extra_filters: Additional filters to apply to the query
            extra_fields: Additional fields to include in the response

        Returns:
            List[Dict[str, Any]]: List of matching content items
        """
        query = {
            "sort": [
                {"field": "featured", "direction": "DESC"},
                {"field": "label", "direction": "ASC"}
            ],
            "limit": limit,
            "logic": "AND",
            "filters": [
                {"field": "type", "operator": "in", "value": [content_type.value]},
            ],
            "search": search_term
        }

        # Add installed filter if specified
        if installed is not None:
            query["filters"].append({
                "field": "installed",
                "operator": "eq",
                "value": installed
            })

        # Add any extra filters
        if extra_filters:
            query["filters"].extend(extra_filters)

        # Add fields selection if provided
        fields = [
            "name", "installed", "type", "display", "label",
            "version", "publisher", "certified", "iconLarge",
            "description", "latestAvailableVersion", "draft",
            "local", "status", "featuredTags", "featured"
        ]
        if extra_fields:
            fields.extend(extra_fields)
        query["__selectFields"] = fields

        response = self.client.post(
            f'/api/query/solutionpacks?$limit={limit}&$page=1&$search={search_term}',
            data=query
        )
        return response.get('hydra:member', [])

    def _find_single_content(
            self,
            content_type: ContentType,
            search_term: str,
            installed: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Find a single content item matching the search criteria."""
        results = self._search_content(
            content_type=content_type,
            installed=installed,
            search_term=search_term,
            limit=1
        )
        return results[0] if results else None

    # Solution Pack Methods
    def find_installed_pack(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find a single installed solution pack by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching solution pack object, or None if no matches

        Example:
            .. code-block:: python

                pack = content_hub.find_installed_pack("SOAR Framework")
        """
        return self._find_single_content(ContentType.SOLUTION_PACK, search_term, installed=True)

    def find_available_pack(self, search_term: str = "") -> Optional[Dict[str, Any]]:
        """
        Find a single available solution pack by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching solution pack object, or None if no matches

        Example:
            .. code-block:: python

                pack = content_hub.find_available_pack("SOAR Framework")
        """
        return self._find_single_content(ContentType.SOLUTION_PACK, search_term, installed=False)

    def search_installed_packs(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all installed solution packs matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching solution pack objects

        Example:
            .. code-block:: python

                packs = content_hub.search_installed_packs("SOAR", limit=10)
        """
        return self._search_content(ContentType.SOLUTION_PACK, installed=True, search_term=search_term, limit=limit)

    def search_available_packs(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all available solution packs matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching solution pack objects

        Example:
            .. code-block:: python

                packs = content_hub.search_available_packs("SOAR", limit=10)
        """
        return self._search_content(ContentType.SOLUTION_PACK, installed=False, search_term=search_term, limit=limit)

    # Connector Methods
    def find_installed_connector(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find a single installed connector by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching connector object, or None if no matches

        Example:
            .. code-block:: python

                connector = content_hub.find_installed_connector("OpenAI")
        """
        return self._find_single_content(ContentType.CONNECTOR, search_term, installed=True)

    def find_available_connector(self, search_term: str = "") -> Optional[Dict[str, Any]]:
        """
        Find a single available connector by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching connector object, or None if no matches

        Example:
            .. code-block:: python

                connector = content_hub.find_available_connector("OpenAI")
        """
        return self._find_single_content(ContentType.CONNECTOR, search_term, installed=None)

    def find_uninstalled_connector(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find a single uninstalled connector by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching connector object, or None if no matches

        Example:
            .. code-block:: python

                connector = content_hub.find_uninstalled_connector("OpenAI")
        """
        return self._find_single_content(ContentType.CONNECTOR, search_term, installed=False)

    def search_installed_connectors(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all installed connectors matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching connector objects

        Example:
            .. code-block:: python

                connectors = content_hub.search_installed_connectors("OpenAI")
        """
        return self._search_content(ContentType.CONNECTOR, installed=True, search_term=search_term, limit=limit)

    def search_available_connectors(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all available connectors matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching connector objects

        Example:
            .. code-block:: python

                connectors = content_hub.search_available_connectors("OpenAI")
        """
        return self._search_content(ContentType.CONNECTOR, installed=None, search_term=search_term, limit=limit)

    def search_uninstalled_connectors(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all uninstalled connectors matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching connector objects

        Example:
            .. code-block:: python

                connectors = content_hub.search_uninstalled_connectors("OpenAI")
        """
        return self._search_content(ContentType.CONNECTOR, installed=False, search_term=search_term, limit=limit)

    # Widget Methods
    def find_installed_widget(self, search_term: str) -> Optional[Dict[str, Any]]:
        """
        Find a single installed widget by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching widget object, or None if no matches

        Example:
            .. code-block:: python

                widget = content_hub.find_installed_widget("Stats")
        """
        return self._find_single_content(ContentType.WIDGET, search_term, installed=True)

    def find_available_widget(self, search_term: str = "") -> Optional[Dict[str, Any]]:
        """
        Find a single available widget by name, label, or description.

        Args:
            search_term: Name, label, or description to search for

        Returns:
            Dict[str, Any]: The first matching widget object, or None if no matches

        Example:
            .. code-block:: python

                widget = content_hub.find_available_widget("Stats")
        """
        return self._find_single_content(ContentType.WIDGET, search_term, installed=False)

    def search_installed_widgets(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all installed widgets matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching widget objects

        Example:
            .. code-block:: python

                widgets = content_hub.search_installed_widgets("Stats")
        """
        return self._search_content(ContentType.WIDGET, installed=True, search_term=search_term, limit=limit)

    def search_available_widgets(self, search_term: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        """
        Search for all available widgets matching the search criteria.

        Args:
            search_term: Name, label, or description to search for
            limit: Maximum number of results to return

        Returns:
            List[Dict[str, Any]]: List of matching widget objects

        Example:
            .. code-block:: python

                widgets = content_hub.search_available_widgets("Stats")
        """
        return self._search_content(ContentType.WIDGET, installed=False, search_term=search_term, limit=limit)
