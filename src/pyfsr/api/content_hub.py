"""Content Hub discovery — ``client.content_hub``.

Search and discover installable content (connectors, solution packs, widgets, and
— on 8.0.0+ — AI agents) from Fortinet's Content Hub at
``repo.fortisoar.fortinet.com``. Use this to find what is *available* to install
on an appliance. For no-appliance discovery and direct artifact download, see
:mod:`pyfsr.repo`.

Every content type is served by one catalog endpoint and discriminated by its
``type`` field; :class:`ContentType` enumerates them.
"""

from __future__ import annotations

import logging
import re
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

import requests
from pydantic import BaseModel, ConfigDict, Field

from ..pagination import extract_members
from .base import BaseAPI

if TYPE_CHECKING:
    from ..models import (
        AIAgent,
        ConnectorVersionInfo,
        ContentHubConnector,
        ContentHubItem,
        SolutionPack,
        Widget,
    )

logger = logging.getLogger("pyfsr")

_REPO_HOST = "https://repo.fortisoar.fortinet.com"
_REPO_BASE = f"{_REPO_HOST}/content-hub"

#: Exact AI-agent lookup must see every installed agent, not the default page of
#: 30 — an appliance can carry more, and a fuzzy-ranked page could omit the exact
#: match entirely.
_ALL_AI_AGENTS = 2147483647


class ContentType(Enum):
    """Types of content that can be searched for in FortiSOAR Content Hub"""

    SOLUTION_PACK = "solutionpack"
    CONNECTOR = "connector"
    WIDGET = "widget"
    #: FortiSOAR 8.0.0+ only; earlier appliances have no ``ai_agent`` content.
    AI_AGENT = "ai_agent"


def _model_for_type(content_type: ContentType):
    """Return the typed model class for a Content Hub ``content_type``."""
    from ..models import AIAgent, ContentHubConnector, SolutionPack, Widget

    return {
        ContentType.SOLUTION_PACK: SolutionPack,
        ContentType.CONNECTOR: ContentHubConnector,
        ContentType.WIDGET: Widget,
        ContentType.AI_AGENT: AIAgent,
    }[content_type]


#: Content types the Content Hub *Update available* filter covers.
UPDATABLE_TYPES: tuple[str, ...] = ("solutionpack", "connector", "widget")
#: Update order across types: packs first (they may bundle newer connectors/widgets).
_TYPE_ORDER = {"solutionpack": 0, "connector": 1, "widget": 2}
_JOB_OK = {"import complete", "completed"}
_PUBLISH_BUSY = {"started", "in progress", "inprogress"}
#: Import-job states that are finished, or parked waiting on a person (not running).
_JOB_IDLE = {"import complete", "completed", "import failed", "failed", "error", "reviewing"}
#: An unfinished job untouched for longer than this is abandoned, not running.
RUNNING_JOB_MAX_AGE = 1800.0


def _version_tuple(v: str | None) -> tuple[int, ...]:
    """``"7.6.4"`` -> ``(7, 6, 4)``; non-numeric parts sort as 0. Proper ordering, unlike
    the GUI's ``parseInt(v.split(".").join(""))`` (which ranks 7.10.0 below 7.9.0)."""
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", str(v or "")) if p != "")


class ContentUpdate(BaseModel):
    """An installed Content Hub item with a newer version available."""

    model_config = ConfigDict(extra="ignore")

    name: str
    label: str | None = None
    type: str
    installed_version: str | None = None
    latest_version: str
    #: Lowest FortiSOAR version the update needs, when the hub states one (packs).
    min_fsr_version: str | None = None
    #: Names of solution packs this item depends on.
    depends_on: list[str] = Field(default_factory=list)
    uuid: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ContentUpdate:
        deps = [d.get("name") for d in (row.get("dependencies") or []) if isinstance(d, dict) and d.get("name")]
        return cls(
            name=row["name"],
            label=row.get("label"),
            type=row.get("type") or "",
            installed_version=row.get("version"),
            latest_version=row["latestAvailableVersion"],
            min_fsr_version=row.get("latestCompatibleVersion") or None,
            depends_on=deps,
            uuid=row.get("uuid"),
        )


class UpdateResult(BaseModel):
    """What :meth:`ContentHubSearch.update` did for one item."""

    name: str
    type: str
    from_version: str | None = None
    to_version: str
    #: ``updated`` | ``failed`` | ``skipped`` | ``unavailable`` (the content service refused it)
    #: | ``timeout`` | ``started`` (wait=False) | ``planned`` (dry run)
    status: str
    message: str | None = None
    job_id: str | None = None
    configs_before: int | None = None
    configs_after: int | None = None
    seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("updated", "started", "planned")


def _content_refusal(error: str) -> str | None:
    """Explain the box's content-service refusals (8.0+ downloads from FortiCloud, not the public repo).

    The catalog can advertise a version the content service will not serve this
    box; the update then fails with one of these, which is not a pyfsr or network error.
    """
    e = error.lower()
    if "premium content" in e:
        return "premium content: this box's license does not include it"
    if "content not available" in e:
        return (
            "the Content Hub service will not serve this version to this box (it is listed as an "
            "update, but not available for this FortiSOAR version or license)"
        )
    if "not allowed" in e and "content" in e:
        return "the Content Hub service does not allow this content for this box"
    return None


class ContentUpdateError(RuntimeError):
    """An update was refused before it was sent (incompatible box, publish running, not found)."""


def order_updates(items: list[ContentUpdate]) -> list[ContentUpdate]:
    """Packs first, each after the packs it depends on; then connectors, then widgets."""
    by_name = {i.name: i for i in items}
    out: list[ContentUpdate] = []
    seen: set[str] = set()

    def visit(item: ContentUpdate, stack: tuple[str, ...] = ()) -> None:
        if item.name in seen or item.name in stack:
            return
        for dep in item.depends_on:
            if dep in by_name:
                visit(by_name[dep], (*stack, item.name))
        seen.add(item.name)
        out.append(item)

    for item in sorted(items, key=lambda i: (_TYPE_ORDER.get(i.type, 9), (i.label or i.name).lower())):
        visit(item)
    return sorted(out, key=lambda i: _TYPE_ORDER.get(i.type, 9))  # stable: keeps dependency order per type


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

    # AI Agent Methods (FortiSOAR 8.0.0+)
    def search_installed_ai_agents(self, search_term: str = "", limit: int = 30) -> list[AIAgent]:
        """Search installed AI agents (fuzzy — name, label, or description).

        AI agents are Content Hub items served by the same catalog endpoint as
        packs and connectors, discriminated by ``type: "ai_agent"``. Requires
        FortiSOAR 8.0.0+; earlier appliances return nothing.

        Returns dict-compatible ``AIAgent`` objects.
        """
        return self._search_content(
            ContentType.AI_AGENT,
            installed=True,
            search_term=search_term,
            limit=limit,
        )

    def search_available_ai_agents(self, search_term: str = "", limit: int = 30) -> list[AIAgent]:
        """Search available (not-yet-installed) AI agents. FortiSOAR 8.0.0+."""
        return self._search_content(
            ContentType.AI_AGENT,
            installed=False,
            search_term=search_term,
            limit=limit,
        )

    def get_installed_ai_agent(self, name_or_label: str) -> AIAgent:
        """Resolve one installed AI agent by **exact** ``name`` or ``label``.

        Unlike the fuzzy ``search_*``/``find_*`` methods (which also match on
        description and would happily return a near-miss), this matches exactly —
        use it when the identity of the agent matters, e.g. resolving the agent an
        export template references. ``name`` is the agent id (``"conversation"``);
        ``label`` its display name (``"Chat Assistant"``). ``name`` wins a tie.

        Requires FortiSOAR 8.0.0+.

        Args:
            name_or_label: the agent's exact ``name`` or ``label``.

        Raises:
            ValueError: if no installed agent matches exactly.
        """
        agents = self.search_installed_ai_agents(limit=_ALL_AI_AGENTS)
        for agent in agents:
            if agent.name == name_or_label:
                return agent
        for agent in agents:
            if agent.label == name_or_label:
                return agent
        raise ValueError(f"AI agent {name_or_label!r} not found")

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

    # ------------------------------------------------------------------ updates
    def updates(
        self,
        types: str | list[str] | tuple[str, ...] | None = None,
        search: str = "",
        *,
        include_development: bool = False,
    ) -> list[ContentUpdate]:
        """Installed items with a newer version on the Content Hub.

        The same query the Content Hub page runs with *Update available* ticked:
        ``installed = true`` and ``latestAvailableVersion`` set, excluding
        development connectors (pass ``include_development=True`` to keep them).
        ``types`` is any of ``"solutionpack"``, ``"connector"``, ``"widget"``
        (default: all three). ``latestAvailableVersion`` is only as fresh as the
        box's last Content Hub sync (``pyfsr appliance content-hub sync``).
        Returned in the order :meth:`update_all` would apply them.
        """
        wanted = [types] if isinstance(types, str) else list(types or UPDATABLE_TYPES)
        filters: list[dict[str, Any]] = [
            {"field": "type", "operator": "in", "value": wanted},
            {"field": "latestAvailableVersion", "operator": "isnull", "value": False},
            {"field": "installed", "operator": "eq", "value": True},
        ]
        if not include_development:
            filters.append(
                {
                    "logic": "OR",
                    "filters": [
                        {"field": "development", "operator": "eq", "value": False},
                        {"field": "type", "operator": "eq", "value": "widget"},
                        {"field": "type", "operator": "eq", "value": "solutionpack"},
                    ],
                }
            )
        rows: list[dict[str, Any]] = []
        page, size = 1, 200
        while True:
            body: dict[str, Any] = {
                "sort": [{"field": "label", "direction": "ASC"}],
                "page": page,
                "limit": size,
                "logic": "AND",
                "filters": filters,
                "__selectFields": [
                    "uuid",
                    "name",
                    "label",
                    "type",
                    "version",
                    "installed",
                    "latestAvailableVersion",
                    "latestCompatibleVersion",
                    "dependencies",
                    "development",
                ],
            }
            if search:
                body["search"] = search
            members = extract_members(
                self.client.post(f"/api/query/solutionpacks?$limit={size}&$page={page}", data=body)
            )
            rows.extend(members)
            if len(members) < size:
                break
            page += 1
        items = [ContentUpdate.from_row(r) for r in rows if r.get("name") and r.get("latestAvailableVersion")]
        # Some boxes return a row whose "latest" is not newer (re-synced catalog); drop those.
        items = [i for i in items if _version_tuple(i.latest_version) > _version_tuple(i.installed_version)]
        return order_updates(items)

    def _box_version(self) -> str | None:
        try:
            v = self.client.version()
        except Exception:  # noqa: BLE001 - unknown version: skip the compatibility guard
            return None
        if isinstance(v, dict):
            v = v.get("version")
        return str(v) if v else None

    def _publish_running(self) -> bool:
        admin = getattr(self.client, "modules_admin", None)
        status_fn = getattr(admin, "_publish_status", None)
        if not callable(status_fn):
            return False
        body = status_fn()
        return isinstance(body, dict) and str(body.get("status") or "").strip().lower() in _PUBLISH_BUSY

    def _resolve_update(self, item: str | ContentUpdate, type: str | None) -> ContentUpdate:
        if isinstance(item, ContentUpdate):
            return item
        want = item.strip().lower()
        for u in self.updates(type):
            if want in (u.name.lower(), (u.label or "").lower()):
                return u
        raise ContentUpdateError(
            f"no update available for {item!r}"
            + (f" ({type})" if type else "")
            + " -- it is not installed, already current, or the box's Content Hub catalog is stale "
            "(pyfsr appliance content-hub sync)"
        )

    def running_installs(self, *, max_age: float = RUNNING_JOB_MAX_AGE) -> list[dict[str, Any]]:
        """Import jobs running on the box now (any source: Content Hub, Import Wizard, another session).

        A job counts when its status is not finished (or parked in *Reviewing*) and it
        changed in the last ``max_age`` seconds; older unfinished jobs are abandoned
        and would otherwise block forever.
        """
        resp = self.client.get(
            "/api/3/import_jobs",
            params={
                "$limit": 50,
                "$orderby": "-modifyDate",
                "__selectFields": "uuid,type,status,progressPercent,modifyDate,currentlyImporting",
            },
        )
        now = time.time()
        out = []
        for j in extract_members(resp):
            status = str(j.get("status") or "").strip().lower()
            modified = j.get("modifyDate")
            if status in _JOB_IDLE or not isinstance(modified, (int, float)):
                continue
            if now - float(modified) <= max_age:
                out.append(j)
        return out

    def wait_until_idle(self, *, timeout: float = 900.0, interval: float = 10.0) -> None:
        """Block until no import job is running; :class:`ContentUpdateError` after ``timeout``.

        FortiSOAR runs one import at a time; starting another while one runs
        corrupts or stalls both, so every update waits here first.
        """
        deadline = time.monotonic() + timeout
        while True:
            busy = self.running_installs()
            if not busy:
                return
            if time.monotonic() >= deadline:
                desc = ", ".join(
                    f"{j.get('type')} {j.get('status')} ({j.get('currentlyImporting') or j.get('progressPercent')})"
                    for j in busy
                )
                raise ContentUpdateError(f"another import is still running after {timeout:.0f}s: {desc}")
            time.sleep(interval)

    def update(
        self,
        item: str | ContentUpdate,
        *,
        type: str | None = None,
        version: str | None = None,
        build_number: int | str | None = None,
        wait: bool = True,
        interval: float = 5.0,
        timeout: float = 900.0,
        check_compatibility: bool = True,
    ) -> UpdateResult:
        """Update one installed item, the way the Content Hub *Update* button does.

        ``POST /api/3/solutionpacks/update {"name", "version"}`` for every type
        (pack, connector, widget), then polls the import job. ``item`` is a name or
        label, or a :class:`ContentUpdate` from :meth:`updates`; ``version``
        defaults to its ``latestAvailableVersion``.

        Refused before sending (:class:`ContentUpdateError`), as the GUI does:
        while a module publish is running, and for a pack whose update needs a
        newer FortiSOAR than this box (``check_compatibility=False`` skips that).

        No backup is taken. For a connector, the result reports how many
        configurations it had before and after, and the message says so if the
        update dropped any (to change a connector's version with a config backup
        and restore, use ``client.connectors.ensure_version``).
        """
        u = self._resolve_update(item, type)
        target = version or u.latest_version
        if self._publish_running():
            raise ContentUpdateError("a module publish is running on the box; update after it finishes")
        if check_compatibility and u.min_fsr_version:
            box = self._box_version()
            if box and _version_tuple(u.min_fsr_version) > _version_tuple(box):
                raise ContentUpdateError(
                    f"{u.label or u.name} {target} needs FortiSOAR {u.min_fsr_version} or later; this box runs {box}"
                )

        connectors = getattr(self.client, "connectors", None)
        before: int | None = None
        if u.type == "connector" and connectors is not None:
            try:
                before = len(connectors.configurations(u.name))
            except Exception:  # noqa: BLE001 - unreadable: report no counts
                before = None

        # One install at a time: wait out any import already running (GUI, another session).
        self.wait_until_idle(timeout=timeout, interval=interval)

        body: dict[str, Any] = {"name": u.name, "version": target}
        if build_number is not None:
            body["buildNumber"] = build_number
        started = time.monotonic()
        try:
            resp = self.client.post("/api/3/solutionpacks/update", data=body)
        except Exception as exc:  # noqa: BLE001 - name the content-service refusals; re-raise the rest
            reason = _content_refusal(str(exc))
            if reason is None:
                raise
            return UpdateResult(
                name=u.name,
                type=u.type,
                from_version=u.installed_version,
                to_version=target,
                status="unavailable",
                message=reason,
            )
        if isinstance(resp, str):  # "Solution pack already installed"
            return UpdateResult(
                name=u.name,
                type=u.type,
                from_version=u.installed_version,
                to_version=target,
                status="skipped",
                message=resp,
            )
        from ..models import SolutionPackInstallResponse

        job_id = SolutionPackInstallResponse.model_validate(resp).job_id if isinstance(resp, dict) else None
        result = UpdateResult(
            name=u.name,
            type=u.type,
            from_version=u.installed_version,
            to_version=target,
            status="started",
            job_id=job_id,
            configs_before=before,
        )
        if not wait or not job_id:
            return result
        final = self.client.solution_packs.wait_for_install(job_id, interval=interval, timeout=timeout)
        state = str(final.status or "").strip().lower()
        result.seconds = round(time.monotonic() - started, 1)
        if state in _JOB_OK:
            result.status = "updated"
        elif state not in _JOB_IDLE:
            result.status = "timeout"
            result.message = f"import job still {final.status!r} after {timeout:.0f}s; it may still be running"
        else:
            result.status = "failed"
            result.message = getattr(final, "errorMessage", None) or f"import job ended as {final.status!r}"

        if u.type == "connector" and connectors is not None:
            connectors.clear_cache()
            if before:
                after = len(connectors.configurations(u.name))
                if after < before:
                    lost = f"{before - after} of {before} configurations are gone after the update"
                    logger.warning("%s: %s", u.name, lost)
                    result.message = (result.message + "; " if result.message else "") + lost
                result.configs_after = after
        return result

    def update_all(
        self,
        types: str | list[str] | tuple[str, ...] | None = None,
        *,
        names: list[str] | None = None,
        dry_run: bool = False,
        stop_on_error: bool = False,
        **kwargs: Any,
    ) -> list[UpdateResult]:
        """Apply every available update (or those in ``names``), one at a time.

        Order: solution packs first, each after the packs it depends on; then
        connectors; then widgets. After the packs, the list is re-read so an item
        a pack already brought up to date is not updated twice. ``dry_run`` returns
        the plan without sending anything. Other keyword arguments go to
        :meth:`update`. A refused or failed item is recorded and the run moves on,
        unless ``stop_on_error``.

        Strictly one install at a time: each update waits for any running import
        (from the GUI or elsewhere) to finish before starting, and waits for its own
        import job to finish before the next begins. ``wait=False`` is rejected. If
        an item's job is still running at ``timeout``, or the box stays busy, the run
        stops there rather than overlapping installs.
        """
        if kwargs.pop("wait", True) is not True:
            raise ValueError("update_all() always waits: FortiSOAR runs one import at a time")
        wanted = {n.lower() for n in names} if names else None

        def pick(items: list[ContentUpdate]) -> list[ContentUpdate]:
            if wanted is None:
                return items
            return [i for i in items if i.name.lower() in wanted or (i.label or "").lower() in wanted]

        plan = pick(self.updates(types))
        if dry_run:
            return [
                UpdateResult(
                    name=i.name,
                    type=i.type,
                    from_version=i.installed_version,
                    to_version=i.latest_version,
                    status="planned",
                )
                for i in plan
            ]
        results: list[UpdateResult] = []
        packs = [i for i in plan if i.type == "solutionpack"]
        rest_types = sorted({i.type for i in plan if i.type != "solutionpack"}, key=lambda t: _TYPE_ORDER.get(t, 9))

        halted = False

        def run(item: ContentUpdate) -> bool:
            nonlocal halted
            try:
                r = self.update(item, wait=True, **kwargs)
            except ContentUpdateError as exc:
                if "another import is still running" in str(exc):
                    halted = True  # the box is busy: starting anything else would overlap

                r = UpdateResult(
                    name=item.name,
                    type=item.type,
                    from_version=item.installed_version,
                    to_version=item.latest_version,
                    status="skipped",
                    message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 - record and carry on
                r = UpdateResult(
                    name=item.name,
                    type=item.type,
                    from_version=item.installed_version,
                    to_version=item.latest_version,
                    status="failed",
                    message=f"{type(exc).__name__}: {str(exc).strip()}",
                )
            if r.status == "timeout":
                halted = True  # never start the next install while this one may still run
            results.append(r)
            return r.ok

        for item in packs:
            if not run(item) and (stop_on_error or halted):
                return results
        if rest_types:
            remaining = pick(self.updates(rest_types)) if packs else [i for i in plan if i.type != "solutionpack"]
            for item in remaining:
                if not run(item) and (stop_on_error or halted):
                    return results
        return results
