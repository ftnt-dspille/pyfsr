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

Core Components
~~~~~~~~~~~~~~~

.. toctree::
   :maxdepth: 2

   Client <_autosummary/pyfsr.client>
   Alerts API <_autosummary/pyfsr.api.alerts>
   Export Configuration <_autosummary/pyfsr.api.export_config>
   Solution Packs <_autosummary/pyfsr.api.solution_packs>

Authentication
~~~~~~~~~~~~~~

.. toctree::
   :maxdepth: 2

   API Key Authentication <_autosummary/pyfsr.auth.api_key>
   Username/Password Authentication <_autosummary/pyfsr.auth.user_pass>

Utilities
~~~~~~~~~

.. toctree::
   :maxdepth: 2

   File Operations <_autosummary/pyfsr.utils.file_operations>

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`