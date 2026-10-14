"""Fail-fast guards for operations that only work with a JWT (username/password) client.

Some FortiSOAR endpoints reject an API key, and the failure is rarely legible:
``/api/3/bulkupsert/*`` answers a misleading ``Invalid credentials.`` 500, the
built-in MCP servers answer 401 from deep inside fsr-ai. :func:`requires_jwt`
marks the pyfsr methods that hit those endpoints so an API-key client gets a
:class:`~pyfsr.exceptions.UnsupportedAuthOperationError` that says why, before
any request is sent.

The check reads the auth strategy's ``unsupported_operations``
(:class:`~pyfsr.auth.base.BaseAuth`), so it follows the auth class rather than
sniffing for "is this an API key": a new auth strategy opts in by listing the
operation.

Example:
    >>> from pyfsr.auth.base import BaseAuth
    >>> from pyfsr.auth.guards import requires_jwt
    >>> class Thing:
    ...     @requires_jwt(BaseAuth.OPERATION_AUTH, "the DAS /api/auth routes reject API keys")
    ...     def go(self):
    ...         return "ok"
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from ..exceptions import UnsupportedAuthOperationError

F = TypeVar("F", bound=Callable[..., Any])

_FIX = "Build the client with a username and password: FortiSOAR(base_url, username=..., password=...)."


def _auth_of(obj: Any) -> Any:
    """The auth strategy for a client, an API shortcut (``.client``) or an auth object itself."""
    client = getattr(obj, "client", obj)
    return getattr(client, "auth", client)


def ensure_jwt(obj: Any, operation: str, reason: str, *, what: str | None = None) -> None:
    """Raise :class:`UnsupportedAuthOperationError` if ``obj``'s auth cannot do ``operation``.

    ``obj`` is a client, an API shortcut (anything with ``.client``) or an auth
    strategy. Use this for a check that depends on arguments (for example only
    some MCP servers need a forwarded JWT); use :func:`requires_jwt` when the
    whole method is JWT-only.
    """
    auth = _auth_of(obj)
    if operation in getattr(auth, "unsupported_operations", ()):
        subject = what or "this operation"
        raise UnsupportedAuthOperationError(
            operation,
            getattr(auth, "auth_type", type(auth).__name__),
            message=f"{subject} needs JWT auth, which an API-key client does not have: {reason}. {_FIX}",
        )


def requires_jwt(operation: str, reason: str) -> Callable[[F], F]:
    """Mark a method JWT-only: an API-key client raises before any request is sent.

    Args:
        operation: a ``BaseAuth.OPERATION_*`` constant the API-key strategy lists as unsupported.
        reason: why, in a sentence (goes into the error and the docstring note).

    Works on methods of API shortcuts (``self.client.auth``) and of the client
    (``self.auth``), sync or ``async``. The wrapped function gets a
    ``__requires_jwt__`` attribute and a ``.. note::`` in its docstring.
    """

    def deco(fn: F) -> F:
        what = f"{fn.__qualname__}()"

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                ensure_jwt(self, operation, reason, what=what)
                return await fn(self, *args, **kwargs)

            wrapper: Any = awrapper
        else:

            @functools.wraps(fn)
            def swrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                ensure_jwt(self, operation, reason, what=what)
                return fn(self, *args, **kwargs)

            wrapper = swrapper

        wrapper.__requires_jwt__ = operation
        doc = fn.__doc__ or ""
        if "Requires JWT" not in doc:
            note = (
                "\n\n        .. note::\n"
                f"            Requires JWT auth (username/password): {reason}. An API-key client\n"
                "            raises ``UnsupportedAuthOperationError`` before sending anything.\n"
            )
            wrapper.__doc__ = doc.rstrip() + note + "    " if doc else note
        return wrapper  # type: ignore[no-any-return]

    return deco


def jwt_only(operation: str, reason: str) -> Callable[[type], type]:
    """Class decorator: apply :func:`requires_jwt` to every public method the class defines.

    For API shortcuts whose whole surface is JWT-only (everything under
    ``/api/auth/*``). Private methods, properties and inherited methods are left alone.
    """

    def deco(cls: type) -> type:
        for name, member in list(vars(cls).items()):
            if name.startswith("_") or not inspect.isfunction(member):
                continue
            setattr(cls, name, requires_jwt(operation, reason)(member))
        return cls

    return deco
