import requests

from ._url import normalize_base_url
from .base import BaseAuth


class UserPasswordAuth(BaseAuth):
    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = True):
        super().__init__()
        self.base_url = normalize_base_url(base_url)
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.token = self._authenticate()

    def _authenticate(self) -> str:
        auth_url = f"{self.base_url}/auth/authenticate"
        payload = {"credentials": {"loginid": self.username, "password": self.password}}
        response = requests.post(auth_url, json=payload, verify=self.verify_ssl)
        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            hint = ""
            if not detail and response.status_code in (404, 405, 502, 503):
                # A blank body on one of these almost always means the request
                # never reached FortiSOAR's auth endpoint at all — wrong port
                # (FSR_PORT unset/ignored), wrong scheme, or a proxy/LB in the
                # way — not bad credentials. Say so; "authentication failed"
                # with nothing else sends people credential-debugging instead.
                hint = (
                    " (empty response body — this usually means the request "
                    "didn't reach FortiSOAR's API at all: check the port "
                    "(FSR_PORT / port=), scheme, and that base_url points at "
                    "the appliance, not a proxy/load balancer in front of it)"
                )
            raise requests.exceptions.HTTPError(
                f"Authentication failed ({response.status_code}) at {auth_url}: {detail}{hint}",
                response=response,
            )
        return response.json()["token"]

    def get_auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def refresh(self) -> dict:
        """Re-authenticate (mint a fresh session token) and return new headers.

        FortiSOAR session tokens expire; a long-lived client that authenticated
        once at construction will eventually get ``401``/``403`` ("HMAC signature
        has expired"). The client calls this to recover and retry the request.
        """
        self.token = self._authenticate()
        return self.get_auth_headers()

    @property
    def can_refresh(self) -> bool:
        return True
