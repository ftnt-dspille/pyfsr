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

   # Initialize client
   client = FortiSOAR('your-fortisoar-instance', 'your-api-key')

   # Create an alert
   alert = client.alerts.create(
       name="Test Alert",
       description="Test Description",
       severity="High"
   )

API Reference
-------------

.. toctree::
   :maxdepth: 2

API Reference <autoapi/pyfsr/index>

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