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


def _is_str_annotation(ann: Any) -> bool:
    """True if ``ann`` is ``str`` or ``str | None`` (a plain string field)."""
    if ann is str:
        return True
    args = get_args(ann)
    if args:
        non_none = [a for a in args if a is not type(None)]
        return non_none == [str]
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
