from .base import BaseAPI
from .content_hub import ContentHubSearch


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
