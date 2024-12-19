Alerts API
==========

Overview
--------
The Alerts API provides methods for managing FortiSOAR alerts including creating, updating, and querying alerts.

Examples
--------

Creating Alerts
~~~~~~~~~~~~~~~
.. code-block:: python

    from pyfsr import FortiSOAR

    client = FortiSOAR("your-server", "your-token")

    # Create a new alert
    new_alert = {
        "name": "Suspicious Login",
        "description": "Multiple failed login attempts detected"
    }

    result = client.alerts.create(new_alert)

Querying Alerts
~~~~~~~~~~~~~~~
.. code-block:: python

    # Get all alerts
    all_alerts = client.alerts.list()

Updating Alerts
~~~~~~~~~~~~~~~
.. code-block:: python

    # Update alert status
    client.alerts.update(
        alert_id="123",
        data={
            "assignedTo": "analyst@example.com"
        }
    )

API Reference
-------------
.. currentmodule:: pyfsr.api.alerts

.. autoclass:: AlertsAPI
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__