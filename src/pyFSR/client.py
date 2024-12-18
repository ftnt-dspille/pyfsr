from typing import Union
from urllib.parse import urljoin

import requests

from .api.alerts import AlertsAPI
from .auth.api_key import APIKeyAuth
from .auth.user_pass import UserPasswordAuth
from .constants import API_PATH


class FortiSOAR:
    """Main client class for FortiSOAR API"""

    def __init__(
            self,
            base_url: str,
            auth: Union[str, tuple],
            verify_ssl: bool = True
    ):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.verify_ssl = verify_ssl

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
        self.alerts = AlertsAPI(self)

    def request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make HTTP request to FortiSOAR API"""
        url = urljoin(self.base_url, f"{API_PATH}{endpoint}")
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response
