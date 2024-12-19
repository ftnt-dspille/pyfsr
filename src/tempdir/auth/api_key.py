from .base import BaseAuth


class APIKeyAuth(BaseAuth):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_auth_headers(self) -> dict:
        return {
            'Authorization': f'API-KEY {self.api_key}',
            'Content-Type': 'application/json'
        }
