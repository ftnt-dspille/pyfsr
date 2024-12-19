from abc import ABC, abstractmethod


class BaseAuth(ABC):
    """Base authentication class"""

    @abstractmethod
    def get_auth_headers(self) -> dict:
        """Return authentication headers"""
        pass