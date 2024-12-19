class FortiSOARException(Exception):
    """Base exception for FortiSOAR client"""
    pass

class AuthenticationError(FortiSOARException):
    """Authentication related errors"""
    pass

class APIError(FortiSOARException):
    """API related errors"""
    pass
