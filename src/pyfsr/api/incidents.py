from ._record_module import RecordModuleAPI


class IncidentsAPI(RecordModuleAPI):
    """Create and manage FortiSOAR incidents.

    Mirrors :class:`~pyfsr.api.alerts.AlertsAPI`: friendly picklist values
    (``severity``, ``status``, ``type``) are resolved to IRIs on create/update,
    and ``record=`` links the incident to a source alert.

    Example:
        .. code-block:: python

            client.incidents.create(
                name="INC — Suspicious Login",
                status="Open",
                severity="High",
                type="Malware",
                record="/api/3/alerts/1234-...",
            )
    """

    def __init__(self, client):
        super().__init__(client)
        self.module = "incidents"
