from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

import requests

from ..pagination import extract_members
from .base import BaseAPI

if TYPE_CHECKING:
    from ..models import (
        ConnectorVersionInfo,
        ContentHubConnector,
        ContentHubItem,
        SolutionPack,
        Widget,
    )

_REPO_HOST = "https://repo.fortisoar.fortinet.com"
_REPO_BASE = f"{_REPO_HOST}/content-hub"


class ContentType(Enum):
    """Types of content that can be searched for in FortiSOAR Content Hub"""

    SOLUTION_PACK = "solutionpack"
    CONNECTOR = "connector"
    WIDGET = "widget"


def _model_for_type(content_type: ContentType):
    """Return the typed model class for a Content Hub ``content_type``."""
    from ..models import ContentHubConnector, SolutionPack, Widget

    return {
        ContentType.SOLUTION_PACK: SolutionPack,
        ContentType.CONNECTOR: ContentHubConnector,
        ContentType.WIDGET: Widget,
    }[content_type]


class ContentHubSearch(BaseAPI):
    """
    API implementation for searching FortiSOAR Content Hub items including
    solution packs, connectors, and widgets.

    Every search/find method returns the matching typed model (``SolutionPack``
    / ``ContentHubConnector`` / ``Widget``). Those models subclass
    ``BaseRecord`` and stay dict-compatible (``item["label"]`` / ``item.get(...)``
    work alongside ``item.label``), so the typed view loses nothing.
    """

    def _search_content(
        self,
        content_type: ContentType,
        installed: bool = True,
        search_term: str = "",
        limit: int = 30,
        extra_filters: list[dict[str, Any]] | None = None,
        extra_fields: list[str] | None = None,
    ) -> list[ContentHubItem]:
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
            The matching content items as typed, dict-compatible models
            (``SolutionPack`` / ``ContentHubConnector`` / ``Widget``).
        """
        query = {
            "sort": [
                {"field": "featured", "direction": "DESC"},
                {"field": "label", "direction": "ASC"},
            ],
            "limit": limit,
            "logic": "AND",
            "filters": [
                {"field": "type", "operator": "in", "value": [content_type.value]},
            ],
            "search": search_term,
        }

        # Add installed filter if specified
        if installed is not None:
            query["filters"].append({"field": "installed", "operator": "eq", "value": installed})

        # Add any extra filters
        if extra_filters:
            query["filters"].extend(extra_filters)

        # Add fields selection if provided
        fields = [
            "uuid",
            "name",
            "installed",
            "type",
            "display",
            "label",
            "version",
            "publisher",
            "certified",
            "iconLarge",
            "description",
            "latestAvailableVersion",
            "draft",
            "local",
            "status",
            "featuredTags",
            "featured",
        ]
        if extra_fields:
            fields.extend(extra_fields)
        query["__selectFields"] = fields

        response = self.client.post(
            f"/api/query/solutionpacks?$limit={limit}&$page=1&$search={search_term}", data=query
        )
        members = extract_members(response)
        model = _model_for_type(content_type)
        return [model(**m) for m in members]

    def _find_single_content(
        self,
        content_type: ContentType,
        search_term: str,
        installed: bool = True,
    ) -> ContentHubItem | None:
        """Find a single content item matching the search criteria."""
        results = self._search_content(
            content_type=content_type,
            installed=installed,
            search_term=search_term,
            limit=1,
        )
        return results[0] if results else None

    # Solution Pack Methods
    def find_installed_pack(self, search_term: str) -> SolutionPack | None:
        """Find a single installed solution pack by name, label, or description.

        Returns a dict-compatible ``SolutionPack`` (or ``None``).

        Example:
            .. code-block:: python

                pack = content_hub.find_installed_pack("SOAR Framework")
        """
        return self._find_single_content(ContentType.SOLUTION_PACK, search_term, installed=True)

    def find_available_pack(self, search_term: str = "") -> SolutionPack | None:
        """Find a single available solution pack by name, label, or description.

        Returns a dict-compatible ``SolutionPack`` (or ``None``).
        """
        return self._find_single_content(ContentType.SOLUTION_PACK, search_term, installed=False)

    def search_installed_packs(self, search_term: str = "", limit: int = 30) -> list[SolutionPack]:
        """Search for all installed solution packs matching the search criteria.

        Returns dict-compatible ``SolutionPack`` objects.

        Example:
            .. code-block:: python

                packs = content_hub.search_installed_packs("SOAR", limit=10)
        """
        return self._search_content(
            ContentType.SOLUTION_PACK,
            installed=True,
            search_term=search_term,
            limit=limit,
        )

    def search_available_packs(self, search_term: str = "", limit: int = 30) -> list[SolutionPack]:
        """Search for all available solution packs matching the search criteria.

        Returns dict-compatible ``SolutionPack`` objects.
        """
        return self._search_content(
            ContentType.SOLUTION_PACK,
            installed=False,
            search_term=search_term,
            limit=limit,
        )

    # Connector Methods
    def find_installed_connector(self, search_term: str) -> ContentHubConnector | None:
        """Find a single installed connector by name, label, or description.

        Returns a dict-compatible ``ContentHubConnector`` (or ``None``).
        """
        return self._find_single_content(ContentType.CONNECTOR, search_term, installed=True)

    def find_available_connector(self, search_term: str = "") -> ContentHubConnector | None:
        """Find a single available connector by name, label, or description.

        Returns a dict-compatible ``ContentHubConnector`` (or ``None``).
        """
        return self._find_single_content(ContentType.CONNECTOR, search_term, installed=None)

    def find_uninstalled_connector(self, search_term: str) -> ContentHubConnector | None:
        """Find a single uninstalled connector by name, label, or description.

        Returns a dict-compatible ``ContentHubConnector`` (or ``None``).
        """
        return self._find_single_content(ContentType.CONNECTOR, search_term, installed=False)

    def search_installed_connectors(self, search_term: str = "", limit: int = 30) -> list[ContentHubConnector]:
        """Search for all installed connectors matching the search criteria.

        Returns dict-compatible ``ContentHubConnector`` objects.
        """
        return self._search_content(
            ContentType.CONNECTOR,
            installed=True,
            search_term=search_term,
            limit=limit,
        )

    def search_available_connectors(self, search_term: str = "", limit: int = 30) -> list[ContentHubConnector]:
        """Search for all available connectors matching the search criteria.

        Returns dict-compatible ``ContentHubConnector`` objects.
        """
        return self._search_content(
            ContentType.CONNECTOR,
            installed=None,
            search_term=search_term,
            limit=limit,
        )

    def search_uninstalled_connectors(self, search_term: str = "", limit: int = 30) -> list[ContentHubConnector]:
        """Search for all uninstalled connectors matching the search criteria.

        Returns dict-compatible ``ContentHubConnector`` objects.
        """
        return self._search_content(
            ContentType.CONNECTOR,
            installed=False,
            search_term=search_term,
            limit=limit,
        )

    # Widget Methods
    def find_installed_widget(self, search_term: str) -> Widget | None:
        """Find a single installed widget by name, label, or description.

        Returns a dict-compatible ``Widget`` (or ``None``).
        """
        return self._find_single_content(ContentType.WIDGET, search_term, installed=True)

    def find_available_widget(self, search_term: str = "") -> Widget | None:
        """Find a single available widget by name, label, or description.

        Returns a dict-compatible ``Widget`` (or ``None``).
        """
        return self._find_single_content(ContentType.WIDGET, search_term, installed=False)

    def search_installed_widgets(self, search_term: str = "", limit: int = 30) -> list[Widget]:
        """Search for all installed widgets matching the search criteria.

        Returns dict-compatible ``Widget`` objects.
        """
        return self._search_content(
            ContentType.WIDGET,
            installed=True,
            search_term=search_term,
            limit=limit,
        )

    def search_available_widgets(self, search_term: str = "", limit: int = 30) -> list[Widget]:
        """Search for all available widgets matching the search criteria.

        Returns dict-compatible ``Widget`` objects.
        """
        return self._search_content(
            ContentType.WIDGET,
            installed=False,
            search_term=search_term,
            limit=limit,
        )

    def connector_versions(self, name: str) -> ConnectorVersionInfo:
        """Return all published versions of a connector from Fortinet's public repo.

        Searches the local solutionpacks API for ``name`` (fuzzy — partial
        matches work), then follows the ``infoPath`` on the best-matching cloud
        record to fetch ``{repo}/latest/info.json``. That public endpoint
        requires no authentication and returns ``availableVersions`` listing
        every version ever published.

        ``name`` is a connector slug or partial name (e.g. ``"code-snippet"``
        or ``"code"``). Raises ``ValueError`` if nothing cloud-backed is found
        (box may not have FDN access, or name doesn't match any connector).

        Returns the full info.json payload as a dict-compatible
        :class:`~pyfsr.models.ConnectorVersionInfo` (``availableVersions``,
        ``operations``, ``releaseNotes``, etc.).

        Example::

            info = client.content_hub.connector_versions("code-snippet")
            print(info.availableVersions)        # or info["availableVersions"]
            # ['1.2.0', '1.2.1', ..., '2.2.1']
        """
        from ..models import ConnectorVersionInfo

        results = self._search_content(
            ContentType.CONNECTOR,
            installed=None,
            search_term=name,
            limit=10,
            extra_fields=["latestAvailableVersion", "infoPath"],
        )
        cloud = [r for r in results if not r.get("local")]
        if not cloud:
            raise ValueError(
                f"no cloud-backed connector found for {name!r} "
                "(box may not have FDN access, or name doesn't match any connector)"
            )
        # Prefer exact name match; otherwise take first cloud result
        match = next((r for r in cloud if r.get("name") == name), cloud[0])

        # Build the repo URL. If the record advertises a latestAvailableVersion use
        # that to get the most current info.json; otherwise derive from infoPath.
        latest = match.get("latestAvailableVersion")
        if latest:
            connector_name = match.get("name", name)
            url = f"{_REPO_BASE}/{connector_name}-{latest}/latest/info.json"
        else:
            info_path: str = match["infoPath"]
            if not info_path.startswith("http"):
                info_path = f"{_REPO_HOST}{info_path}"
            repo_base = info_path.rsplit("/", 1)[0]  # drop the buildNumber
            url = f"{repo_base}/latest/info.json"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return ConnectorVersionInfo(**resp.json())
