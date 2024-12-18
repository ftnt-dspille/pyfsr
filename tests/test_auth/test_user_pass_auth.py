import pytest
from pyfsr.auth.user_pass import UserPasswordAuth
from pyfsr.exceptions import AuthenticationError


def test_user_pass_auth_success(mocker, mock_response):
    """Test successful username/password authentication"""
    mock_token = "mock-jwt-token"
    mock_post = mocker.patch('requests.post',
                             return_value=mock_response(json_data={'token': mock_token}))

    auth = UserPasswordAuth('username', 'password', 'https://test.com')
    headers = auth.get_auth_headers()

    assert headers['Authorization'] == f'Bearer {mock_token}'
    assert headers['Content-Type'] == 'application/json'
    mock_post.assert_called_once()


def test_user_pass_auth_failure(mocker, mock_response):
    """Test failed username/password authentication"""
    mocker.patch('requests.post',
                 return_value=mock_response(status_code=401, raise_error=True))

    with pytest.raises(AuthenticationError):
        UserPasswordAuth('username', 'wrong-password', 'https://test.com')
