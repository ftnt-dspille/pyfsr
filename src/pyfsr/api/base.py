from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import FortiSOAR


class BaseAPI:
    """Base API class for all module-specific APIs.

    Subclasses make requests via ``self.client`` (the :class:`FortiSOAR`
    client), which owns the canonical ``request``/``get``/``post``/``put``/
    ``delete`` methods.
    """

    def __init__(self, client: FortiSOAR) -> None:
        self.client: FortiSOAR = client
