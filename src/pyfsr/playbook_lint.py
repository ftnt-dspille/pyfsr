"""Playbook lint — live-target preflight for compiled playbooks (AGENT_DX D3).

The compiler validates a playbook's *shape* offline, but a connector step
compiles and validates fine and then fails at **run time** with
``INTEGRATION-12: could not find a connector configuration`` when the referenced
connector has no configuration on the target box. The friendly ``code:`` form
hides that a ``code-snippet`` connector config is even needed; every connector
step shares this failure mode.

This module closes that gap with a two-step, mostly-offline lint:

- :func:`connector_refs` walks a compiled ``workflow_collections`` envelope and
  yields every connector-bearing step (the Connector step type and the SMTP
  *Send Email* shortcut). Pure — no client, unit-testable against a fixture.
- :func:`check_connector_configs` diffs those refs against what
  ``client.connectors.list_configured()`` reports and returns warn-level findings
  (``not-installed`` / ``no-config`` / ``config-missing``). The only part that
  touches the network, and it batches a single connector listing.

Findings are **warnings, never errors**: deploying a playbook before its
connector configs exist is a legitimate workflow (land the automation, then wire
credentials). The value is a named, actionable warning whose ``fix_hint`` points
at the one-liner — ``client.connectors.create_configuration(name,
client.connectors.default_config(name), name="default", default=True)`` (the A1
``default_config`` helper).

See ``docs/plans/PLAYBOOK_LINT_DESIGN.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import FortiSOAR

#: Canonical step-type UUIDs that carry an ``arguments.connector`` reference.
#: ``STEP_CONNECTOR`` is the generic Connector action; ``STEP_SEND_EMAIL`` is the
#: SMTP *Send Email* shortcut (also pins ``connector`` + ``config``). Mirrors
#: ``fsr_playbooks.compiler.rulesets.STEP_CONNECTOR``.
STEP_CONNECTOR = "0bfed618-0316-11e7-93ae-92361f002671"
STEP_SEND_EMAIL = "4c0019b2-055c-44d0-968c-678a0c2d762e"
_CONNECTOR_STEP_UUIDS = frozenset({STEP_CONNECTOR, STEP_SEND_EMAIL})


@dataclass(frozen=True)
class ConnectorRef:
    """A single connector-bearing step found in a compiled playbook."""

    connector: str
    operation: str | None
    version: str | None
    config: str | None  # pinned config UUID, if the step names one
    workflow: str  # owning playbook name (for the message)
    step: str  # step name


@dataclass(frozen=True)
class LintFinding:
    """One warn-level connector-config problem against a live target."""

    severity: str  # always "warn" — lint never blocks a deploy
    connector: str
    code: str  # "not-installed" | "no-config" | "config-missing"
    message: str
    fix_hint: str


def _step_type_uuid(step: dict[str, Any]) -> str | None:
    """The trailing UUID of a step's ``stepType``.

    The emitter writes ``stepType`` as an IRI
    (``/api/3/workflow_step_types/<uuid>``); decompiled/raw forms may carry a
    bare UUID or a ``{"uuid": ...}`` dict. Tolerate all three."""
    st = step.get("stepType")
    if isinstance(st, dict):
        st = st.get("uuid") or st.get("@id")
    if not isinstance(st, str) or not st:
        return None
    return st.rstrip("/").rsplit("/", 1)[-1]


def connector_refs(fsr_json: dict[str, Any]) -> list[ConnectorRef]:
    """Every connector-bearing step in a compiled ``workflow_collections`` envelope.

    Walks ``data[].workflows[].steps[]`` (tolerating ``collections`` as an alias
    for ``data``), keying off each step's ``stepType`` UUID. Steps without an
    ``arguments.connector`` are skipped even if the UUID matches. Offline-pure.
    """
    refs: list[ConnectorRef] = []
    collections = fsr_json.get("data") or fsr_json.get("collections") or []
    for coll in collections:
        for wf in coll.get("workflows") or []:
            wf_name = wf.get("name") or coll.get("name") or "?"
            for step in wf.get("steps") or []:
                if _step_type_uuid(step) not in _CONNECTOR_STEP_UUIDS:
                    continue
                args = step.get("arguments") or {}
                connector = args.get("connector")
                if not connector:
                    continue
                refs.append(
                    ConnectorRef(
                        connector=connector,
                        operation=args.get("operation"),
                        version=args.get("version"),
                        config=args.get("config") or None,
                        workflow=wf_name,
                        step=step.get("name") or "?",
                    )
                )
    return refs


def _fix_hint(connector: str) -> str:
    return (
        f'client.connectors.create_configuration("{connector}", '
        f'client.connectors.default_config("{connector}"), name="default", default=True)'
    )


def check_connector_configs(client: FortiSOAR, refs: list[ConnectorRef]) -> list[LintFinding]:
    """Diff connector ``refs`` against what the target has installed + configured.

    One :meth:`~pyfsr.api.connectors.ConnectorsAPI.list_configured` call backs the
    whole pass. Per distinct connector:

    - not present at all → ``not-installed``;
    - installed but no configurations → ``no-config``;
    - a ref pins a ``config`` UUID absent from the connector's configurations →
      ``config-missing``.

    Returns warn-level findings, deduped by ``(connector, code, config)``.
    """
    installed = {c.name: c for c in client.connectors.list_configured(refresh=True)}
    findings: list[LintFinding] = []
    seen: set[tuple[str, str, str | None]] = set()

    def add(code: str, ref: ConnectorRef, message: str) -> None:
        key = (ref.connector, code, ref.config if code == "config-missing" else None)
        if key in seen:
            return
        seen.add(key)
        findings.append(
            LintFinding(
                severity="warn",
                connector=ref.connector,
                code=code,
                message=message,
                fix_hint=_fix_hint(ref.connector),
            )
        )

    for ref in refs:
        conn = installed.get(ref.connector)
        if conn is None:
            add(
                "not-installed",
                ref,
                f"{ref.connector!r} is not installed on the target (step {ref.step!r} in {ref.workflow!r})",
            )
            continue
        configs = conn.configurations or []
        if not configs:
            add(
                "no-config",
                ref,
                f"{ref.connector!r} is installed but has no configuration (step {ref.step!r} in {ref.workflow!r})",
            )
            continue
        if ref.config and not any(c.config_id == ref.config for c in configs):
            add(
                "config-missing",
                ref,
                f"step {ref.step!r} in {ref.workflow!r} pins config {ref.config!r}, "
                f"which no longer exists on {ref.connector!r}",
            )

    return findings
