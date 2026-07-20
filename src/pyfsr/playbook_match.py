"""Client-side structural matching over playbook definitions.

The server-side filter language (:meth:`pyfsr.api.playbooks.PlaybooksAPI.find`)
covers single-dimension and cross-relationship-AND queries cheaply, but three
classes of question it *cannot* express, because ``steps.arguments`` is one JSON
column matched by substring:

  * **same-step precision** -- "a step that is fortigate AND block_ip" (find()
    would also match a playbook with fortigate in one step and block_ip in
    another).
  * **quantities** -- "exactly 2 set-variable steps and at least 1 code-snippet".
  * **parent/child joins** -- "a manual playbook whose referenced child blocks an
    IP".

This module parses each playbook's steps into :class:`StepInfo` and evaluates
composable predicates against the parsed shape. The pure functions here are
appliance-free (unit-testable on fixture dicts); ``PlaybooksAPI`` wires them
to live fetches via ``match`` / ``match_across``.

Example::

    from pyfsr.playbook_match import step, count, trigger, all_of
    # 2 set-variable steps AND exactly 1 code-snippet:
    pred = all_of(count(step(step_type="set_variable"), n=2),
                  count(step(step_type="code_snippet"), n=1))
    hits = client.playbooks.match(pred)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .api.playbooks import STEP_TYPE_NAMES, TRIGGER_TYPE_NAMES

# Reverse map: raw engine trigger name (``cybersponse.*``) -> friendly alias.
# Several aliases share a raw name (``referenced``/``child``); the first wins,
# which is the canonical alias.
_TRIGGER_RAW_TO_FRIENDLY: dict[str, str] = {}
for _alias, _raw in TRIGGER_TYPE_NAMES.items():
    _TRIGGER_RAW_TO_FRIENDLY.setdefault(_raw, _alias)


class StepInfo(BaseModel):
    """One parsed playbook step (the fields structural predicates match on)."""

    model_config = ConfigDict(frozen=True)

    name: str | None
    step_type_raw: str | None
    connector: str | None
    operation: str | None
    arguments: dict[str, Any] = Field(default_factory=dict)


class ParsedPlaybook(BaseModel):
    """A playbook definition reduced to the shape predicates evaluate against."""

    name: str | None
    uuid: str | None
    trigger_type: str | None
    steps: list[StepInfo]
    raw: dict[str, Any] = Field(default_factory=dict)

    def references(self) -> list[str]:
        """UUIDs of child playbooks this playbook references (reference steps).

        A WorkflowReference step targets its child by IRI in the ``workflowReference``
        argument (``/api/3/workflows/<uuid>``) — live-verified, not by name.
        """
        ref_raw = STEP_TYPE_NAMES["reference"]
        uuids = []
        for s in self.steps:
            if s.step_type_raw == ref_raw:
                ref = (s.arguments or {}).get("workflowReference")
                if isinstance(ref, str) and ref:
                    uuids.append(ref.rstrip("/").rsplit("/", 1)[-1])
        return uuids


def _step_type_name(step: dict[str, Any]) -> str | None:
    st = step.get("stepType")
    if isinstance(st, dict):
        return st.get("name")
    if isinstance(st, str):
        return st
    return None


def parse_playbook(workflow: dict[str, Any]) -> ParsedPlaybook:
    """Reduce a ``/api/3/workflows`` definition (with ``steps``) to a :class:`ParsedPlaybook`.

    Pass a workflow fetched with relationships (``find(..., relationships=True)``
    or ``get_definition``); without inlined ``steps`` the parsed step list is empty.
    """
    steps: list[StepInfo] = []
    trigger_type: str | None = None
    for s in workflow.get("steps") or []:
        if not isinstance(s, dict):
            continue
        raw_type = _step_type_name(s)
        args = s.get("arguments") if isinstance(s.get("arguments"), dict) else {}
        steps.append(
            StepInfo(
                name=s.get("name"),
                step_type_raw=raw_type,
                connector=(args or {}).get("connector"),
                operation=(args or {}).get("operation"),
                arguments=args or {},
            )
        )
        # The start step carries the trigger's ``cybersponse.*`` type.
        if raw_type and raw_type.startswith("cybersponse.") and trigger_type is None:
            trigger_type = _TRIGGER_RAW_TO_FRIENDLY.get(raw_type, raw_type)
    return ParsedPlaybook(
        name=workflow.get("name"),
        uuid=workflow.get("uuid"),
        trigger_type=trigger_type,
        steps=steps,
        raw=workflow,
    )


# --------------------------------------------------------------------------- #
# Predicates. A "step matcher" is Callable[[StepInfo], bool]; a "playbook
# predicate" is Callable[[ParsedPlaybook], bool]. Helpers build both.
# --------------------------------------------------------------------------- #

StepMatcher = Callable[[StepInfo], bool]
PlaybookPredicate = Callable[[ParsedPlaybook], bool]


def step(
    *,
    step_type: str | None = None,
    connector: str | None = None,
    operation: str | None = None,
) -> StepMatcher:
    """A matcher for a SINGLE step -- all given facets must hold on the same step.

    ``step_type`` accepts a friendly alias (``set_variable``/``connector``/
    ``code_snippet``/...; see ``STEP_TYPE_NAMES``) or a raw engine name.
    ``connector``/``operation`` are matched case-insensitively as substrings of
    the step's ``arguments.connector``/``arguments.operation`` (so ``block_ip``
    matches ``block_ip`` and ``connector="fortigate"`` matches
    ``fortigate-firewall``).
    """
    raw_type = STEP_TYPE_NAMES.get(step_type.lower(), step_type) if step_type else None
    conn = connector.lower() if connector else None
    op = operation.lower() if operation else None

    def _match(s: StepInfo) -> bool:
        if raw_type is not None and s.step_type_raw != raw_type:
            return False
        if conn is not None and conn not in (s.connector or "").lower():
            return False
        if op is not None and op not in (s.operation or "").lower():
            return False
        return True

    return _match


def count(
    matcher: StepMatcher,
    *,
    n: int | None = None,
    min: int | None = None,
    max: int | None = None,
) -> PlaybookPredicate:
    """True when the number of steps satisfying ``matcher`` meets the bound.

    Pass ``n`` for an exact count, or ``min``/``max`` (inclusive) for a range.
    """
    if n is None and min is None and max is None:
        raise ValueError("count() needs one of n, min, max")

    def _pred(pb: ParsedPlaybook) -> bool:
        c = sum(1 for s in pb.steps if matcher(s))
        if n is not None and c != n:
            return False
        if min is not None and c < min:
            return False
        if max is not None and c > max:
            return False
        return True

    return _pred


def has(matcher: StepMatcher) -> PlaybookPredicate:
    """True when at least one step satisfies ``matcher`` (``count(min=1)``)."""
    return count(matcher, min=1)


def trigger(trigger_type: str) -> PlaybookPredicate:
    """True when the playbook's start step is ``trigger_type`` (friendly or raw)."""
    want = trigger_type.lower()

    def _pred(pb: ParsedPlaybook) -> bool:
        if pb.trigger_type is None:
            return False
        return pb.trigger_type.lower() == want or pb.trigger_type == trigger_type

    return _pred


def all_of(*preds: PlaybookPredicate) -> PlaybookPredicate:
    """Logical AND of playbook predicates."""
    return lambda pb: all(p(pb) for p in preds)


def any_of(*preds: PlaybookPredicate) -> PlaybookPredicate:
    """Logical OR of playbook predicates."""
    return lambda pb: any(p(pb) for p in preds)


def none_of(*preds: PlaybookPredicate) -> PlaybookPredicate:
    """Logical NOR -- true when none of ``preds`` hold."""
    return lambda pb: not any(p(pb) for p in preds)


def join_parent_child(
    corpus: list[ParsedPlaybook],
    parent_pred: PlaybookPredicate,
    child_pred: PlaybookPredicate,
) -> list[ParsedPlaybook]:
    """Parents satisfying ``parent_pred`` that reference a child satisfying ``child_pred``.

    Pure: ``corpus`` is the full parsed playbook set; reference steps are resolved
    to children by uuid within the corpus. A parent with no resolvable matching
    child is excluded.
    """
    by_uuid = {pb.uuid: pb for pb in corpus if pb.uuid}
    out = []
    for pb in corpus:
        if not parent_pred(pb):
            continue
        for child_uuid in pb.references():
            child = by_uuid.get(child_uuid)
            if child is not None and child_pred(child):
                out.append(pb)
                break
    return out
