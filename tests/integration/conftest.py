"""Shared fixtures for live integration tests (opt-in: pytest -m integration)."""

from pathlib import Path

import pytest

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # backport


def load_config():
    """Load integration config from examples/config.toml, else skip."""
    config_path = Path(__file__).parent.parent.parent / "examples" / "config.toml"
    if not config_path.exists():
        pytest.skip("Integration test config not found (examples/config.toml)")
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def get_auth_from_config(config):
    """Return API key (str) or (username, password) tuple from config."""
    auth = config["fortisoar"]["auth"]
    if "api_key" in auth:
        return auth["api_key"]
    if "username" in auth and "password" in auth:
        return (auth["username"], auth["password"])
    raise ValueError("config.toml needs api_key or username/password")


@pytest.fixture(scope="session")
def client():
    """A live FortiSOAR client.

    Resolution order, so the suite runs anywhere with no extra files:
      1. ``FSR_*`` environment variables (``FSR_BASE_URL`` + ``FSR_API_KEY`` or
         ``FSR_USERNAME``/``FSR_PASSWORD``), via the SDK's own ``EnvConfig`` —
         the same path documented for end users.
      2. ``examples/config.toml`` (legacy).
    If neither is present, the integration suite is skipped.
    """
    import os

    from pyfsr import FortiSOAR

    if os.environ.get("FSR_BASE_URL") or os.environ.get("FSR_HOST"):
        from pyfsr.config import EnvConfig

        return EnvConfig.from_env().client()

    config = load_config()
    return FortiSOAR(
        base_url=config["fortisoar"]["base_url"],
        auth=get_auth_from_config(config),
        verify_ssl=config["fortisoar"].get("verify_ssl", True),
        suppress_insecure_warnings=True,
    )
