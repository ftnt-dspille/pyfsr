"""Solution packs — ``client.solution_packs``.

Install, export, and manage FortiSOAR solution packs (bundled modules,
playbooks, connectors, and views shipped as one unit). Packs can be resolved by
name, label, or search term; install polls to completion and export writes the
pack archive to disk. Content discovery is delegated to
:class:`~pyfsr.api.content_hub.ContentHubSearch`.

Example:
    >>> client = demo_client()
    >>> resp = client.solution_packs.install("SOAR Framework", "2.2.1")
    >>> resp.name
    'SOAR Framework'
    >>> resp.job_id
    '990e8400-e29b-41d4-a716-446655440012'
"""

import time
from typing import Any

from ..exceptions import APIError
from ..models._integration import InstallJobStatus
from ..models._system import SolutionPackInstallResponse
from ._solutionpacks import upload_solutionpack
from .base import BaseAPI
from .content_hub import ContentHubSearch
from .export_config import ExportConfigAPI, SolutionPackBuilder

_INSTALL_TERMINAL = {"import complete", "import failed", "error"}
_INSTALL_FIELDS = ["status", "progressPercent", "errorMessage", "currentlyImporting"]


class SolutionPackAPI(BaseAPI):
    """
    API implementation for FortiSOAR Solution Pack operations
    """

    def __init__(self, client):
        super().__init__(client)
        self.export_config = ExportConfigAPI(client)
        self.content_hub = ContentHubSearch(client)

    def export_pack(self, pack_identifier: str, output_path: str | None = None, poll_interval: int = 5) -> str:
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

                export_path = client.solution_packs.export_pack("SOAR Framework")
                print(f"Exported to: {export_path}")
        """
        pack = self.content_hub.find_installed_pack(pack_identifier)

        if not pack:
            raise ValueError(f"An Installed Solution pack was not found with the search term: {pack_identifier}")

        # The catalog lookup does not expand relationships, so ``template`` is
        # absent there; re-fetch the pack with relationships to get its export
        # template (the row the server ties the pack export to).
        template = pack.get("template")
        if not template:
            uuid = pack.get("uuid") or pack.get("@id", "").rstrip("/").split("/")[-1]
            full = self.client.get(f"/api/3/solutionpacks/{uuid}?$relationships=true") if uuid else {}
            template = full.get("template") if isinstance(full, dict) else None

        if not template:
            raise ValueError(f"Solution Pack {pack_identifier} has no export template")

        template_uuid = template["uuid"] if isinstance(template, dict) else str(template).rstrip("/").split("/")[-1]

        if not output_path:
            # The export payload is a .zip archive, not JSON — name it accordingly.
            output_path = f"{pack['name']}_{pack['version']}.zip"

        return self.export_config.export_by_template_uuid(
            template_uuid=template_uuid, output_path=output_path, poll_interval=poll_interval
        )

    def install(
        self,
        name: str,
        version: str,
        *,
        build_number: int | str | None = None,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> SolutionPackInstallResponse | InstallJobStatus:
        """Install a solution pack from Content Hub by ``name`` + ``version``.

        Posts ``{"name", "version"}`` to ``POST /api/3/solutionpacks/install`` —
        the same call the Content Hub *Install* button makes. The install runs
        asynchronously as an import job.

        ``build_number`` selects which build of ``version`` to fetch. **Omit it and
        the appliance falls back to the repo's ``latest`` build path** — which 404s
        on a repo that publishes numbered builds without a ``latest`` alias (a
        self-hosted mirror typically does). The appliance reports that 404 as
        ``Unable to download <name> file. Please check the network connection to
        <repo>``, which blames the network for what is really a missing artifact —
        so if you hit that error against a working repo, pass the ``buildNumber``
        from the catalog row (``client.content_hub.search_available_packs()``)
        rather than debugging connectivity.

        Discover installable packs via
        ``client.content_hub.search_available_packs()``.

        Args:
            name: solution pack name (e.g. ``"SOAR Framework"``).
            version: the Content Hub version to install (e.g. ``"1.0.0"``).
            build_number: the build of ``version`` to fetch. Omitted → the
                appliance uses the repo's ``latest`` build path.
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            :class:`~pyfsr.models.SolutionPackInstallResponse` (the SolutionPack
            record plus ``importJob``) when ``wait=False`` so callers can access
            ``.job_id`` for polling. :class:`~pyfsr.models.InstallJobStatus` when
            ``wait=True`` — check ``status == "Import Complete"`` for success.

        Example:
            >>> client = demo_client()
            >>> resp = client.solution_packs.install("SOAR Framework", "2.2.1")
            >>> resp.name
            'SOAR Framework'
            >>> resp.version
            '2.2.1'
            >>> resp.job_id
            '990e8400-e29b-41d4-a716-446655440012'
        """
        body: dict[str, Any] = {"name": name, "version": version}
        if build_number is not None:
            body["buildNumber"] = build_number
        resp = self.client.post("/api/3/solutionpacks/install", data=body)
        if not isinstance(resp, dict):
            return SolutionPackInstallResponse()
        install_resp = SolutionPackInstallResponse.model_validate(resp)
        if not wait or not install_resp.job_id:
            return install_resp
        return self.wait_for_install(install_resp.job_id, interval=interval, timeout=timeout)

    def install_status(self, job_id: str) -> InstallJobStatus:
        """Fetch a solution pack install's import-job progress.

        ``GET /api/3/import_jobs/{job_id}`` (selecting just the progress fields).
        ``status == "Import Complete"`` means the install finished.

        A larger solution-pack import runs a schema migrate that briefly restarts
        the API, so this endpoint can answer ``503`` for a few seconds mid-import
        (the UI treats an ``import_jobs`` 503 the same way). That transient is
        reported as a non-terminal ``"Importing"`` status so :meth:`wait_for_install`
        keeps polling instead of aborting the wait.
        """
        try:
            resp = self.client.get(f"/api/3/import_jobs/{job_id}", params={"__selectFields": _INSTALL_FIELDS})
        except APIError as exc:
            if getattr(exc.response, "status_code", None) == 503:
                return InstallJobStatus(status="Importing")
            raise
        return InstallJobStatus.model_validate(resp if isinstance(resp, dict) else {"status": resp})

    def wait_for_install(self, job_id: str, *, interval: float = 3.0, timeout: float = 300.0) -> InstallJobStatus:
        """Poll an install import job until it reaches a terminal status.

        Returns the latest :class:`~pyfsr.models.InstallJobStatus`. On timeout,
        returns the last poll with a non-terminal ``status`` rather than raising.
        """
        deadline = time.monotonic() + timeout
        status = self.install_status(job_id)
        while str(status.status or "").strip().lower() not in _INSTALL_TERMINAL and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.install_status(job_id)
        return status

    def uninstall(self, name: str) -> None:
        """Uninstall a solution pack by name.

        Looks up the installed pack by ``name``, then sends
        ``DELETE /api/3/solutionpacks/{uuid}``. The appliance marks the pack
        as uninstalled (or removes it if it's a local/dev pack) and strips its
        import job, export job, template, and file references.

        Args:
            name: solution pack name or search term (e.g. ``"SOAR Framework"``).

        Raises:
            ValueError: if no installed pack matching ``name`` is found.

        Example:
            .. code-block:: python

                client.solution_packs.uninstall("SOAR Framework")
        """
        pack = self.content_hub.find_installed_pack(name)
        if not pack:
            raise ValueError(f"No installed solution pack found matching {name!r}")
        uuid = pack.get("uuid") or (
            pack.get("@id", "").rstrip("/").split("/")[-1] if "/" in pack.get("@id", "") else None
        )
        if not uuid:
            raise ValueError(f"Cannot resolve UUID for solution pack {name!r}")
        self.client.delete(f"/api/3/solutionpacks/{uuid}")

    def create(self, builder: SolutionPackBuilder, *, publish: bool = False) -> SolutionPackInstallResponse:
        """Author a new local solution pack from a :class:`SolutionPackBuilder`.

        Resolves the builder's content selection to a full export ``options``
        payload, then ``POST``\\s ``/api/3/solutionpacks`` with the pack metadata
        and a nested ``SolutionPack Export`` template — the same shape the Content
        Hub *Create Solution Pack* wizard posts. The pack is created ``local`` and
        ``draft``; ``publish=True`` marks it ``installed`` (available) rather than
        a development draft.

        Args:
            builder: the configured :class:`SolutionPackBuilder`.
            publish: create it published/available (``installed=True``) instead of
                a development draft.

        Returns:
            :class:`~pyfsr.models.SolutionPackInstallResponse` — the created pack
            record (``.uuid``, ``.name``, ``.version``).

        Example:
            .. code-block:: python

                from pyfsr.api.export_config import SolutionPackBuilder

                pack = (
                    SolutionPackBuilder("My SOC Pack", version="1.0.0")
                    .add_module("alerts")
                    .add_playbook_collection("Incident Response")
                    .post_install_widget("AI Assistant", "5.0.0", auto_launch=True)
                    .tags("Agentic AI")
                )
                created = client.solution_packs.create(pack, publish=True)
        """
        options = self.export_config._resolve_template_options(builder)
        body: dict[str, Any] = {
            "name": builder.name,
            "label": builder.label,
            "version": builder.version,
            "type": "solutionpack",
            "publisher": builder.publisher,
            "infoContent": builder.info_content(),
            "recordTags": list(builder._tags),
            "draft": True,
            "local": True,
            "installed": publish,
            "development": not publish,
            "template": {
                "name": builder.name,
                "options": options,
                "type": "SolutionPack Export",
            },
        }
        if builder.description is not None:
            body["description"] = builder.description
        if builder.min_compatibility is not None:
            body["fsrMinCompatibility"] = builder.min_compatibility
        if builder._categories:
            body["category"] = list(builder._categories)
        resp = self.client.post("/api/3/solutionpacks", data=body)
        return SolutionPackInstallResponse.model_validate(resp)

    def install_from_file(
        self,
        path: str,
        *,
        replace: bool = False,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> SolutionPackInstallResponse | InstallJobStatus:
        """Install a solution pack from a local ``.zip``/``.tgz`` bundle.

        Uploads the archive to ``POST /api/3/solutionpacks/install`` (``$type``
        defaults to ``solutionpack`` server-side), the same multipart endpoint the
        Content Hub *Upload* button uses. This is the file counterpart of
        :meth:`install` (which fetches by name/version from the repo) and returns
        the same shape — a pack record carrying the async import job.

        Args:
            path: filesystem path to the pack bundle.
            replace: overwrite an already-staged copy of this exact name+version
                (``$replace=true``).
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            :class:`~pyfsr.models.SolutionPackInstallResponse` (with ``.job_id``
            for polling) when ``wait=False``; :class:`~pyfsr.models.InstallJobStatus`
            when ``wait=True`` — check ``status == "Import Complete"``.

        Example:
            .. code-block:: python

                client.solution_packs.install_from_file("MyPack-1.0.0.zip", wait=True)
        """
        resp = upload_solutionpack(self.client, path, type_="solutionpack", replace=replace)
        parsed = SolutionPackInstallResponse.model_validate(resp)
        if not wait:
            return parsed
        if not parsed.job_id:
            raise ValueError(f"install_from_file: no import job id in response: {resp}")
        return self.wait_for_install(parsed.job_id, interval=interval, timeout=timeout)
