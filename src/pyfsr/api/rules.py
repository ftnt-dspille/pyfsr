"""The rules module — ``client.rules``.

FortiSOAR splits "rules" across **two applications**, and this API hides the seam:

- **Delivery rules** (``list_delivery_rules``) and **channels** (``list_channels``)
  live in the standalone *rule engine*, reached through a front-door proxy.
- **Preprocessing rules** (``list_preprocessing_rules``) are ordinary crudhub
  records at ``/api/3/preprocessing_rules``.

Two rule-engine quirks are absorbed here so callers never meet them:

1. **Dual proxy root.** The rule engine answers at ``/rule/api/`` on some builds and
   ``/api/rule/api/`` on others; the wrong one falls through to the SPA and returns
   HTML that fails JSON parsing. :meth:`RulesAPI.rule_engine_get` tries both and
   caches whichever answers.
2. **No server-side name filter.** The rule-engine collections ignore a ``name``
   param, so name lookups fetch the (small) list once and match client-side. The
   crudhub preprocessing collection *does* filter server-side, so it does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models import DeliveryRule, PreprocessingRule, RuleChannel
from ..pagination import extract_members
from .base import BaseAPI

if TYPE_CHECKING:
    from ..client import FortiSOAR

#: Front-door roots the rule engine may be proxied at, in probe order.
_RULE_ROOTS = ("/rule/api/", "/api/rule/api/")

#: Rule-engine collections take no paging params; ask for everything at once.
_ALL = 2147483647

_PREPROCESSING = "/api/3/preprocessing_rules"


class RulesAPI(BaseAPI):
    """Read delivery rules, rule channels, and preprocessing rules.

    Example:
        .. code-block:: python

            # Delivery rules (rule engine)
            for rule in client.rules.list_delivery_rules():
                print(rule.name, rule.entity_type, rule.is_active)

            # The channel a rule action delivers through
            channel = client.rules.get_channel("In-App Notifications")

            # Preprocessing rules (crudhub)
            pre = client.rules.get_preprocessing_rule(
                "Enforcing File Attachments for File Indicators"
            )
    """

    def __init__(self, client: FortiSOAR) -> None:
        super().__init__(client)
        # Which of _RULE_ROOTS answered, remembered per client so repeated calls
        # don't re-pay the failed-probe round-trip.
        self._root: str | None = None

    def rule_engine_get(self, subpath: str) -> Any:
        """GET a rule-engine collection, tolerating either front-door root.

        Tries ``/rule/api/`` then ``/api/rule/api/`` and caches whichever answers
        with JSON. The unmatched root falls through to the SPA (an HTML body that
        fails to parse), which is why a failure here is retried rather than raised.

        Args:
            subpath: collection path below the root (e.g. ``"rules/"``).

        Raises:
            RuntimeError: if neither root yields JSON.
        """
        roots = (self._root,) if self._root else _RULE_ROOTS
        last_exc: Exception | None = None
        for root in roots:
            try:
                result = self.client.get(f"{root}{subpath}", params={"limit": _ALL})
            except Exception as exc:  # SPA fallthrough / 404 on the wrong route
                last_exc = exc
                continue
            self._root = root
            return result
        # A cached root that later fails (e.g. re-probe after an upgrade) should
        # fall back to a full probe rather than stay stuck.
        if self._root:
            self._root = None
            return self.rule_engine_get(subpath)
        raise RuntimeError(f"rule-engine app not reachable at {' or '.join(_RULE_ROOTS)} ({last_exc})")

    def list_delivery_rules(self, *, typed: bool = True) -> list[DeliveryRule] | list[dict[str, Any]]:
        """List every delivery rule (``GET <rule-engine>/rules/``).

        Args:
            typed: parse into :class:`~pyfsr.models.DeliveryRule` (default) or return dicts.
        """
        members = [m for m in extract_members(self.rule_engine_get("rules/")) if isinstance(m, dict)]
        if not typed:
            return members
        return [DeliveryRule.model_validate(m) for m in members]

    def get_delivery_rule(self, name: str, *, typed: bool = True) -> DeliveryRule | dict[str, Any]:
        """Resolve a delivery rule by exact ``name`` (matched client-side).

        Raises:
            ValueError: if no delivery rule carries that name.
        """
        return self._by_name(self.list_delivery_rules(typed=typed), name, "delivery rule")

    def list_channels(self, *, typed: bool = True) -> list[RuleChannel] | list[dict[str, Any]]:
        """List every rule channel (``GET <rule-engine>/channel/``).

        Note the collection is singular (``channel/``), unlike ``rules/``.
        """
        members = [m for m in extract_members(self.rule_engine_get("channel/")) if isinstance(m, dict)]
        if not typed:
            return members
        return [RuleChannel.model_validate(m) for m in members]

    def get_channel(self, name: str, *, typed: bool = True) -> RuleChannel | dict[str, Any]:
        """Resolve a rule channel by exact ``name`` (matched client-side).

        Raises:
            ValueError: if no channel carries that name.
        """
        return self._by_name(self.list_channels(typed=typed), name, "rule channel")

    def list_preprocessing_rules(
        self, params: dict[str, Any] | None = None, *, typed: bool = True
    ) -> list[PreprocessingRule] | list[dict[str, Any]]:
        """List preprocessing rules (``GET /api/3/preprocessing_rules``)."""
        members = [m for m in extract_members(self.client.get(_PREPROCESSING, params=params)) if isinstance(m, dict)]
        if not typed:
            return members
        return [PreprocessingRule.model_validate(m) for m in members]

    def get_preprocessing_rule(self, name: str, *, typed: bool = True) -> PreprocessingRule | dict[str, Any]:
        """Resolve a preprocessing rule by exact ``name``.

        Unlike the rule-engine collections this one filters server-side.

        Raises:
            ValueError: if no preprocessing rule carries that name.
        """
        for record in extract_members(self.client.get(_PREPROCESSING, params={"name": name})):
            if isinstance(record, dict):
                return PreprocessingRule.model_validate(record) if typed else record
        raise ValueError(f"preprocessing rule {name!r} not found")

    @staticmethod
    def _by_name(records: list[Any], name: str, kind: str) -> Any:
        """Exact-match ``name`` over already-fetched records (typed or dict)."""
        for record in records:
            found = record.get("name") if isinstance(record, dict) else getattr(record, "name", None)
            if found == name:
                return record
        raise ValueError(f"{kind} {name!r} not found")
