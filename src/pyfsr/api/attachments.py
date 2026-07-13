"""Attachment records (``/api/3/attachments``).

A FortiSOAR attachment is a metadata record that points at an uploaded *file*
record: you upload the bytes to ``/api/3/files`` first, then create an
``attachments`` row linking that file by IRI. This wrapper hides that two-step
dance — :meth:`~pyfsr.api.attachments.AttachmentsAPI.create_from_file` uploads
and links in one call. Accessed as ``client.attachments``.

Example:
    >>> client = demo_client()
    >>> att = client.attachments.create(
    ...     name="report.csv",
    ...     file="/api/3/files/880e8400-e29b-41d4-a716-446655440010",
    ...     description="Daily report"
    ... )
    >>> att["name"]
    'report.csv'
    >>> att["description"]
    'Daily report'
"""

from __future__ import annotations

from typing import Any

from ..models import Attachment
from ..projection import iri_to_uuid
from .base import BaseAPI

_BASE = "/api/3/attachments"


class AttachmentsAPI(BaseAPI):
    """Create and manage attachment records."""

    def create(self, *, name: str, file: Any, description: str | None = None, **fields: Any) -> Attachment:
        """Create an attachment record linking an already-uploaded file.

        ``file`` is the file record's IRI (``"/api/3/files/<uuid>"``) or the file
        record/dict itself (its ``@id`` is used). Extra ``fields`` are merged into
        the payload. Use :meth:`create_from_file` to upload and link in one step.
        Returns a typed :class:`~pyfsr.models.Attachment`.

        Example:
            >>> client = demo_client()
            >>> att = client.attachments.create(
            ...     name="test.csv",
            ...     file="/api/3/files/880e8400-e29b-41d4-a716-446655440010"
            ... )
            >>> att["name"]
            'report.csv'
        """
        if isinstance(file, str) and file.startswith("/api/"):
            file_iri = file
        else:
            uuid = iri_to_uuid(file)
            file_iri = file if isinstance(file, str) else f"/api/3/files/{uuid}"
        payload: dict[str, Any] = {"name": name, "file": file_iri}
        if description is not None:
            payload["description"] = description
        payload.update(fields)
        return Attachment.model_validate(self.client.post(_BASE, data=payload))

    def create_from_file(
        self, path: str, *, name: str | None = None, description: str | None = None, **fields: Any
    ) -> Attachment:
        """Upload a local file **and** create its attachment record in one call.

        Wraps ``client.files.upload(path)`` then :meth:`create`. ``name`` defaults
        to the uploaded file's name. Returns a typed :class:`~pyfsr.models.Attachment`.
        """
        file_record = self.client.files.upload(path)
        file_iri = file_record["@id"]
        attach_name = name or file_record.get("filename") or file_iri.rsplit("/", 1)[-1]
        return self.create(name=attach_name, file=file_iri, description=description, **fields)

    def get(self, ref: str) -> Attachment:
        """Fetch an attachment record by uuid or IRI (typed).

        Example:
            >>> client = demo_client()
            >>> att = client.attachments.get("770e8400-e29b-41d4-a716-446655440009")
            >>> att["name"]
            'report.csv'
        """
        uuid = iri_to_uuid(ref)
        return Attachment.model_validate(self.client.get(f"{_BASE}/{uuid}"))

    def delete(self, ref: str) -> None:
        """Delete an attachment record by uuid or IRI."""
        uuid = iri_to_uuid(ref)
        self.client.delete(f"{_BASE}/{uuid}")
