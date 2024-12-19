Authentication
==============

Overview
--------
PyFSR supports two authentication methods:

1. API Key Authentication (Recommended)
2. Username/Password Authentication

API Key Authentication
----------------------
Use API key authentication for better security and easier token management.

.. code-block:: python

    from pyfsr import FortiSOAR
    from pyfsr.auth import APIKeyAuth

    # Initialize with API key
    auth = APIKeyAuth("your-api-key")
    client = FortiSOAR("your-server", auth)

Username/Password Authentication
--------------------------------
Use username/password authentication when API keys are not available.

.. code-block:: python

    from pyfsr import FortiSOAR
    from pyfsr.auth import UserPassAuth

    # Initialize with username and password
    auth = UserPassAuth("username", "password")
    client = FortiSOAR("your-server", auth)

API Reference
-------------

API Key Authentication
~~~~~~~~~~~~~~~~~~~~~~
.. currentmodule:: pyfsr.auth.api_key
.. autoclass:: APIKeyAuth
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Username/Password Authentication
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. currentmodule:: pyfsr.auth.user_pass
.. autoclass:: UserPasswordAuth
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__