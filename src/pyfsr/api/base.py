class BaseAPI:
    """Base API class for all module-specific APIs.

    Subclasses make requests via ``self.client`` (the :class:`FortiSOAR`
    client), which owns the canonical ``request``/``get``/``post``/``put``/
    ``delete`` methods.
    """

    def __init__(self, client):
        self.client = client
