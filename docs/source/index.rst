Welcome to pyfsr
================

A Python client for the FortiSOAR REST API.

Quick Start
-----------

Installation:

.. code-block:: bash

   pip install pyfsr

Basic Usage:

.. code-block:: python

    from pyfsr import FortiSOAR

    # Initialize the client
    client = FortiSOAR('your-server', 'your-token')

    # or with username and password
    client = FortiSOAR('your-server', ('your-username', 'your-password'))

    # Generic get call to Alerts endpoint
    response = client.get('/api/v3/alerts')

    # Create an alert
    alert_data = {
        "name": "Test Alert",
        "description": "This is a test alert",
        "severity": "High"
    }
    alert_record = client.alerts.create(**alert_data)

    # List all alerts
    alerts = client.alerts.list()

    # Get a specific alert
    alert = client.alerts.get("alert-id")

API Reference
-------------

.. toctree::
   :maxdepth: 4

   autoapi/pyfsr/index


Module Overview
~~~~~~~~~~~~~~~

- **Client** - Main FortiSOAR client (:class:`pyfsr.FortiSOAR`)
- **Alerts** - Manage FortiSOAR alerts (:class:`pyfsr.api.alerts.AlertsAPI`)
- **Export Config** - Handle configuration exports (:class:`pyfsr.api.export_config.ExportConfigAPI`)
- **Solution Packs** - Work with solution packs (:class:`pyfsr.api.solution_packs.SolutionPackAPI`)
- **Authentication** - API key and user authentication handlers (:mod:`pyfsr.auth`)
- **File Operations** - File handling utilities (:class:`pyfsr.utils.file_operations.FileOperations`)

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`