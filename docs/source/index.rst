Welcome to pyfsr documentation
==============================

PyFSR is a Python client library for the FortiSOAR REST API, allowing you to interact with FortiSOAR programmatically.

Installation
------------
.. code-block:: bash

   pip install pyfsr

Quick Start
-----------
.. code-block:: python

   from pyfsr import FortiSOAR

   # Initialize the client
   client = FortiSOAR('your-server', 'your-token')

   # generic get call to get system info
    response = client.get('/api/v3/alerts')

   # Create an alert
   alert_data = {
       "name": "Test Alert",
       "description": "This is a test alert",
       "severity": "High"
   }
   response = client.alerts.create(**alert_data)

   # List all alerts
   alerts = client.alerts.list()

   # Get a specific alert
   alert = client.alerts.get("alert-id")

Features
--------
- Simple and intuitive API interface
- Support for all FortiSOAR API endpoints
- Automatic authentication handling
- Type hints for better IDE support
- Comprehensive error handling

API Reference
-------------
.. toctree::
   :maxdepth: 2
   :caption: API Documentation:

   api/client
   api/auth
   api/alerts

Authentication
--------------
PyFSR supports token-based authentication. You can obtain your API token from the FortiSOAR web interface:

1. Log in to your FortiSOAR instance
2. Navigate to Settings → API Tokens
3. Create a new API token
4. Use this token when initializing the client

Examples
--------

List All Alerts
~~~~~~~~~~~~~~~
.. code-block:: python

   # Get all alerts
   alerts = client.alerts.list()

   # Print alert names
   for alert in alerts['items']:
       print(alert['name'])

Create an Alert
~~~~~~~~~~~~~~~
.. code-block:: python

   # Create alert with specific properties
   new_alert = client.alerts.create(
       name="Suspicious Activity",
       description="Detected suspicious login attempts",
       severity="High",
       enabled=True
   )

Update an Alert
~~~~~~~~~~~~~~~
.. code-block:: python

   # Update alert properties
   client.alerts.update("alert-id", {
       "severity": "Critical",
       "description": "Updated description"
   })

Contributing
------------
Contributions are welcome! Please feel free to submit a Pull Request.

License
-------
This project is licensed under the MIT License - see the LICENSE file for details.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`