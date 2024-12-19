Client
======

Overview
--------
The FortiSOAR client is the main interface for interacting with the FortiSOAR REST API. It supports both API key and username/password authentication methods.

Basic Usage
-----------

API Key Authentication
~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: python

    from pyfsr import FortiSOAR

    # Initialize with API key
    client = FortiSOAR(
        base_url="https://your-soar-instance.com",
        auth="your-api-key",
        verify_ssl=True
    )

Username/Password Authentication
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: python

    # Initialize with username/password
    client = FortiSOAR(
        base_url="https://your-soar-instance.com",
        auth=("username", "password")
    )

Handling Self-Signed Certificates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: python

    # Disable SSL verification with warning suppression
    client = FortiSOAR(
        base_url="https://your-soar-instance.com",
        auth="your-api-key",
        verify_ssl=False,
        supress_insecure_warnings=True
    )

API Reference
-------------
.. currentmodule:: pyfsr.client
.. autoclass:: FortiSOAR
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Available APIs
--------------
- **alerts**: Access the Alerts API interface through ``pyfsr.client.alerts``