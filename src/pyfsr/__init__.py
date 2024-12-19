"""
PyFSR: Python client for the FortiSOAR REST API.

For detailed documentation, visit: https://ftnt-dspille.github.io/pyfsr/
"""
from .client import FortiSOAR
from .exceptions import FortiSOARException, AuthenticationError

__all__ = ['FortiSOAR', 'FortiSOARException', 'AuthenticationError']
__version__ = "0.1.0"
