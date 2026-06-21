from __future__ import annotations

from typing import Any

from .base import BaseAPI


def _module_from_iri(iri: str) -> str:
    """Return the module segment of a record IRI (``/api/3/alerts/<uuid>`` -> ``alerts``)."""
    parts = [p for p in iri.split("/") if p]
    # .../api/3/<module>/<uuid>  ->  module is the second-to-last segment
    if len(parts) >= 2:
        return parts[-2]
    raise ValueError(f"Cannot derive module from record IRI: {iri!r}")


class CommentsAPI(BaseAPI):
    """Create and manage FortiSOAR comments (the analyst notes attached to records).

    A comment is its own record (``/api/3/comments``) linked to one or more parent
    records (alerts, incidents, tasks, ...) through the parent module's relationship
    field. ``create`` derives that relationship field from the record IRI, so a
    comment can be attached to any module without per-module wiring.

    Example:
        .. code-block:: python

            client.comments.create(
                "Triaged — false positive, closing.",
                record="/api/3/alerts/1234-...",
            )
    """

    def __init__(self, client):
        super().__init__(client)
        self.module = "comments"

    def create(
        self,
        content: str,
        *,
        record: str | list[str] | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Create a comment, optionally linked to one or more parent records.

        Args:
            content: The comment body text.
            record: A record IRI (``/api/3/<module>/<uuid>``) or list of IRIs to
                attach the comment to. All IRIs must belong to the same module;
                the relationship field is derived from that module name.
            **data: Extra fields merged into the payload (e.g. ``file`` for an
                attachment IRI).

        Returns:
            The created comment object.

        Example:
            .. code-block:: python

                client.comments.create(
                    "Looks malicious — escalating.",
                    record="/api/3/incidents/abcd-...",
                )
        """
        payload: dict[str, Any] = {"content": content, **data}
        if record is not None:
            iris = [record] if isinstance(record, str) else list(record)
            modules = {_module_from_iri(iri) for iri in iris}
            if len(modules) != 1:
                raise ValueError(f"All linked records must share one module, got: {sorted(modules)}")
            payload[modules.pop()] = iris
        return self.client.post(f"/api/3/{self.module}", data=payload)

    def list(self, params: dict | None = None) -> dict[str, Any]:
        """List comments, optionally filtered via query parameters."""
        return self.client.get(f"/api/3/{self.module}", params=params)

    def get(self, comment_id: str) -> dict[str, Any]:
        """Get a single comment by ID."""
        return self.client.get(f"/api/3/{self.module}/{comment_id}")

    def update(self, comment_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update a comment (e.g. edit its ``content``)."""
        return self.client.put(f"/api/3/{self.module}/{comment_id}", data=data)

    def delete(self, comment_id: str) -> None:
        """Delete a comment."""
        self.client.delete(f"/api/3/{self.module}/{comment_id}")
