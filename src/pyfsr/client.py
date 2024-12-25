"""Main client class for FortiSOAR API"""
from typing import Union, Optional, Dict, Any
from urllib.parse import urljoin

import requests

from .api.alerts import AlertsAPI
from .api.export_config import ExportConfigAPI
from .api.solution_packs import SolutionPackAPI
from .auth.api_key import APIKeyAuth
from .auth.user_pass import UserPasswordAuth
from .utils.file_operations import FileOperations


class FortiSOAR:
    """
    Main FortiSOAR client class for interacting with the FortiSOAR API.
    """

    def __init__(
            self,
            base_url: str,
            auth: Union[str, tuple],
            verify_ssl: bool = True,
            suppress_insecure_warnings: bool = False
    ):

        """
        Initialize the FortiSOAR client.

       Args:
           base_url (str): The base URL for the FortiSOAR API.
           auth (Union[str, tuple]): The authentication method, either an API key (str) or a tuple of (username, password).
           verify_ssl (bool, optional): Whether to verify SSL certificates. Defaults to True.
           suppress_insecure_warnings (bool, optional): Whether to suppress insecure request warnings. Defaults to False.

       Raises:
           ValueError: If the provided authentication method is invalid.
       """
        # Ensure base_url starts with https://
        if not base_url.startswith('https://'):
            base_url = f'https://{base_url}'
        self.base_url: str = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.verify_ssl = verify_ssl
        if suppress_insecure_warnings:
            requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

        # Setup authentication
        if isinstance(auth, str):
            self.auth = APIKeyAuth(auth)
        elif isinstance(auth, tuple) and len(auth) == 2:
            username, password = auth
            self.auth = UserPasswordAuth(username, password, self.base_url, self.verify_ssl)
        else:
            raise ValueError("Invalid authentication provided")

        # Apply authentication headers
        self.session.headers.update(self.auth.get_auth_headers())

        # Initialize API interfaces
        self.alerts: AlertsAPI = AlertsAPI(self)

        # Initialize file operations utility
        self.files: FileOperations = FileOperations(self)

        # Add solution packs API
        self.export_config: ExportConfigAPI = ExportConfigAPI(self)

        self.solution_packs: SolutionPackAPI = SolutionPackAPI(self, self.export_config)

    def request(
            self,
            method: str,
            endpoint: str,
            params: Optional[Dict] = None,
            data: Optional[Dict] = None,
            files: Optional[Dict] = None,
            headers: Optional[Dict] = None,
            **kwargs
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
            **kwargs: Additional arguments passed to requests

        Returns:
            requests.Response: Response from the API

        Raises:
            ValidationError: When request data validation fails
            AuthenticationError: When authentication fails
            PermissionError: When user lacks required permissions
            ResourceNotFoundError: When requested resource is not found
            APIError: For other API errors
        """
        # Ensure endpoint starts with /
        if not endpoint.startswith('/'):
            endpoint = f'/{endpoint}'

        # Add API version prefix if not present
        if not endpoint.startswith(('/api/3/', '/auth/', '/api/public/')):
            endpoint = f'/api/3{endpoint}'

        url = urljoin(self.base_url, endpoint)

        # Merge any additional headers
        request_headers = self.session.headers.copy()
        if headers:
            request_headers.update(headers)

        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=data if files is None else None,
                data=data if files is not None else None,
                files=files,
                headers=request_headers,
                **kwargs
            )
            response.raise_for_status()
            return response

        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response'):
                handle_api_error(e.response)
            raise

    def get(self, endpoint: str, params: Optional[Dict] = None, **kwargs) -> Union[Dict[str, Any], bytes]:
        """
        Perform GET request and return response based on content type.

        Returns JSON for application/json responses and bytes for binary responses.
        """
        response = self.request('GET', endpoint, params=params, **kwargs)
        content_type = response.headers.get('Content-Type', '')

        if 'application/json' in content_type:
            return response.json()
        elif any(binary_type in content_type for binary_type in ['application/zip', 'application/octet-stream']):
            return response.content
        else:
            # Default to JSON if content type is not explicitly specified
            return response.json()

    def post(self, endpoint: str, data: Optional[Dict] = None, files: Optional[Dict] = None,
             params: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """Perform POST request and return JSON response"""
        response = self.request('POST', endpoint, params=params, data=data, files=files, **kwargs)
        return response.json()

    def put(self, endpoint: str, data: Optional[Dict] = None, params: Optional[Dict] = None, **kwargs) -> Dict[
        str, Any]:
        """Perform PUT request and return JSON response"""
        response = self.request('PUT', endpoint, params=params, data=data, **kwargs)
        return response.json()

    def delete(self, endpoint: str, params: Optional[Dict] = None, **kwargs) -> None:
        """Perform DELETE request"""
        self.request('DELETE', endpoint, params=params, **kwargs)

    def query(self, module: str, query_data: Dict) -> Dict[str, Any]:
        """
        Execute a query against a module

        Args:
            module: Name of the module to query
            query_data: Query parameters and filters

        Returns:
            Query results
        """
        return self.post(f'/query/{module}', data=query_data)
