"""Widget upload + publish — ``client.widgets``.

Mirrors :class:`~pyfsr.api.connectors.ConnectorsAPI` (``install_from_file``)
but adds the widget-specific **publish** step connectors don't need. A widget
upload alone lands in the *development* workspace (``draft:true,
installed:false``) — it is not live until published.

REST contract, wire shapes, and the two independent ``replace`` flags are
live-verified on FortiSOAR 8.0; see
``docs/plans/WIDGET_UPLOAD_PUBLISH_PLAN.md`` for the full write-up and
provenance.

.. warning::
    Live-verified appliance bug on 8.0.0-6034: uploading a **second, different
    version** of a widget name that already exists on the box (draft or
    published) reliably 500s appliance-side. The *first-ever* upload of a name
    always works, and re-uploading the exact same already-seen version always
    works — only a genuine version bump on an existing name is affected. See
    the plan doc's "Known appliance bug" section before relying on
    :meth:`WidgetsAPI.deploy` to ship a new version of an already-installed
    widget; confirm on your target build first.

Example:
    >>> client = demo_client()
    >>> [w.name for w in client.widgets.list()]
    ['mobileSettings', 'recordSummary', 'accessControl']
    >>> client.widgets.get("accessControl").version
    '2.1.0'

    ``publish`` needs only a uuid (no file), so it replays from the fixture too —
    this is the real response from publishing a genuine widget package live::

        >>> record = client.widgets.publish("5fef77ad-8917-40c6-82a2-fdd753bdf41c")
        >>> record.draft, record.installed, record.published
        (False, True, True)

    ``upload``/``deploy`` need a real ``.tgz`` on disk plus a live appliance::

        record = client.widgets.deploy("my-widget-1.2.0.tgz")
        assert record.published
"""

from __future__ import annotations

import time
from pathlib import Path

from ..exceptions import APIError, WidgetPublishError, WidgetUploadConflict
from ..models._widgets import WidgetRecord
from ..pagination import extract_members
from ._solutionpacks import upload_solutionpack
from .base import BaseAPI

_UPLOAD_CONFLICT_MARKER = "already exists in widget workspace"


class WidgetsAPI(BaseAPI):
    """Widget listing, upload, and publish."""

    # ------------------------------------------------------------- discovery
    def list(self, *, installed: bool | None = None, name: str | None = None) -> list[WidgetRecord]:
        """List widget records via ``GET /api/3/widgets``.

        Args:
            installed: filter to ``installed==True``/``False`` client-side.
            name: filter to this widget name client-side.

        Returns one :class:`WidgetRecord` per widget *version* on the box.
        """
        resp = self.client.get("/api/3/widgets")
        records = [WidgetRecord.model_validate(m) for m in extract_members(resp)]
        if name is not None:
            records = [r for r in records if r.name == name]
        if installed is not None:
            records = [r for r in records if bool(r.installed) == installed]
        return records

    def get(self, name: str) -> WidgetRecord | None:
        """The newest record for widget ``name`` (highest ``version``), or ``None``."""
        records = self.list(name=name)
        if not records:
            return None
        return max(records, key=lambda r: _version_key(r.version))

    # ---------------------------------------------------------------- upload
    def upload(self, path: str, *, replace: bool = True) -> WidgetRecord:
        """Upload a widget ``.tgz`` — step 1 of deploy.

        Posts to the shared solution-pack installer (``POST
        /api/3/solutionpacks/install?$type=widget``) — there is no dedicated
        ``widgets/import`` endpoint. Lands the widget in the *development*
        workspace (``draft:true, installed:false``); it is **not live** until
        :meth:`publish`.

        Args:
            path: filesystem path to the widget ``.tgz``.
            replace: overwrite an already-staged copy of this exact
                name+version (``$replace=true``). ``False`` + an existing
                name+version raises :class:`~pyfsr.exceptions.WidgetUploadConflict`.

                **Live-verified effect of ``replace=True``:** it does not just
                clear a *staging* collision — re-uploading an exact name+version
                that is currently **installed and published** replaces that
                installed record too, resetting it to a fresh
                ``draft=True, installed=False`` (a new uuid; the old uuid's
                record is gone). In other words, ``upload(replace=True)`` on a
                live widget immediately un-publishes it until :meth:`publish`
                runs again — this is exactly why :meth:`deploy` always pairs the
                two calls rather than leaving a caller to run them separately.

        Returns:
            The created :class:`WidgetRecord` (``draft=True, installed=False``).

        Raises:
            FileNotFoundError: if ``path`` doesn't exist.
            WidgetUploadConflict: that name+version is already staged and
                ``replace=False``.
        """
        try:
            resp = upload_solutionpack(self.client, path, type_="widget", replace=replace)
        except APIError as exc:
            if _UPLOAD_CONFLICT_MARKER in (exc.message or ""):
                raise WidgetUploadConflict(exc.message) from exc
            raise
        return WidgetRecord.model_validate(resp)

    # --------------------------------------------------------------- publish
    def publish(self, uuid: str, *, replace: bool = True, go_live: bool = True) -> WidgetRecord:
        """Publish an uploaded widget — step 2 of deploy.

        Loads the development-workspace manifest (``GET
        /api/3/widgets/development/<uuid>``) and ``PUT``\\ s it back to
        ``/api/3/widgets/<uuid>`` minus ``tree``, plus the publish flags. This
        is exactly what the Content-Hub UI's *Publish* button sends — using
        local ``info.json`` instead of the box's own development record was
        observed to make the publish not stick.

        Args:
            uuid: the widget's uuid (from :meth:`upload` / :meth:`list`).
            replace: supersede whatever version of this widget is currently
                installed (``replace`` body field, paired with
                ``replaceVersions``). Independent of upload's ``$replace`` —
                see the plan doc's "two replace flags" table.

                **Live-verified:** ``True`` and ``False`` produced an
                *identical* observable outcome in every case tested — a single
                fresh ``draft=False, installed=True`` record, no stacked
                versions. That's expected: by the time ``publish`` runs, the
                prior installed record for this name+version has typically
                already been collapsed by :meth:`upload`'s own ``replace``
                (see its docstring), so there was nothing left for this flag to
                visibly supersede in testing. It's kept ``True`` by default to
                match "replace existing version" (the UI's implied intent) and
                because the wire shape is what the real Publish button sends —
                but its effect when two *different* versions of a name
                genuinely coexist is unconfirmed (that scenario is currently
                blocked by a live-verified appliance bug on version bumps; see
                the module docstring's warning).
            go_live: ``True`` (default) publishes for real (``draft=False``).
                ``False`` publishes-as-draft (rarely wanted).

        Returns:
            The published :class:`WidgetRecord`.
        """
        dev_resp = self.client.get(f"/api/3/widgets/development/{uuid}")
        manifest = extract_members(dev_resp)
        manifest = dict(manifest[0]) if manifest else dict(dev_resp) if isinstance(dev_resp, dict) else {}
        manifest.pop("tree", None)
        manifest.update(
            {
                "@id": f"/api/3/widgets/{uuid}",
                "draft": not go_live,
                "installed": True,
                "enablePublish": False,
                "replace": replace,
                "replaceVersions": [],
                "publishedDate": int(time.time()),
            }
        )
        resp = self.client.put(f"/api/3/widgets/{uuid}", data=manifest)
        return WidgetRecord.model_validate(resp if isinstance(resp, dict) else {})

    # ---------------------------------------------------------------- deploy
    def deploy(
        self,
        path: str,
        *,
        replace: bool = True,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 60.0,
    ) -> WidgetRecord:
        """Upload then publish a widget ``.tgz`` in one call — the common path.

        Unlike a connector install, a widget upload creates no import job to
        poll — ``wait`` instead re-reads :meth:`list` until the published
        version settles ``draft=False, installed=True`` (a cheap insurance
        pass against a delayed reconciler revert).

        Args:
            path: filesystem path to the widget ``.tgz``.
            replace: passed to both :meth:`upload` (``$replace``) and
                :meth:`publish` (``replace``) — see the plan doc for why
                these are independent flags that usually agree.
            wait: settle-poll after publish until the new version is live.
            interval: seconds between settle polls.
            timeout: give up waiting after this many seconds.

        Returns:
            The settled :class:`WidgetRecord`.

        Raises:
            WidgetUploadConflict: upload collided with an existing staged version.
            WidgetPublishError: ``wait=True`` and the widget never settled live.
        """
        uploaded = self.upload(path, replace=replace)
        published = self.publish(uploaded.uuid, replace=replace, go_live=True)
        if not wait:
            return published

        def _settled() -> WidgetRecord | None:
            record = self.get(published.name)
            if record and record.version == published.version and record.published:
                return record
            return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _settled():
                # One extra read as cheap insurance against a delayed reconciler revert.
                time.sleep(min(interval, max(deadline - time.monotonic(), 0)))
                record = _settled()
                if record:
                    return record
                continue
            time.sleep(interval)
        raise WidgetPublishError(
            host=self.client.base_url,
            name=published.name,
            version=published.version,
        )

    # ---------------------------------------------------------------- export
    def export(self, uuid: str, dest: str, *, development: bool = False) -> str:
        """Export a widget ``.tgz`` (mirrors the connector export).

        ``POST /api/3/widgets/export/<uuid>`` with body ``{"development": bool}``,
        response is the raw archive bytes.

        Args:
            uuid: the widget's uuid.
            dest: filesystem path to write the ``.tgz`` to.
            development: export the *development*-workspace copy instead of
                the installed/published one.

        Returns:
            ``dest``.
        """
        response = self.client.request(
            "POST",
            f"/api/3/widgets/export/{uuid}",
            data={"development": development},
            headers={"Accept": "application/octet-stream"},
        )
        dest_path = Path(dest)
        dest_path.write_bytes(response.content)
        return str(dest_path)

    # ---------------------------------------------------------------- remove
    def remove(self, uuid: str) -> None:
        """Delete a widget record — ``DELETE /api/3/delete/widgets`` with ``{"ids": [uuid]}``."""
        self.client.request("DELETE", "/api/3/delete/widgets", data={"ids": [uuid]})


def _version_key(version: str | None) -> tuple:
    """Sort key for a dotted version string — numeric-aware, tolerant of non-numeric parts."""
    if not version:
        return ()
    parts = []
    for p in version.split("."):
        parts.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(parts)
