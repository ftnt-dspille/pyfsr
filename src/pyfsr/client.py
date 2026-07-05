"""Main client class for FortiSOAR API"""

import logging
import os
import sys
import time
import warnings
from typing import TYPE_CHECKING, Any, Literal, overload
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .api.agents import AgentsAPI
from .api.ai import AIApi
from .api.alerts import AlertsAPI
from .api.api_keys import ApiKeysAPI
from .api.api_users import ApiKeyUsersAPI
from .api.app_config import AppConfigAPI
from .api.attachments import AttachmentsAPI
from .api.audit import AuditAPI
from .api.auth_config import AuthConfigAPI
from .api.comments import CommentsAPI
from .api.connectors import ConnectorsAPI
from .api.content_hub import ContentHubSearch
from .api.export_config import ExportConfigAPI
from .api.export_templates import ExportTemplatesAPI
from .api.feeds import IngestFeedsAPI
from .api.import_config import ImportConfigAPI
from .api.incidents import IncidentsAPI
from .api.manual_input import ManualInputAPI
from .api.modules import ModulesAPI
from .api.modules_admin import ModulesAdminAPI
from .api.native_mcp import NativeMCPApi
from .api.notifications import NotificationsAPI
from .api.picklists import PicklistsAPI
from .api.playbooks import PlaybooksAPI
from .api.roles import RolesAPI
from .api.routers import RoutersAPI
from .api.schedules import SchedulesAPI
from .api.search import SearchAPI
from .api.solution_packs import SolutionPackAPI
from .api.system import SystemAPI
from .api.system_settings import SystemSettingsAPI
from .api.tags import TagsAPI
from .api.tasks import TasksAPI
from .api.taxii import TaxiiAPI
from .api.teams import TeamsAPI
from .api.user_settings import UserSettingsAPI
from .api.users import UsersAPI
from .api.view_templates import ViewTemplatesAPI
from .api.views import ViewsAPI
from .api.wf_tools import WfToolsAPI
from .api.workflow_collections import WorkflowCollectionsAPI
from .auth.api_key import APIKeyAuth
from .auth.base import BaseAuth
from .auth.user_pass import UserPasswordAuth
from .exceptions import FortiSOARException, handle_api_error

if TYPE_CHECKING:
    from .appliance import Appliance
from .models import (
    AggregateRow,
    Alert,
    ApiKey,
    Comment,
    FileRecord,
    Incident,
    Role,
    Task,
    Team,
    User,
    Workflow,
    WorkflowCollection,
)
from .records import RecordSet
from .utils.file_operations import FileOperations

logger = logging.getLogger("pyfsr")

# Header names whose values are secrets and must never be logged in full.
_SENSITIVE_HEADERS = {"authorization", "x-api-key", "cookie", "csrf-token"}


def _mask_headers(headers: dict) -> dict:
    """Return a copy of ``headers`` with sensitive values masked for logging.

    Keeps a short, non-secret prefix (e.g. the ``API-KEY`` / ``Bearer`` scheme)
    so logs stay diagnosable without exposing the credential.
    """
    masked: dict = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADERS and isinstance(value, str) and value:
            scheme = value.split(" ", 1)[0] if " " in value else ""
            masked[key] = f"{scheme} ***".strip() if scheme else "***"
        else:
            masked[key] = value
    return masked


class FortiSOAR:
    """
    Main FortiSOAR client class for interacting with the FortiSOAR API.
    """

    def __init__(
        self,
        base_url: str,
        auth: str | tuple | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        verify_ssl: bool = True,
        suppress_insecure_warnings: bool = False,
        verbose: bool = False,
        port: int | None = None,
        timeout: int | float | None = 30,
        max_retries: int = 2,
        dry_run: bool = False,
        http_trace: bool = False,
    ):
        """
        Initialize the FortiSOAR client.

        Args:
           base_url (str): The base URL for the FortiSOAR API.
           auth (str | tuple, optional): **Deprecated.** Legacy positional auth —
               an API-key ``str`` or a ``(username, password)`` tuple. Prefer the
               explicit keywords below.
           username (str, optional): Login user, paired with ``password`` for
               credential auth.
           password (str, optional): Login password. Given *without* ``username``
               it is treated as an API key (a lone secret is almost always a key).
           token (str, optional): API key for token auth. ``api_key`` is an alias.
           api_key (str, optional): Alias for ``token``.
           verify_ssl (bool, optional): Whether to verify SSL certificates. Defaults to True.
           suppress_insecure_warnings (bool, optional): Whether to suppress insecure request
               warnings. Defaults to False.
           port (int, optional): Port to connect to. Overrides any port in base_url.
               Defaults to None (uses 443 for HTTPS).
           timeout (int | float, optional): Per-request timeout in seconds applied
               to every call (individual requests may override via ``timeout=``).
               Defaults to 30; pass None to disable.
           max_retries (int, optional): Automatic retries for transient failures
               (connection errors and 429/5xx) on idempotent methods, with
               exponential backoff. Defaults to 2; pass 0 to disable.
           dry_run (bool, optional): When True, mutating requests (POST/PUT/PATCH/
               DELETE) are **not** sent — they are logged and a synthetic success
               response is returned instead. Reads (GET/HEAD/OPTIONS) pass through
               normally. Lets callers exercise their write path without touching the
               appliance. Defaults to False.
           http_trace (bool, optional): When True, log full outgoing and incoming
               HTTP bodies to stderr for debugging. Defaults to False; no overhead
               when disabled.

        Raises:
            ValueError: If the provided authentication method is invalid.
        """
        # Private logging configuration
        self._log_level = logging.INFO
        self._log_file = "logs/fortisoar.log"
        self._max_log_size = 10 * 1024 * 1024  # 10MB
        self._backup_count = 5
        self.http_trace = http_trace

        # Setup logging if enabled
        self.verbose = verbose
        if verbose:
            # Create formatter
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

            # Ensure log directory exists
            log_dir = os.path.dirname(os.path.abspath(self._log_file))
            os.makedirs(log_dir, exist_ok=True)

            # Create rotating file handler
            file_handler = logging.FileHandler(self._log_file)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            # Also add console handler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

            logger.setLevel(self._log_level)

        # Ensure base_url starts with https://
        if not base_url.startswith("https://"):
            base_url = f"https://{base_url}"
        base_url = base_url.rstrip("/")

        # Apply explicit port, overriding any port already in the URL
        if port is not None:
            parsed = urlparse(base_url)
            netloc = f"{parsed.hostname}:{port}"
            base_url = urlunparse(parsed._replace(netloc=netloc))

        self.base_url: str = base_url

        if self.verbose:
            logger.info(f"Initializing FortiSOAR client for {self.base_url}")
            logger.info(f"Logging to file: {self._log_file}")

        self.timeout = timeout
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.verify_ssl = verify_ssl

        # Retry transient failures (connect errors + 429/5xx) on idempotent
        # methods with exponential backoff; writes are never auto-retried.
        if max_retries:
            retry = Retry(
                total=max_retries,
                connect=max_retries,
                read=max_retries,
                status=max_retries,
                backoff_factor=0.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
        if suppress_insecure_warnings:
            requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        # Setup authentication
        self.auth = self._resolve_auth(
            auth=auth,
            username=username,
            password=password,
            token=token or api_key,
        )

        # Apply authentication headers
        self.session.headers.update(self.auth.get_auth_headers())

        # Initialize API interfaces
        self.alerts: AlertsAPI = AlertsAPI(self)

        # Typed CRUD shortcuts for the common SOC record modules
        self.incidents: IncidentsAPI = IncidentsAPI(self)
        self.tasks: TasksAPI = TasksAPI(self)
        self.comments: CommentsAPI = CommentsAPI(self)

        # Initialize file operations utility
        self.files: FileOperations = FileOperations(self)

        # Attachment records (upload + link), and export templates
        self.attachments: AttachmentsAPI = AttachmentsAPI(self)
        self.export_templates: ExportTemplatesAPI = ExportTemplatesAPI(self)

        # Public content-repository downloads (standalone, unauthenticated):
        # client.repo.download_connector(...) etc. The module needs no client,
        # but exposing it here keeps discovery consistent with the rest of the API.
        from . import repo as _repo

        self.repo = _repo

        # Add solution packs API
        self.export_config: ExportConfigAPI = ExportConfigAPI(self)

        # Configuration import (re-apply an export .zip)
        self.import_config: ImportConfigAPI = ImportConfigAPI(self)

        # Content Hub search (solution packs, connectors, widgets)
        self.content_hub: ContentHubSearch = ContentHubSearch(self)

        # Module / field schema discovery
        self.modules: ModulesAPI = ModulesAPI(self)

        # Module / field schema administration (create, alter fields, publish)
        self.modules_admin: ModulesAdminAPI = ModulesAdminAPI(self)

        # Active system view template (SVT) resolution per module/layout
        self.views: ViewsAPI = ViewsAPI(self)

        # Read/write system view templates + role/condition-based default assignment
        self.view_templates: ViewTemplatesAPI = ViewTemplatesAPI(self)

        # Application navigation and module-visibility configuration
        self.app_config: AppConfigAPI = AppConfigAPI(self)

        # Picklist discovery + friendly-value -> IRI resolution
        self.picklists: PicklistsAPI = PicklistsAPI(self)

        # Connector discovery / health / operation execution
        self.connectors: ConnectorsAPI = ConnectorsAPI(self)

        # Playbook run history + manual-input resume
        self.playbooks: PlaybooksAPI = PlaybooksAPI(self)

        # Playbook (workflow) collection CRUD
        self.workflow_collections: WorkflowCollectionsAPI = WorkflowCollectionsAPI(self)

        # Workflow-engine authoring helpers (Jinja render, global variables)
        self.wf_tools: WfToolsAPI = WfToolsAPI(self)

        self.solution_packs: SolutionPackAPI = SolutionPackAPI(self)

        # Appliance tuning: system settings, DAS auth config, periodic schedules
        self.system_settings: SystemSettingsAPI = SystemSettingsAPI(self)
        self.user_settings: UserSettingsAPI = UserSettingsAPI(self)
        self.auth_config: AuthConfigAPI = AuthConfigAPI(self)
        self.schedules: SchedulesAPI = SchedulesAPI(self)
        self.notifications: NotificationsAPI = NotificationsAPI(self)
        self.manual_input: ManualInputAPI = ManualInputAPI(self)
        self.users: UsersAPI = UsersAPI(self)
        self.ai: AIApi = AIApi(self)
        self.mcp: NativeMCPApi = NativeMCPApi(self)

        # Tag names + execution-agent lifecycle (agents need a router at create time)
        self.tags: TagsAPI = TagsAPI(self)
        self.agents: AgentsAPI = AgentsAPI(self)
        self.roles: RolesAPI = RolesAPI(self)
        self.teams: TeamsAPI = TeamsAPI(self)
        self.routers: RoutersAPI = RoutersAPI(self)

        # Threat-intel / bulk ingest, TAXII sharing, audit log
        self.feeds: IngestFeedsAPI = IngestFeedsAPI(self)
        self.taxii: TaxiiAPI = TaxiiAPI(self)
        self.audit: AuditAPI = AuditAPI(self)

        # API-key user lifecycle, appliance introspection/licensing, global search
        self.api_users: ApiKeyUsersAPI = ApiKeyUsersAPI(self)
        self.api_keys: ApiKeysAPI = ApiKeysAPI(self)
        self.system: SystemAPI = SystemAPI(self)
        self.search: SearchAPI = SearchAPI(self)

    def _log_request(self, method: str, url: str, params: dict, data: dict, headers: dict) -> None:
        """Log request details when verbose mode is enabled."""
        if not self.verbose:
            return

        logger.info(f"\n{'=' * 50}\nRequest:")
        logger.info(f"Method: {method}")
        logger.info(f"URL: {url}")

        if headers:
            logger.info("Headers:")
            for key, value in _mask_headers(headers).items():
                logger.info(f"  {key}: {value}")

        if params:
            logger.info("Query Parameters:")
            for key, value in params.items():
                logger.info(f"  {key}: {value}")

        if data:
            logger.info("Request Data:")
            logger.info(f"  {data}")

    def _log_response(self, response: requests.Response, elapsed: float) -> None:
        """Log response details when verbose mode is enabled."""
        if not self.verbose:
            return

        logger.info("\nResponse:")
        logger.info(f"Status Code: {response.status_code}")
        logger.info(f"Elapsed Time: {elapsed:.2f} seconds")

        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                logger.info("Response JSON:")
                logger.info(f"  {response.json()}")
            except ValueError:
                logger.info("Response Text:")
                logger.info(f"  {response.text[:1000]}...")
        elif len(response.content) < 1000:
            logger.info("Response Text:")
            logger.info(f"  {response.text}")
        else:
            logger.info(f"Response Content Length: {len(response.content)} bytes")

        logger.info("=" * 50)

    def _resolve_auth(
        self,
        *,
        auth: str | tuple | None,
        username: str | None,
        password: str | None,
        token: str | None,
    ) -> BaseAuth:
        """Pick the auth strategy from the (several) ways it can be supplied.

        Preferred form is explicit keywords — ``username``/``password`` for
        credential auth, ``token`` (or ``api_key``) for API-key auth. As a
        convenience, **a lone ``password`` with no ``username`` is read as an API
        key** — passing a single secret almost always means a key, not half of a
        login. The legacy positional ``auth`` (``str`` key or ``(user, pass)``
        tuple) is still accepted but deprecated.
        """
        # Legacy positional form — keep working, nudge toward keywords.
        if auth is not None:
            if username or password or token:
                raise ValueError("Pass auth either positionally or via username/password/token keywords — not both.")
            warnings.warn(
                "Passing auth positionally is deprecated; use "
                "FortiSOAR(url, username=..., password=...) or "
                "FortiSOAR(url, token=...).",
                DeprecationWarning,
                stacklevel=3,
            )
            if isinstance(auth, str):
                token = auth
            elif isinstance(auth, tuple) and len(auth) == 2:
                username, password = auth
            else:
                raise ValueError("Positional auth must be an API-key str or a (username, password) tuple.")

        # Explicit API key wins.
        if token:
            if username:
                raise ValueError("Provide either token/api_key or username/password, not both.")
            if self.verbose:
                logger.info("Using API key authentication")
            return APIKeyAuth(self.base_url, token, self.verify_ssl)

        # Username + password → credential login.
        if username and password:
            if self.verbose:
                logger.info("Using username/password authentication")
            return UserPasswordAuth(self.base_url, username, password, self.verify_ssl)

        # A lone secret with no username → treat it as an API key.
        if password and not username:
            if self.verbose:
                logger.info("No username given; treating the lone secret as an API key")
            return APIKeyAuth(self.base_url, password, self.verify_ssl)

        if username and not password:
            raise ValueError("username was given without a password.")

        raise ValueError("No authentication provided — pass token=<api-key> or username=<user>, password=<pass>.")

    @classmethod
    def from_config_file(cls, path: str, **overrides: Any) -> "FortiSOAR":
        """Build a client from a TOML config file (the ``[fortisoar]`` layout).

        Convenience wrapper over ``EnvConfig.from_config_file(path).client()``;
        see :meth:`pyfsr.config.EnvConfig.from_config_file`. ``overrides`` pass
        straight through to the constructor.
        """
        from .config import EnvConfig

        return EnvConfig.from_config_file(path).client(**overrides)

    @classmethod
    def from_env_file(cls, path: str, *, override: bool = False, **overrides: Any) -> "FortiSOAR":
        """Build a client from a ``KEY=VALUE`` env file plus ``os.environ``.

        Convenience wrapper over ``EnvConfig.from_env_file(path).client()``.
        """
        from .config import EnvConfig

        return EnvConfig.from_env_file(path, override=override).client(**overrides)

    @classmethod
    def from_env(cls, **overrides: Any) -> "FortiSOAR":
        """Build a client from ``FSR_*`` environment variables.

        Convenience wrapper over ``EnvConfig.from_env().client()``.
        """
        from .config import EnvConfig

        return EnvConfig.from_env().client(**overrides)

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        headers: dict | None = None,
        *,
        raise_on_status: bool = True,
        **kwargs,
    ) -> requests.Response:
        """
        Make a request to the FortiSOAR API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            params: Query parameters
            data: Request body data
            files: Files to upload
            headers: Additional headers
            raise_on_status: When True (default) a non-2xx response is converted
                to a typed exception (``AuthenticationError`` / ``PermissionError`` /
                ``ResourceNotFoundError`` / ``APIError``). Pass ``False`` to get the
                raw :class:`requests.Response` back instead — for access-control
                probes and "is this identity allowed to do X?" checks that need the
                status code (200 vs 401/403) rather than a raised exception. The
                reauth-retry for refreshable auth still runs first, so a refreshed
                token can still turn a 401 into a 200; non-refreshable (API-key) auth
                returns the raw denial status. Transport errors (connection,
                timeout) always raise regardless of this flag.
            **kwargs: Additional arguments passed to requests

        Returns:
            requests.Response: Response from the API

        Raises:
            ValidationError: When request data validation fails
            AuthenticationError: When authentication fails
            PermissionError: When user lacks required permissions
            ResourceNotFoundError: When requested resource is not found
            UnsupportedAuthOperationError: When operation is not supported with current
                authentication method
            APIError: For other API errors
        """
        # Check operation support based on endpoint
        if endpoint.startswith("/api/auth/"):
            self.auth.check_operation_supported(BaseAuth.OPERATION_AUTH)

        # Ensure endpoint starts with /
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        # Add API version prefix if not present
        if not endpoint.startswith(("/api/3/", "/auth/", "/api/public/", "/api/")):
            endpoint = f"/api/3{endpoint}"

        url = urljoin(self.base_url, endpoint)

        # Merge any additional headers
        request_headers = self.session.headers.copy()
        if headers:
            request_headers.update(headers)

        self._log_request(method, url, params, data, request_headers)

        # Dry-run: never send mutating requests. Log the intent and hand back a
        # synthetic 200 so the caller's write path runs without touching the box.
        if self.dry_run and method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            return self._dry_run_response(method, url, params, data)

        # Apply the default timeout unless the caller passed one explicitly.
        kwargs.setdefault("timeout", self.timeout)

        # Internal marker: set on the single auth-refresh replay so it can't leak
        # into session.request (which would TypeError) and bounds the retry to one.
        reauthed = kwargs.pop("_reauthed", False)

        start_time = time.time()
        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=data if files is None else None,
                data=data if files is not None else None,
                files=files,
                headers=request_headers,
                **kwargs,
            )
            elapsed = time.time() - start_time

            # HTTP trace: log request/response bodies if enabled
            if self.http_trace:
                import json as _json

                print(f"[HTTP] {method.upper()} {url}", file=sys.stderr)
                if params:
                    print(f"  params: {params}", file=sys.stderr)
                if data:
                    try:
                        print(f"  request body: {_json.dumps(data)}", file=sys.stderr)
                    except (TypeError, ValueError):
                        print(f"  request body: {data}", file=sys.stderr)
                print(f"  response: {response.status_code}", file=sys.stderr)
                if response.content:
                    try:
                        print(f"  response body: {response.json()}", file=sys.stderr)
                    except (TypeError, ValueError):
                        print(f"  response body: {response.text[:500]}", file=sys.stderr)

            self._log_response(response, elapsed)

            # Recover from an expired session token: a long-lived client that
            # authenticated once at construction can outlive its token and start
            # getting 401/403 ("HMAC signature has expired"). Re-authenticate
            # once and replay the request. Guarded so it fires at most once and
            # only for refreshable (token) auth; file uploads aren't replayed
            # because the stream is already consumed.
            if (
                response.status_code in (401, 403)
                and not reauthed
                and files is None
                and getattr(self.auth, "can_refresh", False)
            ):
                fresh = None
                try:
                    fresh = self.auth.refresh()
                except Exception:  # noqa: BLE001 — fall through to normal error handling
                    fresh = None
                if fresh:
                    self.session.headers.update(fresh)
                    if logger.isEnabledFor(logging.INFO):
                        logger.info("auth token refreshed after %d; retrying request", response.status_code)
                    return self.request(
                        method,
                        endpoint,
                        params=params,
                        data=data,
                        files=files,
                        headers=headers,
                        raise_on_status=raise_on_status,
                        _reauthed=True,
                        **kwargs,
                    )

            if raise_on_status:
                response.raise_for_status()
            return response

        except requests.exceptions.RequestException as e:
            elapsed = time.time() - start_time
            if hasattr(e, "response") and e.response is not None:
                self._log_response(e.response, elapsed)
                handle_api_error(e.response)
            if self.verbose:
                logger.error(f"Request failed: {str(e)}")  # pragma: no cover
            raise

    def _dry_run_response(self, method: str, url: str, params: dict | None, data: dict | None) -> requests.Response:
        """Build a synthetic 200 response for a suppressed dry-run write.

        The body echoes the would-be request so callers that read ``.json()``
        (e.g. to pull a new record's ``@id``/``uuid``) still get a usable shape.
        """
        import io
        import json as _json

        if self.verbose:
            logger.info(f"[dry-run] suppressed {method.upper()} {url} (params={params}, data={data})")

        body = {
            "dryRun": True,
            "method": method.upper(),
            "url": url,
            "params": params,
            "data": data,
        }
        # DELETE returns no body in normal flow; echo the envelope anyway so the
        # method is uniform and callers can detect the dry-run.
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response.raw = io.BytesIO(_json.dumps(body).encode())
        response.encoding = "utf-8"
        return response

    def get(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        raise_on_status: bool = True,
        **kwargs,
    ) -> dict[str, Any] | bytes | requests.Response:
        """
        Perform GET request and return response based on content type.

        Returns JSON for application/json responses and bytes for binary responses.
        With ``raise_on_status=False`` returns the raw :class:`requests.Response`
        (so the caller can read ``.status_code`` on a denial).
        """
        response = self.request("GET", endpoint, params=params, raise_on_status=raise_on_status, **kwargs)
        if not raise_on_status:
            return response
        content_type = response.headers.get("Content-Type", "")

        if "application/json" in content_type:
            return response.json()
        elif any(binary_type in content_type for binary_type in ["application/zip", "application/octet-stream"]):
            return response.content
        else:
            # Default to JSON if content type is not explicitly specified
            return response.json()

    def post(
        self,
        endpoint: str,
        data: dict | None = None,
        files: dict | None = None,
        params: dict | None = None,
        *,
        raise_on_status: bool = True,
        **kwargs,
    ) -> dict[str, Any] | requests.Response:
        """Perform POST request and return JSON response.

        With ``raise_on_status=False`` returns the raw :class:`requests.Response`
        instead of parsing JSON — use it for fire-and-observe-status probes.
        """
        response = self.request(
            "POST",
            endpoint,
            params=params,
            data=data,
            files=files,
            raise_on_status=raise_on_status,
            **kwargs,
        )
        return response if not raise_on_status else response.json()

    def put(
        self,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
        *,
        raise_on_status: bool = True,
        **kwargs,
    ) -> dict[str, Any] | requests.Response:
        """Perform PUT request and return JSON response.

        With ``raise_on_status=False`` returns the raw :class:`requests.Response`.
        """
        response = self.request(
            "PUT",
            endpoint,
            params=params,
            data=data,
            raise_on_status=raise_on_status,
            **kwargs,
        )
        return response if not raise_on_status else response.json()

    def delete(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        raise_on_status: bool = True,
        **kwargs,
    ) -> None | requests.Response:
        """Perform DELETE request.

        With ``raise_on_status=False`` returns the raw :class:`requests.Response`
        instead of ``None``.
        """
        response = self.request("DELETE", endpoint, params=params, raise_on_status=raise_on_status, **kwargs)
        return response if not raise_on_status else None

    def query(self, module: str, query_data: dict) -> dict[str, Any]:
        """
        Execute a query against a module

        Args:
            module: Name of the module to query
            query_data: Query parameters and filters

        Returns:
            Query results
        """
        return self.post(f"/api/query/{module}", data=query_data)

    def appliance(
        self,
        *,
        user: str = "csadmin",
        password: str | None = None,
        port: int = 22,
        key_path: str | None = None,
        sudo_password: str | None = None,
        insecure_skip_host_key_check: bool = False,
    ) -> "Appliance":
        """Open an :class:`~pyfsr.appliance.Appliance` against this client's host.

        The REST client and the appliance use **different** transports: this client
        talks to ``/api/3`` with an API key/credentials, while the appliance reaches
        the same box over **SSH** (or locally when on-box). The host is reused from
        this client's ``base_url``; SSH credentials must still be supplied here.

        >>> box = client.appliance(key_path="~/.ssh/id_rsa")   # doctest: +SKIP
        >>> box.service.status()                               # doctest: +SKIP
        """
        from .appliance import Appliance

        host = urlparse(self.base_url).hostname
        return Appliance(
            host=host,
            user=user,
            password=password,
            port=port,
            key_path=key_path,
            sudo_password=sudo_password,
            insecure_skip_host_key_check=insecure_skip_host_key_check,
        )

    @overload
    def records(self, module: Literal["alerts"]) -> RecordSet[Alert]: ...
    @overload
    def records(self, module: Literal["incidents"]) -> RecordSet[Incident]: ...
    @overload
    def records(self, module: Literal["tasks"]) -> RecordSet[Task]: ...
    @overload
    def records(self, module: Literal["comments"]) -> RecordSet[Comment]: ...
    @overload
    def records(self, module: Literal["workflows"]) -> RecordSet[Workflow]: ...
    @overload
    def records(self, module: Literal["workflow_collections"]) -> RecordSet[WorkflowCollection]: ...
    @overload
    def records(self, module: Literal["files"]) -> RecordSet[FileRecord]: ...
    @overload
    def records(self, module: Literal["people"]) -> RecordSet[User]: ...
    @overload
    def records(self, module: Literal["teams"]) -> RecordSet[Team]: ...
    @overload
    def records(self, module: Literal["roles"]) -> RecordSet[Role]: ...
    @overload
    def records(self, module: Literal["api_keys"]) -> RecordSet[ApiKey]: ...
    @overload
    def records(self, module: str) -> RecordSet[Any]: ...
    def records(self, module: str) -> RecordSet[Any]:
        """Return a :class:`~pyfsr.records.RecordSet` for generic CRUD on ``module``.

        Reads come back as typed models (Alert/Incident/Task/Comment, else a
        dict-compatible ``BaseRecord``); pass ``raw=True`` on an individual read
        for a plain dict.

        Example:
            >>> incidents = client.records("incidents")
            >>> page = incidents.query(Query().eq("status.itemValue", "Open").limit(50))
            >>> for inc in incidents.iterate():
            ...     print(inc.uuid, inc["name"])
        """
        return RecordSet(self, module)

    def aggregate_many(
        self, specs: dict[str, dict[str, Any]], *, max_workers: int = 8
    ) -> dict[str, list[AggregateRow]]:
        """Run several modules' aggregations **concurrently**, keyed by module.

        ``specs`` maps a module name to the keyword arguments for that module's
        :meth:`RecordSet.aggregate <pyfsr.records.RecordSet.aggregate>` call —
        e.g. ``{"alerts": {"group_by": "severity.itemValue", "count": True},
        "incidents": {"count": True}}``. Each aggregation is an independent
        ``POST /api/query/<module>``, so they run in a bounded thread pool
        instead of one-after-another; the dashboard-style sweep that was N
        round-trips becomes roughly one. A module whose aggregation raises lands
        as an empty ``[]`` so one failure never sinks the whole sweep.

        Returns a dict mapping each module to its list of
        :class:`~pyfsr.models._system.AggregateRow` results.
        """
        from ._concurrency import map_threaded

        items = list(specs.items())

        def _one(item: tuple[str, dict[str, Any]]) -> tuple[str, list[AggregateRow]]:
            module, kwargs = item
            try:
                return module, self.records(module).aggregate(**kwargs)
            except Exception:  # noqa: BLE001 - report empty, don't abort the sweep
                return module, []

        return dict(map_threaded(_one, items, max_workers=max_workers, on_error="raise"))

    def list_modules(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """List every module on the appliance as ``[{type, label, plural}, ...]``.

        Discovery shortcut for :meth:`ModulesAPI.list <pyfsr.api.modules.ModulesAPI.list>`
        — learn the right module ``type`` (and plural name) before a record lookup.
        """
        return self.modules.list(refresh=refresh)

    def describe_module(self, module: str, *, refresh: bool = False) -> dict[str, Any]:
        """Describe one module's fields (name/type/required/picklist).

        Shortcut for
        :meth:`ModulesAPI.describe <pyfsr.api.modules.ModulesAPI.describe>`.
        """
        return self.modules.describe(module, refresh=refresh)

    def version(self) -> str | dict[str, Any]:
        """Get the FortiSOAR product version (tries multiple endpoints).

        Attempts to retrieve the FortiSOAR build version via a fallback chain
        of endpoints, returning the first successful response as a version
        string or a dict with build details:

        1. ``GET /cyops_version.json`` — the canonical version file, e.g.
           ``{"version": "8.0.0-6034"}``. Live-verified across releases.
        2. ``GET /api/3/appliances`` (reads ``@version`` / ``build`` if present)
        3. License details endpoint (``GET /api/auth/license``, via system API)
        4. Public version endpoint (``GET /api/version``, via system API)

        Returns:
            str | dict[str, Any]: A clean version string (e.g., ``"7.4.2"``),
            or a dict like ``{"version": "7.4.2", "build": "..."}`` if
            multiple fields are present.

        Raises:
            FortiSOARException: If all fallback endpoints return 404 or other
                failures; the message names every endpoint that was tried.

        Example:
            >>> v = client.version()                       # doctest: +SKIP
            >>> print(v)                                   # doctest: +SKIP
            7.4.2
            >>> v = client.version()                       # doctest: +SKIP
            >>> print(v["version"] if isinstance(v, dict) else v)  # doctest: +SKIP
            7.4.2
        """
        errors = []

        # Primary: /cyops_version.json — canonical version file, served at the
        # root of the configured base URL (outside /api/3).
        cyops_url = urljoin(self.base_url, "/cyops_version.json")
        try:
            resp = self.session.get(cyops_url, timeout=self.timeout, verify=self.verify_ssl)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("version"):
                    return data["version"]
        except Exception as e:  # noqa: BLE001 - tolerate and fall through
            errors.append(f"/cyops_version.json: {type(e).__name__}")

        # Fallback 1: /api/3/appliances
        try:
            resp = self.get("/api/3/appliances")
            if isinstance(resp, dict) and resp:
                # Try to extract version/build from appliance record
                for key in ("@version", "version", "build"):
                    if key in resp:
                        return resp[key]
                # If appliances dict has data, return it
                if any(k for k in resp if k != "@type"):
                    return resp
        except Exception as e:
            errors.append(f"/api/3/appliances: {type(e).__name__}")

        # Fallback 2: license endpoint (via system API)
        try:
            license_info = self.system.license()
            if isinstance(license_info, dict):
                # Extract version/build if present
                for key in ("version", "build", "@version"):
                    if key in license_info:
                        return license_info[key]
                # Return whole dict if it has useful content
                if any(k for k in license_info if k != "@type"):
                    return license_info
        except Exception as e:
            errors.append(f"/api/auth/license: {type(e).__name__}")

        # Fallback 3: public version endpoint (via system API)
        try:
            version_info = self.system.version()
            if isinstance(version_info, dict):
                # Extract version string if present
                for key in ("version", "build", "@version"):
                    if key in version_info:
                        return version_info[key]
                # Return whole dict if it has useful content
                if version_info and any(k for k in version_info if k != "@type"):
                    return version_info
        except Exception as e:
            errors.append(f"/api/version: {type(e).__name__}")

        # All fallbacks exhausted
        endpoint_list = ", ".join(["/cyops_version.json", "/api/3/appliances", "/api/auth/license", "/api/version"])
        error_detail = "; ".join(errors) if errors else "all endpoints returned empty"
        raise FortiSOARException(
            f"Could not retrieve FortiSOAR version from any fallback endpoint ({endpoint_list}): {error_detail}"
        )
