"""Base authentication class for FortiSOAR"""

from abc import ABC, abstractmethod

from ..exceptions import UnsupportedAuthOperationError


class BaseAuth(ABC):
    """Base class for FortiSOAR authentication methods"""

    # Operations that can be restricted
    OPERATION_AUTH = "auth"  # /auth endpoints
    OPERATION_CONFIG_EXPORT = "config_export"  # Export configuration
    OPERATION_CONFIG_IMPORT = "config_import"  # Import configuration
    OPERATION_PLAYBOOK = "playbook"  # Playbook operations
    OPERATION_SOLUTION_PACK = "solution_pack"  # Solution pack operations
    #: Content Hub install (solution pack or connector). The async connector-import
    #: step forwards the caller's credential to the integrations service as a Bearer
    #: token; an API-key (HMAC) session is not a JWT, so the import never runs and the
    #: job sits at Draft with no error. Reads/search on solution packs are fine.
    OPERATION_SOLUTIONPACK_INSTALL = "solutionpack_install"
    OPERATION_BULKUPSERT = "bulkupsert"  # /api/3/bulkupsert/* endpoints
    #: fsr-ai replays the *caller's* JWT (``Forwarded-Authorization: Bearer``) to
    #: the built-in ``FSR``-auth MCP servers, so an API-key caller has nothing to forward.
    OPERATION_FORWARDED_BEARER = "forwarded_bearer"
    #: Create/update of an Actor record (``/api/3/people``, ``/appliances``, ``/api_keys``).
    OPERATION_ACTOR_WRITE = "actor_write"
    #: fsr-ai called directly at ``/ai/...`` (nginx -> fsr-ai, which takes only a Bearer JWT or HMAC).
    OPERATION_AI_DIRECT = "ai_direct"

    def __init__(self):
        """Initialize base auth class"""
        self._unsupported_operations: set[str] = set()

    @property
    def auth_type(self) -> str:
        """Get the authentication type name"""
        return self.__class__.__name__

    @abstractmethod
    def get_auth_headers(self) -> dict:
        """Get authentication headers for requests"""
        pass

    def refresh(self) -> dict:
        """Re-establish credentials and return fresh auth headers.

        Default is a no-op (returns the current headers) -- correct for static
        credentials like an API key. Token-based auth (username/password)
        overrides this to re-authenticate, so a long-lived client can recover
        from an expired session token mid-run instead of failing the request.
        """
        return self.get_auth_headers()

    @property
    def can_refresh(self) -> bool:
        """Whether :meth:`refresh` mints a *new* credential (vs. a no-op).

        The client only retries an auth-failed request when this is True, so
        static-credential auth (API key) doesn't pointlessly re-send.
        """
        return False

    @property
    def unsupported_operations(self) -> set[str]:
        """Get set of unsupported operations"""
        return self._unsupported_operations

    def check_operation_supported(self, operation: str) -> None:
        """
        Check if an operation is supported with this authentication method.

        Args:
            operation: Operation to check

        Raises:
            UnsupportedAuthOperationError: If operation is not supported
        """
        if operation in self._unsupported_operations:
            raise UnsupportedAuthOperationError(operation, self.auth_type)
