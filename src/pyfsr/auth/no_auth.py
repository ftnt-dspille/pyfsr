"""No-auth strategy for FortiSOAR public endpoints.

A handful of FortiSOAR endpoints are intentionally unauthenticated — build
version (``GET /api/version``) and the public license API
(``POST /api/public/license``, used for first-time activation and FortiFlex
token redemption). On a *fresh* or *license-locked* appliance there are no
working credentials to authenticate with, so those endpoints are the only way
in. :class:`NoAuth` lets a client be constructed for exactly that case: it sends
no ``Authorization`` header and does no construction-time validation.

Build one by passing ``public=True`` to :class:`pyfsr.client.FortiSOAR`. Authenticated operations
are marked unsupported so a misdirected call fails with a clear message rather
than a confusing 401.
"""

from .base import BaseAuth


class NoAuth(BaseAuth):
    """Unauthenticated strategy — for public endpoints only (no credentials)."""

    def __init__(self, base_url: str = "", verify_ssl: bool = True):
        super().__init__()
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        # Everything that needs a real identity is unsupported on a public client.
        self._unsupported_operations = {
            self.OPERATION_AUTH,
            self.OPERATION_CONFIG_EXPORT,
            self.OPERATION_CONFIG_IMPORT,
            self.OPERATION_PLAYBOOK,
            self.OPERATION_SOLUTION_PACK,
        }

    def get_auth_headers(self) -> dict:
        """No credentials — send no auth header."""
        return {}
