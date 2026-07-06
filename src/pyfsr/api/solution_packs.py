"""Solution packs — ``client.solution_packs``.

Install, export, and manage FortiSOAR solution packs (bundled modules,
playbooks, connectors, and views shipped as one unit). Packs can be resolved by
name, label, or search term; install polls to completion and export writes the
pack archive to disk. Content discovery is delegated to
:class:`~pyfsr.api.content_hub.ContentHubSearch`.
"""

import time

from ..models._integration import InstallJobStatus
from ..models._system import SolutionPackInstallResponse
from .base import BaseAPI
from .content_hub import ContentHubSearch
from .export_config import ExportConfigAPI

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

        if not pack.get("template"):
            raise ValueError(f"Solution Pack {pack_identifier} has no export template")

        template_uuid = pack["template"]["uuid"]

        if not output_path:
            output_path = f"{pack['name']}_{pack['version']}.json"

        return self.export_config.export_by_template_uuid(
            template_uuid=template_uuid, output_path=output_path, poll_interval=poll_interval
        )

    def install(
        self,
        name: str,
        version: str,
        *,
        wait: bool = False,
        interval: float = 3.0,
        timeout: float = 300.0,
    ) -> SolutionPackInstallResponse | InstallJobStatus:
        """Install a solution pack from Content Hub by ``name`` + ``version``.

        Posts ``{"name", "version"}`` to ``POST /api/3/solutionpacks/install`` —
        the same call the Content Hub *Install* button makes. The install runs
        asynchronously as an import job.

        Discover installable packs via
        ``client.content_hub.search_available_packs()``.

        Args:
            name: solution pack name (e.g. ``"SOAR Framework"``).
            version: the Content Hub version to install (e.g. ``"1.0.0"``).
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            :class:`~pyfsr.models.SolutionPackInstallResponse` (the SolutionPack
            record plus ``importJob``) when ``wait=False`` so callers can access
            ``.job_id`` for polling. :class:`~pyfsr.models.InstallJobStatus` when
            ``wait=True`` — check ``status == "Import Complete"`` for success.

        Example:
            .. code-block:: python

                resp = client.solution_packs.install("SOAR Framework", "2.2.1")
                status = client.solution_packs.wait_for_install(resp.job_id)
                print(status.status)  # "Import Complete"

                # or in one call:
                status = client.solution_packs.install("SOAR Framework", "2.2.1", wait=True)
        """
        resp = self.client.post("/api/3/solutionpacks/install", data={"name": name, "version": version})
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
        """
        resp = self.client.get(f"/api/3/import_jobs/{job_id}", params={"__selectFields": _INSTALL_FIELDS})
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
