"""Module-specific API shortcuts reached from the client.

Each ``client.<module>`` attribute (``client.alerts``, ``client.incidents``,
``client.tasks``, ``client.users``, ``client.content_hub``, …) is a small typed
wrapper over the generic record layer, adding module-aware conveniences such as
picklist-name resolution and parent-record linking. They all share
:class:`~pyfsr.api.base.BaseAPI`. For generic CRUD over any module, use
:class:`~pyfsr.records.RecordSet` via ``client.records(<module>)`` instead.
"""
