"""File upload/download helpers — ``client.files``.

Wraps FortiSOAR's ``/api/3/files`` endpoint to upload a local file (mimicking a
browser upload) and download an attachment back to disk, returning typed
:class:`~pyfsr.models.FileRecord` results. Attachment records that link a file to
a parent module are handled by ``client.attachments``.
"""

import logging
import mimetypes
from pathlib import Path

from ..models import FileRecord

logger = logging.getLogger("pyfsr")


class FileOperations:
    """Utility class for handling file operations in FortiSOAR"""

    def __init__(self, client):
        """
        Initialize FileOperations with a FortiSOAR client instance

        Args:
            client: FortiSOAR client instance
        """
        self.client = client

    def upload(self, filename: str) -> FileRecord:
        """
        Upload a file to FortiSOAR, mimicking browser file upload behavior

        Args:
            filename: Path to the file to upload

        Returns:
            FileRecord: The created ``/api/3/files`` record. Stays
            dict-compatible (``rec["@id"]``) while exposing typed fields
            (``rec.iri``, ``rec.filename``).
        """
        file_path = Path(filename)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Get proper mime type
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            mime_type = "application/octet-stream"

        # Ensure file is opened in binary mode
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, mime_type)}

            try:
                # The key issue - we need to send as multipart/form-data
                response = self.client.post(
                    "/api/3/files",
                    files=files,
                    headers={
                        # Remove Content-Type header - let requests set it with boundary
                        "Content-Type": None
                    },
                )
                record = FileRecord.model_validate(response) if isinstance(response, dict) else response
                logger.debug("File upload successful: %s", record.get("@id"))
                return record

            except Exception as e:
                logger.error("File upload failed for %s: %s", file_path, e)
                if hasattr(e, "response") and e.response is not None:
                    logger.error("Response status: %s", e.response.status_code)
                    logger.debug("Response body: %s", e.response.text)
                raise

    def upload_many(self, filenames: list[str]) -> list[FileRecord]:
        """Upload multiple files to FortiSOAR"""
        return [self.upload(f) for f in filenames]
