"""The base for every typed FortiSOAR record model.

:class:`BaseRecord` is a Pydantic v2 model that stays *dict-compatible* on
purpose: FortiSOAR entities carry far more fields than any curated model
enumerates, so unknown keys are preserved (``extra="allow"``) and the model
supports ``rec["field"]`` / ``rec.get(...)`` / ``"field" in rec`` alongside
attribute access. That means code written against the old dict-returning API
keeps working while new code gets typed fields and IDE completion.

JSON-LD envelope keys (``@id`` / ``@type``) are exposed as the ``iri`` and
``record_type`` properties (Python can't have ``@``-prefixed attributes), but
remain reachable as ``rec["@id"]`` for round-tripping.
"""

from __future__ import annotations

from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _is_str_like(t: Any) -> bool:
    """True if ``t`` is ``str`` or a NewType whose supertype chain reaches ``str``."""
    if t is str:
        return True
    sup = getattr(t, "__supertype__", None)
    while sup is not None:
        if sup is str:
            return True
        sup = getattr(sup, "__supertype__", None)
    return False


def _is_str_annotation(ann: Any) -> bool:
    """True if ``ann`` is ``str | None`` (or a str-NewType variant)."""
    if _is_str_like(ann):
        return True
    args = get_args(ann)
    if args:
        non_none = [a for a in args if a is not type(None)]
        return len(non_none) == 1 and _is_str_like(non_none[0])
    return False


class BaseRecord(BaseModel):
    """Dict-compatible base for typed FortiSOAR records.

    Every concrete entity model (Alert, Incident, Task, Comment, ...) subclasses
    this. Modules without a registered model are parsed into a bare
    ``BaseRecord`` so callers still get IRI/uuid helpers and dict access.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id_iri: str | None = Field(default=None, alias="@id")
    record_type: str | None = Field(default=None, alias="@type")
    uuid: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _collapse_expanded_refs(cls, data: Any) -> Any:
        """Collapse expanded relationship objects back to their IRI string.

        FortiSOAR returns a single-relationship field (e.g. ``modifyUser``) as a
        bare IRI string normally, but as the full ``{"@id": ...}`` object when
        relationships are pulled. For fields the model types as ``str`` we
        replace such an object with its ``@id`` so the typed model never breaks;
        picklist fields typed ``Any`` keep their full expanded value.
        """
        if not isinstance(data, dict):
            return data
        result = dict(data)
        for name, info in cls.model_fields.items():
            if not _is_str_annotation(info.annotation):
                continue
            for key in (info.alias, name):
                value = result.get(key) if key else None
                if isinstance(value, dict) and "@id" in value:
                    result[key] = value["@id"]
        return result

    # -- IRI / identity helpers --------------------------------------------
    @property
    def iri(self) -> str | None:
        """The record's ``@id`` IRI (e.g. ``/api/3/alerts/<uuid>``)."""
        return self.id_iri

    def picklist_uuid(self, field: str) -> str | None:
        """Return the trailing uuid of a picklist/relationship IRI field.

        Picklist and single-relationship fields hold an IRI like
        ``/api/3/picklists/<uuid>``; this pulls out the ``<uuid>`` tail. Returns
        ``None`` when the field is absent or not a string IRI.
        """
        value = self.get(field)
        if isinstance(value, str) and "/" in value:
            return value.rsplit("/", 1)[-1]
        return None

    # -- relationship accessors --------------------------------------------
    def as_record(self, field: str, model: type[BaseModel]) -> Any:
        """Coerce relationship ``field`` into ``model``, whether expanded or an IRI.

        A single-relationship field comes back either as a bare IRI string (not
        expanded) or as the full nested object (relationships pulled). This
        normalizes both into a ``model`` instance — an IRI string yields a thin
        instance carrying only ``@id`` (so ``.iri`` works) — and returns ``None``
        when the field is absent/null.
        """
        value = self.get(field)
        if value is None:
            return None
        if isinstance(value, model):
            return value
        if isinstance(value, str):
            return model.model_validate({"@id": value})
        if isinstance(value, dict):
            return model.model_validate(value)
        return None

    def _as_actor(self, field: str, user_model: type[BaseModel], appliance_model: type[BaseModel]) -> Any:
        """Coerce an actor field (createUser/modifyUser) to User or Appliance by ``@type``."""
        value = self.get(field)
        if value is None:
            return None
        atype = None
        if isinstance(value, dict):
            atype = value.get("@type")
        elif hasattr(value, "record_type"):
            atype = value.record_type
        model = appliance_model if atype == "Appliance" else user_model
        return self.as_record(field, model)

    def _as_records(self, field: str, model: type[BaseModel]) -> list[Any]:
        """List variant of :meth:`as_record` for to-many relationships."""
        value = self.get(field)
        if not isinstance(value, list):
            return []
        out = []
        for item in value:
            if isinstance(item, model):
                out.append(item)
            elif isinstance(item, str):
                out.append(model.model_validate({"@id": item}))
            elif isinstance(item, dict):
                out.append(model.model_validate(item))
        return out

    @property
    def create_user(self) -> Any:
        """The ``createUser`` as a :class:`~pyfsr.models.User` or :class:`~pyfsr.models.Appliance`.

        Dispatches on ``@type``: ``"Appliance"`` records (playbook-engine actors)
        return an :class:`~pyfsr.models.Appliance`; everything else returns a
        :class:`~pyfsr.models.User`. Both share ``BaseRecord`` so ``.iri`` and
        ``.uuid`` always work.
        """
        from ._system import Appliance, User

        return self._as_actor("createUser", User, Appliance)

    @property
    def modify_user(self) -> Any:
        """The ``modifyUser`` as a :class:`~pyfsr.models.User` or :class:`~pyfsr.models.Appliance`.

        See :attr:`create_user` for dispatch logic.
        """
        from ._system import Appliance, User

        return self._as_actor("modifyUser", User, Appliance)

    @property
    def assigned_to(self) -> Any:
        """The assignee as a :class:`~pyfsr.models.User`, or ``None``.

        Reads ``assignedTo`` (alerts/incidents) and falls back to
        ``assignedToPerson`` (tasks).
        """
        from ._system import User

        if self.get("assignedTo") is not None:
            return self.as_record("assignedTo", User)
        return self.as_record("assignedToPerson", User)

    @property
    def owner_teams(self) -> list[Any]:
        """The ``owners`` relationship as a list of :class:`~pyfsr.models.Team`."""
        from ._system import Team

        return self._as_records("owners", Team)

    # -- dict-compatibility shims ------------------------------------------
    def _lookup_attr(self, key: str) -> tuple[bool, Any]:
        """Resolve ``key`` against field names, aliases, then extras."""
        for name, info in type(self).model_fields.items():
            if key == name or key == info.alias:
                return True, getattr(self, name)
        extra = self.__pydantic_extra__ or {}
        if key in extra:
            return True, extra[key]
        return False, None

    def __getitem__(self, key: str) -> Any:
        found, value = self._lookup_attr(key)
        if not found:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        found, _ = self._lookup_attr(key)
        return found

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style accessor: value for ``key`` (by name or ``@``-alias) or default."""
        found, value = self._lookup_attr(key)
        return value if found else default

    def to_dict(self, *, by_alias: bool = True, exclude_none: bool = False) -> dict[str, Any]:
        """Serialize back to a plain FortiSOAR-shaped dict.

        Defaults to ``by_alias=True`` so ``@id``/``@type`` round-trip with their
        wire names.
        """
        return self.model_dump(by_alias=by_alias, exclude_none=exclude_none)
