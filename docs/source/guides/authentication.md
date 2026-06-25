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

`from_env` reads `FSR_*` from the environment (or a dict you pass) and resolves
a plain config object — no network, so the resolved shape is safe to doctest:

```{doctest}
>>> from pyfsr import EnvConfig
>>> cfg = EnvConfig.from_env({
...     "FSR_BASE_URL": "https://soar.example.com",
...     "FSR_API_KEY": "key-123",
... })
>>> cfg.base_url, cfg.auth, cfg.verify_ssl, cfg.timeout
('https://soar.example.com', 'key-123', True, 30)
>>> type(cfg.auth).__name__          # a lone key resolves to a str
'str'
>>> user_pw = EnvConfig.from_env({
...     "FSR_BASE_URL": "https://soar.example.com",
...     "FSR_USERNAME": "admin", "FSR_PASSWORD": "secret",
... })
>>> type(user_pw.auth).__name__      # user+password resolves to a tuple
'tuple'
```

When required variables are missing it raises `ValueError` with an actionable
message — pass an explicit `env` dict to make the failure deterministic:

```{doctest}
>>> try:
...     EnvConfig.from_env({})                     # no host
... except ValueError as e:
...     str(e)
'FSR_BASE_URL (or FSR_HOST) is required'
>>> try:
...     EnvConfig.from_env({"FSR_BASE_URL": "https://soar.example.com"})  # no auth
... except ValueError as e:
...     str(e)
'set FSR_API_KEY, or both FSR_USERNAME and FSR_PASSWORD'
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

```{note}
`EnvConfig.from_env()` itself never touches the network — it only resolves
config. The live validation call happens later, inside
{class}`~pyfsr.client.FortiSOAR`, when you call `.client()` (or
construct `FortiSOAR(...)` directly): constructing the client validates the
credential against the appliance.
```
