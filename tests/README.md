# Testing pyfsr

This document explains how to run tests for the pyfsr library against a real FortiSOAR instance.

## Prerequisites

1. Access to a FortiSOAR instance
2. API token with appropriate permissions
3. Python 3.10 or later
4. pip/virtualenv

## Setup

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

2. Install test requirements:

```bash
pip install -r test_requirements.txt
```

3. Create config.toml:

```toml
[fortisoar]
# Required
base_url = "https://your-fortisoar-instance"

# Authentication - use either username/password OR auth_token
username = "your-username"  # Recommended
password = "your-password"  # Recommended
# auth_token = "your-api-token"  # Alternative

# Optional
verify_ssl = trueconfig.toml
```

The config.toml file should be placed in the tests directory. You can also specify a different location when running
tests using the --config-path option.

## Running Tests

Run all tests:

```bash
pytest -v
```

Run specific test file:

```bash
pytest test_solution_pack.py -v
```

Run with different config file:

```bash
pytest --config-path=/path/to/config.toml -v
```

Run with coverage:

```bash
pytest --cov=pyfsr
```

## Test Organization

- `test_solution_pack.py` - Tests for SolutionPackAPI including docstring examples
- `config.toml` - Test configuration file
- Add more test files as needed

## Adding New Tests

1. Docstring Examples
    - Add realistic examples to class/method docstrings
    - Use real data structures and values
    - Show complete workflows where appropriate

2. Integration Tests
    - Add test methods to appropriate test file
    - Use pytest fixtures for common setup
    - Clean up any test data created

## Best Practices

1. Always use temporary directories for test outputs
2. Clean up any test data created in FortiSOAR
3. Keep test data minimal - export small modules/playbooks
4. Use meaningful test names and docstrings
5. Add comments explaining complex test setup
6. Don't commit config.toml with real credentials

## Troubleshooting

1. **Configuration Not Found**
    - Verify config.toml exists in tests directory
    - Check file permissions
    - Try specifying path with --config-path

2. **Permission Denied**
    - Check API token has required permissions
    - Review FortiSOAR logs for specific error

3. **Test Failures**
    - Check FortiSOAR instance is accessible
    - Verify test prerequisites are met
    - Review test output for specific errors
    - Verify config.toml values are correct