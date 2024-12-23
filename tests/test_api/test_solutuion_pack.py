"""Tests that run against a real FortiSOAR instance using TOML config"""

import doctest
import os
import tomllib
from pathlib import Path

import pytest

from pyfsr import FortiSOAR
from pyfsr.api.export_config import SolutionPackAPI


class FortiSOARTestConfig:
    """Load FortiSOAR test configuration from config.toml"""

    def __init__(self, config_path=None):
        if config_path is None:
            config_path = Path(__file__).parent / "config.toml"

        if not config_path.exists():
            raise FileNotFoundError(
                f"FortiSOAR test configuration not found at {config_path}.\n"
                "Please create config.toml with:\n"
                "[fortisoar]\n"
                'base_url = "https://your-fortisoar-instance"\n'
                'username = "your-username"\n'
                'password = "your-password"\n'
                "verify_ssl = true  # Optional"
            )

        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        fortisoar_config = config.get('fortisoar', {})
        self.base_url = fortisoar_config.get('base_url')

        # Support both auth methods
        self.username = fortisoar_config.get('username')
        self.password = fortisoar_config.get('password')
        self.auth_token = fortisoar_config.get('auth_token')

        self.verify_ssl = fortisoar_config.get('verify_ssl', True)

        if not self.base_url:
            raise ValueError("FortiSOAR base_url is required in config.toml")

        if not (self.username and self.password) and not self.auth_token:
            raise ValueError(
                "FortiSOAR authentication required in config.toml.\n"
                "Either provide username/password or auth_token."
            )

    def get_client(self) -> FortiSOAR:
        """Create FortiSOAR client using configured authentication"""
        if self.username and self.password:
            return FortiSOAR(
                base_url=self.base_url,
                auth=(self.username, self.password),
                verify_ssl=self.verify_ssl
            )
        else:
            return FortiSOAR(
                base_url=self.base_url,
                auth=self.auth_token,
                verify_ssl=self.verify_ssl
            )


def setup_doctest_globals(config_path=None):
    """Set up globals for doctests to use real FortiSOAR instance"""
    config = FortiSOARTestConfig(config_path)
    client = config.get_client()

    return {
        'FortiSOAR': FortiSOAR,
        'client': client,
    }


def test_docstring_examples(tmp_path):
    """Run doctests against real FortiSOAR instance"""

    # Add temp dir to globals so examples can write files there
    test_globals = setup_doctest_globals()
    test_globals['test_output_dir'] = tmp_path

    # Configure doctest
    finder = doctest.DocTestFinder()
    runner = doctest.DocTestRunner(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE
    )

    # Find and run all doctests
    tests = finder.find(SolutionPackAPI, globs=test_globals)

    for test in tests:
        runner.run(test)

    # Check results
    results = runner.summarize()
    assert results.failed == 0, f"{results.failed} doctests failed"


@pytest.fixture
def soar_client():
    """Fixture providing configured FortiSOAR client"""
    config = FortiSOARTestConfig()
    return config.get_client()


@pytest.fixture
def solution_pack_api(soar_client):
    """Fixture providing SolutionPackAPI instance"""
    return soar_client.solution_packs


def test_export_solution_pack_workflow(solution_pack_api, tmp_path):
    """Test complete solution pack export workflow against real instance"""

    # Test export options
    options = {
        "modules": ["alerts"],
        "playbooks": {
            "collections": [],
            "includeVersions": False,
            "globalVariables": []
        },
        "dashboards": [],
        "reports": [],
        "roles": []
    }

    # Export to temp directory
    output_path = tmp_path / "test_export.json"
    result_path = solution_pack_api.export_solution_pack(
        name="API Test Pack",
        options=options,
        output_path=str(output_path)
    )

    # Verify export succeeded
    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
