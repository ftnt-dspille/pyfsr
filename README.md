# pyfsr

[Documentation](https://ftnt-dspille.github.io/pyfsr/) | [Installation](#installation) | [Quick Start](#quick-start)

**PyFSR** is a Python client library for the FortiSOAR REST API, allowing you to interact with FortiSOAR
programmatically.

## Installation

```bash
pip install pyfsr
```

## Quick Start

```python
from pyfsr import FortiSOAR

# Initialize the client
client = FortiSOAR('your-server', 'your-token')

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
```

## Features

- Simple API interface
- Support for all FortiSOAR API endpoints using a generic `get`, `post`, `put`, `delete` methods
- Authentication handling
- Type hints for better IDE support

## Roadmap

- Add support for more API endpoints
- Add support for more complex API calls
    - Filtering
    - Pagination
    - Starting Playbooks
- HMAC Authentication
- Better Unit Testing

## Important Notes

This library is a work in progress and is not yet ready for production use.

