"""Static validation of the Jinja expressions embedded in playbook step arguments.

FortiSOAR renders Jinja inside step ``arguments`` at execution time, so a
malformed expression — or one whose *type* is wrong for the field it feeds —
surfaces only as a runtime 400/500 mid-run, long after deployment. This module
lints those expressions *before* deploy, catching the classes of bug that
motivated it (see ``AGENT_OBSERVABILITY_TOOLING_PLAN.md`` Track D4):

- **Syntax errors** — an unclosed ``{{``/``{%``, a bad filter call, etc.
- **Unknown filters** — a filter name FortiSOAR does not register (a typo, or a
  stock-Jinja filter FSR omits), checked against the appliance's filter catalog.
- **``picklist``-returns-dict** — the exact bug from the FortiEDR C2 scenario: a
  whole-value ``{{ "X" | picklist("V") }}`` with no key argument evaluates to the
  *dict* picklist item, not a string, so the field POST fails with a 400. The fix
  is a key extraction: ``picklist("V", "@id")``.

The core (:func:`validate_jinja_expressions`) is pure — it takes a list of step
dicts and returns issues, with no client or network — so it validates compiled
output before it is ever posted. :meth:`pyfsr.api.playbooks.PlaybooksAPI.validate_jinja`
wraps it to lint a live playbook by uuid.

``jinja2`` is an optional dependency (not required by the client). When present,
expressions get a real parse; when absent, a delimiter-balance check stands in so
validation still runs everywhere — only the precise syntax-error messages are lost.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

# The filters FortiSOAR registers in its Jinja environment (ansible-derived set
# plus FSR's own: picklist, fromIRI, iriToLink, getRelativeDate, …). Harvested
# from the reference DB's observed usage + registered macros. A name absent here
# is either a typo or a stock-Jinja filter FSR does not expose — both worth a
# warning before deploy.
FSR_JINJA_FILTERS: frozenset[str] = frozenset(
    """
    abs attr b64decode b64encode basename batch bool capitalize center checksum cidr_merge
    combinations combine comment comp_type5 count count_occurrence counter d default dict2items
    dictsort difference dirname e escape expanduser expandvars extract extract_artifacts
    extract_cef fileglob filesizeformat find_indicators first flatten float forceescape format
    fromIRI from_json from_yaml from_yaml_all getRelativeDate groupby hash hash_salt html2text
    html2texthash htmltotext human_readable human_to_bytes hwaddr indent int intersect ip4_hex
    ip_range ipaddr ipmath ipsubnet ipv4 ipv6 ipwrap iriToLink items items2dict join json2html
    json_query last length list loadRelationships log logParse lower macaddr mandatory map
    markdown2html max md5 min network_in_network network_in_usable next_nth_usable np_batch
    np_join np_split np_unique nthhost parse_cli parse_cli_textfsm parse_xml password_hash
    path_join permutations picklist pow pprint previous_nth_usable product quote random random_mac
    readfile realpath reduce_on_network regex_escape regex_findall regex_replace regex_search
    reject rejectattr rekey_on_member relpath replace resolveRange reverse root round safe select
    selectattr sha1 shuffle slaac slice sort split splitext strftime string striptags subelements
    sum symmetric_difference ternary title toDict toJSON to_datetime to_json to_nice_json
    to_nice_yaml to_uuid to_yaml tojson trim truncate type5_pw type_debug union unique upper
    urldecode urlencode urlize urlsplit vlan_expander vlan_parser win_basename win_dirname
    win_splitdrive wordcount wordwrap xml_to_dict xmlattr yaql zip zip_longest
    """.split()
)

# A string is Jinja-bearing if it carries *any* Jinja delimiter — including a lone
# opener like ``{{`` with no close, so an unclosed expression still reaches the
# syntax check (which flags the imbalance) rather than being skipped as plain text.
_HAS_JINJA = re.compile(r"\{\{|\}\}|\{%|%\}")
# Filter invocations: the name after a ``|`` (but not ``||``). Captures ``picklist`` in
# ``x | picklist(...)`` and ``default`` in ``x | default("")``.
_FILTER_CALL = re.compile(r"\|\s*([a-zA-Z_]\w*)")
# A whole-value single output expression: the entire string is one ``{{ ... }}``.
_WHOLE_VALUE = re.compile(r"^\s*\{\{(?P<body>.*)\}\}\s*$", re.DOTALL)
# A ``picklist(...)`` call — used to count its argument groups.
_PICKLIST_CALL = re.compile(r"\bpicklist\s*\((?P<args>[^)]*)\)")


class JinjaIssue(BaseModel):
    """One problem found in a step's Jinja expression.

    ``severity`` is ``"error"`` for things that will definitely fail (syntax,
    unbalanced delimiters) and ``"warning"`` for likely-wrong-but-not-certain
    findings (unknown filter, ``picklist`` with no key). ``path`` locates the
    argument within the step (e.g. ``"arguments.request.ip"``).
    """

    model_config = ConfigDict(extra="allow")

    step: str
    path: str
    expression: str
    severity: str
    kind: str
    message: str


def _iter_strings(value: Any, path: str) -> list[tuple[str, str]]:
    """Yield ``(path, string)`` for every string leaf in a nested arg structure."""
    out: list[tuple[str, str]] = []
    if isinstance(value, str):
        out.append((path, value))
    elif isinstance(value, dict):
        for k, v in value.items():
            out.extend(_iter_strings(v, f"{path}.{k}"))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            out.extend(_iter_strings(v, f"{path}[{i}]"))
    return out


def _syntax_issue(expr: str) -> str | None:
    """Return a syntax-error message for ``expr``, or ``None`` if it parses.

    Uses jinja2 when installed for a precise message; otherwise falls back to a
    delimiter-balance check (``{{`` vs ``}}``, ``{%`` vs ``%}``) so the common
    unclosed-brace mistake is still caught without the dependency.
    """
    try:
        from jinja2 import Environment
        from jinja2.exceptions import TemplateSyntaxError
    except ImportError:
        opens = expr.count("{{") + expr.count("{%")
        closes = expr.count("}}") + expr.count("%}")
        if opens != closes:
            return f"unbalanced Jinja delimiters ({opens} open, {closes} close)"
        return None
    try:
        Environment().parse(expr)
    except TemplateSyntaxError as exc:
        return f"{exc.message} (line {exc.lineno})"
    return None


def validate_jinja_expressions(
    steps: list[dict[str, Any]],
    *,
    known_filters: frozenset[str] | set[str] | None = None,
) -> list[JinjaIssue]:
    """Lint the Jinja in a list of playbook step dicts. Pure — no client/network.

    Args:
        steps: step dicts, each with a ``name`` and an ``arguments`` mapping (the
            shape from ``Workflow.steps`` / a compiled playbook envelope).
        known_filters: the set of filter names to treat as valid. Defaults to
            :data:`FSR_JINJA_FILTERS` (the appliance catalog). Pass a set derived
            from a specific box to validate against that box's registered filters.

    Returns:
        A list of :class:`JinjaIssue`, in step-then-argument order (empty if
        clean). Errors and warnings are interleaved by position; filter on
        ``severity`` to separate them.
    """
    catalog = known_filters if known_filters is not None else FSR_JINJA_FILTERS
    issues: list[JinjaIssue] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or step.get("stepName") or "<unnamed>")
        args = step.get("arguments")
        if not isinstance(args, (dict, list)):
            continue
        for path, text in _iter_strings(args, "arguments"):
            if not _HAS_JINJA.search(text):
                continue

            syntax = _syntax_issue(text)
            if syntax is not None:
                issues.append(
                    JinjaIssue(
                        step=name,
                        path=path,
                        expression=text,
                        severity="error",
                        kind="syntax",
                        message=syntax,
                    )
                )
                # A syntax error makes the filter/type checks unreliable — skip them.
                continue

            for fname in _FILTER_CALL.findall(text):
                if fname not in catalog:
                    issues.append(
                        JinjaIssue(
                            step=name,
                            path=path,
                            expression=text,
                            severity="warning",
                            kind="unknown_filter",
                            message=f"filter {fname!r} is not a registered FortiSOAR Jinja filter "
                            "(a typo, or a stock-Jinja filter FSR does not expose)",
                        )
                    )

            issues.extend(_picklist_type_issues(name, path, text))
    return issues


def _picklist_type_issues(step: str, path: str, text: str) -> list[JinjaIssue]:
    """Flag a whole-value ``picklist`` call with no key argument (returns a dict).

    ``{{ "Sev" | picklist("High") }}`` evaluates to the whole picklist *item dict*,
    not a string, so posting it into a string/IRI field 400s. The fix is a key:
    ``picklist("High", "@id")``. Only a *whole-value* expression is flagged — a
    ``picklist`` embedded in a larger string is stringified by Jinja and is fine.
    """
    whole = _WHOLE_VALUE.match(text)
    if not whole:
        return []
    body = whole.group("body")
    out: list[JinjaIssue] = []
    for m in _PICKLIST_CALL.finditer(body):
        # Count comma-separated argument groups (naive but sufficient: picklist
        # args are quoted literals, no nested commas in practice).
        arg_str = m.group("args").strip()
        n_args = 0 if not arg_str else arg_str.count(",") + 1
        if n_args < 2:
            out.append(
                JinjaIssue(
                    step=step,
                    path=path,
                    expression=text,
                    severity="warning",
                    kind="picklist_returns_dict",
                    message="picklist() with no key argument returns the whole item dict, not a "
                    "string — a string/IRI field will reject it (400). Add a key, e.g. "
                    'picklist("Value", "@id").',
                )
            )
    return out
