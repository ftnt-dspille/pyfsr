"""Three operations that return the *same* logical payload three ways.

Run all three via ``client.connectors.execute(...)`` and compare the returned
envelopes to see whether using the ``Result`` class changes the shape that
reaches the playbook (it should all normalize to ``{operation, status, message,
data}``).
"""

from connectors.core.connector import ConnectorError, Result, get_logger

logger = get_logger("result-probe")


def check_health(config):
    if not config.get("marker"):
        raise ConnectorError("Marker is required.")
    return True


def _payload(config, params, style):
    return {
        "echo": params.get("echo", "hi"),
        "marker": config.get("marker"),
        "style": style,
    }


def return_bare_dict(config, params):
    """Return a plain dict — the most common real-world pattern."""
    return _payload(config, params, "bare_dict")


def return_result_set_data(config, params):
    """Return a Result using set_status / set_message / set_data."""
    result = Result()
    result.set_status("Success")
    result.set_message("returned via Result.set_data")
    result.set_data(_payload(config, params, "result_set_data"))
    return result


def return_result_set_result(config, params):
    """Return a Result using only set_result(status, message) — no data."""
    result = Result()
    result.set_result(status="Success", message="returned via Result.set_result")
    return result
