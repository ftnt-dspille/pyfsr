# Authentication

pyfsr supports two authentication methods, selected by the type of the `auth`
argument you pass to {class}`~pyfsr.client.FortiSOAR`.

## API token

Pass the token as a string. This is the recommended method for automation:

```{code-block} python
from pyfsr import FortiSOAR

client = FortiSOAR("soar.example.com", "your-api-token")
```

## Username & password

Pass a `(username, password)` tuple. pyfsr exchanges these for a session token:

```{code-block} python
client = FortiSOAR("soar.example.com", ("admin", "password"))
```

## Transport options

```{code-block} python
client = FortiSOAR(
    "soar.example.com",
    "your-api-token",
    verify_ssl=False,                  # self-signed appliances
    suppress_insecure_warnings=True,   # silence urllib3 warnings
    port=8443,                         # non-standard port
    timeout=60,                        # per-request timeout (seconds)
    max_retries=3,                     # transient-failure retries
    verbose=True,                      # debug logging (secrets masked)
)
```

```{note}
Authorization headers, API keys, cookies, and CSRF tokens are masked in logs
even when `verbose=True`, so debug output stays safe to share.
```

## Environment-based config

For apps and the bundled MCP server, drive configuration from `FSR_*`
environment variables via {class}`~pyfsr.config.EnvConfig` instead of hard-wiring
host and credentials:

```{code-block} python
from pyfsr import EnvConfig

client = EnvConfig.from_env().client()
```

| Variable | Purpose |
| --- | --- |
| `FSR_BASE_URL` | Appliance host or URL (required; `FSR_HOST` also accepted) |
| `FSR_API_KEY` | API-key auth |
| `FSR_USERNAME` / `FSR_PASSWORD` | Username/password auth (alternative to `FSR_API_KEY`) |
| `FSR_PORT` | Optional port override |
| `FSR_VERIFY_SSL` | `false`/`0`/`no`/`off` disables TLS verification |
| `FSR_SUPPRESS_INSECURE_WARNINGS` | Silence urllib3 warnings when SSL is off |
| `FSR_TIMEOUT` | Per-request timeout in seconds (default 30) |
