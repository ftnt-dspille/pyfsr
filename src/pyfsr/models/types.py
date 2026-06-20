"""Semantic string NewTypes for FortiSOAR IRI fields.

Both types are transparent at runtime (zero cost) but give type-checkers and
IDEs distinct names so a function that expects a ``PicklistIRI`` won't silently
accept a module record IRI, and vice-versa.
"""

from typing import NewType

PicklistIRI = NewType("PicklistIRI", str)
"""IRI of a picklist item — e.g. ``/api/3/picklists/<uuid>``."""

RecordIRI = NewType("RecordIRI", str)
"""IRI of a module record — e.g. ``/api/3/<module>/<uuid>``."""
