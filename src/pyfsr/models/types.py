"""Semantic string NewTypes for FortiSOAR IRI fields.

Both types are transparent at runtime (zero cost) but give type-checkers and
IDEs distinct names so a function that expects a ``PicklistIRI`` won't silently
accept a module record IRI, and vice-versa.
"""

from typing import NewType

PicklistIRI = NewType("PicklistIRI", str)
"""An IRI pointing to a picklist item: ``/api/3/picklists/<uuid>``."""

RecordIRI = NewType("RecordIRI", str)
"""An IRI pointing to a module record: ``/api/3/<module>/<uuid>``."""
