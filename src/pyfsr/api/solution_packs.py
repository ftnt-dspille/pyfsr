import time

from ..models._integration import InstallJobStatus
from .base import BaseAPI
from .connectors import _import_job_id
from .content_hub import ContentHubSearch

_INSTALL_TERMINAL = {"import complete", "import failed", "error"}
_INSTALL_FIELDS = ["status", "progressPercent", "errorMessage", "currentlyImporting"]


class SolutionPackAPI(BaseAPI):
    """
    API implementation for FortiSOAR Solution Pack operations
    """

    def __init__(self, client, export_config):
        super().__init__(client)
        self.export_config = export_config
        self.content_hub = ContentHubSearch(client)

    def export_pack(
        self, pack_identifier: str, output_path: str | None = None, poll_interval: int = 5
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

                export_path = client.solution_packs.export_pack("SOAR Framework")
                print(f"Exported to: {export_path}")
        """
        pack = self.content_hub.find_installed_pack(pack_identifier)

        if not pack:
            raise ValueError(
                f"An Installed Solution pack was not found with the search term: {pack_identifier}"
            )

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
    ) -> InstallJobStatus:
        """Install a solution pack from Content Hub by ``name`` + ``version``.

        Posts ``{"name", "version"}`` to ``POST /api/3/solutionpacks/install`` —
        the same call the Content Hub *Install* button makes. The install runs
        asynchronously as an import job; with ``wait=True`` this method blocks
        until the job reaches a terminal status and returns the final
        :class:`~pyfsr.models.InstallJobStatus`.

        Discover installable packs via
        ``client.content_hub.search_available_packs()``.

        Args:
            name: solution pack name (e.g. ``"SOAR Framework"``).
            version: the Content Hub version to install (e.g. ``"1.0.0"``).
            wait: block until the import job reaches a terminal status.
            interval: seconds between polls when ``wait`` (default 3).
            timeout: give up waiting after this many seconds (default 300).

        Returns:
            ``InstallJobStatus`` — immediately after the POST if ``wait=False``
            (only ``status`` is populated from the response), or the final
            polled status when ``wait=True``.

        Example:
            .. code-block:: python

                status = client.solution_packs.install("SOAR Framework", "2.2.1", wait=True)
                print(status.status)  # "Import Complete"
        """
        resp = self.client.post(
            "/api/3/solutionpacks/install", data={"name": name, "version": version}
        )
        job_id = _import_job_id(resp) if isinstance(resp, dict) else None
        if not wait or not job_id:
            status_val = resp.get("status") if isinstance(resp, dict) else None
            return InstallJobStatus(status=status_val)
        return self.wait_for_install(job_id, interval=interval, timeout=timeout)

    def install_status(self, job_id: str) -> InstallJobStatus:
        """Fetch a solution pack install's import-job progress.

        ``GET /api/3/import_jobs/{job_id}`` (selecting just the progress fields).
        ``status == "Import Complete"`` means the install finished.
        """
        resp = self.client.get(
            f"/api/3/import_jobs/{job_id}", params={"__selectFields": _INSTALL_FIELDS}
        )
        return InstallJobStatus.model_validate(resp if isinstance(resp, dict) else {"status": resp})

    def wait_for_install(
        self, job_id: str, *, interval: float = 3.0, timeout: float = 300.0
    ) -> InstallJobStatus:
        """Poll an install import job until it reaches a terminal status.

        Returns the latest :class:`~pyfsr.models.InstallJobStatus`. On timeout,
        returns the last poll with a non-terminal ``status`` rather than raising.
        """
        deadline = time.monotonic() + timeout
        status = self.install_status(job_id)
        while (
            str(status.status or "").strip().lower() not in _INSTALL_TERMINAL
            and time.monotonic() < deadline
        ):
            time.sleep(interval)
            status = self.install_status(job_id)
        return status
