"""Authentication strategies for the FortiSOAR client.

The client authenticates with either a username/password
(:class:`~pyfsr.auth.user_pass.UserPasswordAuth`, which fetches and refreshes a
bearer token) or a static API key. Both implement the common
:class:`~pyfsr.auth.base.BaseAuth` interface — supplying request headers and
declaring which operations they support — so the client is agnostic to which one
is in use. You normally select an auth method by the arguments you pass to
:class:`~pyfsr.client.FortiSOAR`, not by constructing these directly.
"""
