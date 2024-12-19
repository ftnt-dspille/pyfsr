import mimetypes
from pathlib import Path
from typing import Dict, Any


class FileOperations:
    """Utility class for handling file operations in FortiSOAR"""

    def __init__(self, client):
        """
        Initialize FileOperations with a FortiSOAR client instance

        Args:
            client: FortiSOAR client instance
        """
        self.client = client

    def upload(self, filename: str) -> Dict[str, Any]:
        """
        Upload a file to FortiSOAR, mimicking browser file upload behavior

        Args:
            filename: Path to the file to upload

        Returns:
            Dict[str, Any]: Server response
        """
        file_path = Path(filename)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Get proper mime type
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type is None:
            mime_type = 'application/octet-stream'

        # Ensure file is opened in binary mode
        with open(file_path, 'rb') as f:
            files = {
                'file': (
                    file_path.name,
                    f,
                    mime_type
                )
            }

            try:
                # The key issue - we need to send as multipart/form-data
                response = self.client.post(
                    '/api/3/files',
                    files=files,
                    headers={
                        # Remove Content-Type header - let requests set it with boundary
                        'Content-Type': None
                    }
                )
                print(f"File upload successful. Response: {response}")
                return response

            except Exception as e:
                print(f"Upload failed: {str(e)}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response status: {e.response.status_code}")
                    print(f"Response body: {e.response.text}")
                raise

    def upload_many(self, filenames: list[str]) -> list[Dict[str, Any]]:
        """Upload multiple files to FortiSOAR"""
        return [self.upload(f) for f in filenames]
