"""The tasks module — ``client.tasks``.

Create and manage FortiSOAR tasks, typically attached to a parent record (an
alert or incident) via ``record=`` on create. Friendly picklist values (e.g.
``status="Open"``) are resolved to IRIs automatically. See :doc:`/guides/records`
for the underlying record semantics.
"""

from ._record_module import RecordModuleAPI


class TasksAPI(RecordModuleAPI):
    """Create and manage FortiSOAR tasks.

    Tasks are typically attached to a parent record (an alert or incident). Pass
    ``record=`` to link on create; friendly picklist values (e.g.
    ``status="Open"``) are resolved to IRIs automatically.

    Example:
        .. code-block:: python

            client.tasks.create(
                name="Verify the source IP reputation",
                status="Open",
                record="/api/3/alerts/1234-...",
            )
    """

    def __init__(self, client):
        super().__init__(client)
        self.module = "tasks"
