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

        Default is a no-op (returns the current headers) — correct for static
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
