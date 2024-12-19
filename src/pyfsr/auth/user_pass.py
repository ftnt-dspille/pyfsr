import requests

from .base import BaseAuth


class UserPasswordAuth(BaseAuth):
    def __init__(self, username: str, password: str, base_url: str, verify_ssl: bool = True):
        self.username = username
        self.password = password
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        self.token = self._authenticate()

    def _authenticate(self) -> str:
        auth_url = f"{self.base_url}/auth/authenticate"
        payload = {
            "credentials": {
                "loginid": self.username,
                "password": self.password
            }
        }
        response = requests.post(auth_url, json=payload, verify=self.verify_ssl)
        response.raise_for_status()
        return response.json()['token']

    def get_auth_headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
