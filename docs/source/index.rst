pyfsr Documentation
===================

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   api/index

Quick Start
-----------

Installation:

.. code-block:: bash

   pip install pyfsr

Usage:

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

The complete API reference is available in the modules section.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`