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

    ``upload`` is runnable against the replay fixture too — it builds a minimal
    ``.tgz`` in a ``tempfile`` and posts it (see :meth:`WidgetsAPI.upload`'s doctest).
    ``deploy`` chains upload + publish + a settle-poll that needs the published
    widget to land in the live list — not exercised offline::

        record = client.widgets.deploy("my-widget-1.2.0.tgz")
        assert record.published
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..auth.api_key import APIKeyAuth
from ..exceptions import APIError, WidgetIncompatible, WidgetPublishError, WidgetUploadConflict
from ..models._widgets import WidgetRecord
from ..pagination import extract_members
from ..widget_dev import pack_widget, widget_compatibility_error, widget_info_from_tgz
from ._solutionpacks import upload_solutionpack
from .base import BaseAPI


@dataclass
class ExportedWidget:
    """One widget archive written by :meth:`WidgetsAPI.export_dashboard`."""

    name: str | None
    version: str | None
    path: str
    via: str
    """``"dashboard"`` when the dashboard places it, else the widget that loads it."""


@dataclass
class DashboardExport:
    """What :meth:`WidgetsAPI.export_dashboard` exported and what it could not."""

    widgets: list[ExportedWidget] = field(default_factory=list)
    connectors: dict[str, list[str]] = field(default_factory=dict)
    """Installed connector name -> the widgets that call it."""
    missing: list[str] = field(default_factory=list)
    """Widgets the dashboard places that the box does not have."""
    version_mismatch: list[str] = field(default_factory=list)


_UPLOAD_CONFLICT_MARKER = "already exists in widget workspace"


class WidgetsAPI(BaseAPI):
    """Widget listing, upload, and publish."""

    # ------------------------------------------------------------- discovery
    def list(self, *, installed: bool | None = None, name: str | None = None) -> list[WidgetRecord]:
        """List widget records via ``GET /api/3/widgets``.

        Args:
            installed: filter to ``installed==True``/``False`` client-side.
            name: filter to this widget name client-side.

        Returns one :class:`~pyfsr.models.WidgetRecord` per widget *version* on the box.
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
    def check_compatible(self, tgz: str | Path) -> None:
        """Raise :class:`~pyfsr.exceptions.WidgetIncompatible` if this box would refuse the widget.

        Same rule as the appliance's upload check (see
        :func:`pyfsr.widget_dev.widget_compatibility_error`). A box whose version
        cannot be read is not judged.
        """
        version_tuple = getattr(self.client, "version_tuple", None)
        vt = version_tuple() if callable(version_tuple) else None
        if vt is None:
            return
        platform = ".".join(map(str, vt))
        info = widget_info_from_tgz(tgz)
        compat = (info.get("metadata") or {}).get("compatibility")
        why = widget_compatibility_error(compat, platform)
        if why:
            name = info.get("name")
            raise WidgetIncompatible(
                f"widget {name!r} {info.get('version')}: {why}",
                name=name,
                compatibility=compat,
                platform_version=platform,
            )

    def upload(self, path: str, *, replace: bool = True) -> WidgetRecord:
        """Upload a widget ``.tgz`` (or pack a widget **folder** first) — step 1 of deploy.

        With a username/password (JWT) client, posts to the shared solution-pack
        installer (``POST /api/3/solutionpacks/install?$type=widget``). That
        lands the widget in the *development* workspace (``draft:true,
        installed:false``); it is **not live** until :meth:`publish`.

        With an **API-key** client, that route answers 401 (live-verified on a
        FortiSOC 7.6.6 tenant), so the upload goes through ``POST
        /api/3/widgets/import`` instead -- the route the Import Wizard uses for a
        bundled widget. It installs the widget **live** in one step and returns
        the installed record, so :meth:`deploy` skips the publish step.

        A **directory** path is packed first with :func:`pyfsr.widget_dev.pack_widget`
        (required-file check, junk/AppleDouble exclusion, ``<name>-<version>``
        layout), so ``upload``/``deploy`` take a widget folder or a ``.tgz``
        interchangeably.

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
            The created :class:`~pyfsr.models.WidgetRecord` (``draft=True, installed=False``).

        Raises:
            FileNotFoundError: if ``path`` doesn't exist.
            WidgetUploadConflict: that name+version is already staged and
                ``replace=False``.

        Example:
            >>> import io, tarfile, tempfile
            >>> from pathlib import Path
            >>> client = demo_client_jwt()  # username/password: the draft-upload path
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     tgz = Path(tmp) / "jinjaEditorWidget-1.1.3.tgz"
            ...     with tarfile.open(tgz, "w:gz") as tar:
            ...         info = tarfile.TarInfo("jinjaEditorWidget-1.1.3/info.json")
            ...         data = b'{"name": "jinjaEditorWidget", "version": "1.1.3"}'
            ...         info.size = len(data)
            ...         tar.addfile(info, io.BytesIO(data))
            ...     record = client.widgets.upload(str(tgz))
            >>> (record.name, record.version, record.draft, record.installed)
            ('jinjaEditorWidget', '1.1.3', True, False)
        """
        if Path(path).is_dir():
            path = pack_widget(path)
        self.check_compatible(path)
        if _is_api_key(self.client):
            return self._import_live(path, replace=replace)
        try:
            resp = upload_solutionpack(self.client, path, type_="widget", replace=replace)
        except APIError as exc:
            if _UPLOAD_CONFLICT_MARKER in (exc.message or ""):
                raise WidgetUploadConflict(exc.message) from exc
            raise
        return WidgetRecord.model_validate(resp)

    def _import_live(self, path: str | Path, *, replace: bool = True) -> WidgetRecord:
        """Install a widget ``.tgz`` through ``POST /api/3/widgets/import`` (API-key path).

        The Content-Hub installer route (``/api/3/solutionpacks/install``) answers
        an API key with 401 "An authentication exception occurred." on FortiSOC
        (live-verified on 7.6.6). ``/api/3/widgets/import`` is the route the
        Import Wizard itself calls for a bundled widget (``WidgetConfig::
        importFromFile``); it accepts an API key and installs the widget **live**
        in one step (``installed:true, draft:false``), so there is nothing to
        publish afterwards. Returns the installed record.
        """
        info = widget_info_from_tgz(path)
        name, version = str(info.get("name")), str(info.get("version"))
        with open(path, "rb") as fh:
            self.client.request(
                "POST",
                "/api/3/widgets/import",
                data={"replace": "true" if replace else "false"},
                files={"file": (Path(path).name, fh, "application/gzip")},
            )
        record = next((r for r in self.list(name=name) if r.version == version), None)
        if record is None:
            raise WidgetPublishError(host=self.client.base_url, name=name, version=version)
        return record

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
            The published :class:`~pyfsr.models.WidgetRecord`.
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
            path: filesystem path to the widget ``.tgz``, or to a widget
                folder (packed first -- see :meth:`upload`).
            replace: passed to both :meth:`upload` (``$replace``) and
                :meth:`publish` (``replace``) — see the plan doc for why
                these are independent flags that usually agree.
            wait: settle-poll after publish until the new version is live.
            interval: seconds between settle polls.
            timeout: give up waiting after this many seconds.

        Returns:
            The settled :class:`~pyfsr.models.WidgetRecord`.

        Raises:
            WidgetUploadConflict: upload collided with an existing staged version.
            WidgetPublishError: ``wait=True`` and the widget never settled live.
        """
        uploaded = self.upload(path, replace=replace)
        if uploaded.published:
            # API-key upload goes through /api/3/widgets/import, which installs live.
            return uploaded
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

    def ensure_deployed(
        self,
        path: str,
        *,
        replace: bool = True,
        wait: bool = True,
        interval: float = 3.0,
        timeout: float = 60.0,
    ) -> WidgetRecord:
        """Deploy a widget ``.tgz`` only if it isn't already live at the same version.

        Idempotent wrapper around :meth:`deploy`: reads the widget name and
        version from the ``.tgz`` (via a lightweight upload probe), and if a
        widget with the same name+version is already ``installed`` and
        ``published``, skips the upload/publish cycle and returns the existing
        record. Otherwise falls through to :meth:`deploy`.

        Re-running a deploy script that ships the same widget version is a
        no-op; a version bump deploys the new one.

        Args:
            path: filesystem path to the widget ``.tgz``, or to a widget
                folder (packed first -- see :meth:`upload`).
            replace: passed to :meth:`deploy` (and through to upload/publish).
            wait: settle-poll after publish.
            interval: seconds between settle polls.
            timeout: give up waiting after this many seconds.

        Returns:
            The settled :class:`~pyfsr.models.WidgetRecord` (existing or newly
            deployed).
        """
        import tarfile

        if Path(path).is_dir():
            # A folder: read name+version straight from its info.json, then
            # fall through to deploy (which packs before uploading).
            info = json.loads((Path(path) / "info.json").read_text(encoding="utf-8"))
            name, version = info.get("name"), info.get("version")
        else:
            # Peek the widget name + version from the .tgz manifest without uploading.
            name, version = _peek_widget_manifest(path, tarfile)
        if name is not None:
            existing = self.get(name)
            if existing is not None and existing.version == version and existing.installed and existing.published:
                return existing
        return self.deploy(path, replace=replace, wait=wait, interval=interval, timeout=timeout)

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

    def export_dashboard(
        self,
        dashboard: str | dict,
        dest_dir: str | Path,
        *,
        nested: bool = True,
    ) -> DashboardExport:
        """Export every widget a dashboard needs, nested widgets included.

        Reads the dashboard record (``GET /api/3/dashboard/<uuid>``, or pass the
        record itself), exports each widget it places, then scans each exported
        archive's code for widgets it loads at runtime (for example a widget that
        looks up ``killchainphases`` from the installed list and renders it
        inside itself) and exports those too, until nothing new turns up.
        Candidates read from code are kept only when the box has a widget by that
        name, so stray strings never become exports.

        Connector names the widgets pass to an execute helper are collected
        (checked against the installed connectors) but not exported; see
        ``connectors`` on the result.

        Args:
            dashboard: dashboard uuid, or a dashboard record dict.
            dest_dir: folder for the ``<name>-<version>.tgz`` files (created).
            nested: follow widgets found in widget code (default ``True``).

        Returns:
            a :class:`DashboardExport`.
        """
        from ..widget_dev import dashboard_widget_refs, widget_code_refs

        record = self.client.get(f"/api/3/dashboard/{dashboard}") if isinstance(dashboard, str) else dashboard
        out_dir = Path(dest_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        by_name: dict[str, list[WidgetRecord]] = {}
        for r in self.list():
            if r.name:
                by_name.setdefault(r.name, []).append(r)
        installed_connectors: set[str] | None = None

        result = DashboardExport()
        queue: list[tuple[str, str | None, str]] = [
            (n, v, "dashboard") for n, v in sorted(dashboard_widget_refs(record))
        ]
        seen: set[str] = set()
        while queue:
            name, version, via = queue.pop(0)
            candidates = by_name.get(name, [])
            exact = [r for r in candidates if version and r.version == version]
            live = [r for r in candidates if r.installed]
            pick = exact or live or candidates or [None]
            rec = max(pick, key=lambda r: _version_key(r.version)) if pick[0] is not None else None
            if rec is None or not rec.uuid:
                if via == "dashboard":
                    result.missing.append(f"{name}-{version}" if version else name)
                continue
            key = f"{rec.name}-{rec.version}"
            if key in seen:
                continue
            seen.add(key)
            if version and rec.version != version:
                result.version_mismatch.append(f"{name}: dashboard wants {version}, box has {rec.version}")
            dest = out_dir / f"{key}.tgz"
            self.export(rec.uuid, str(dest))
            result.widgets.append(ExportedWidget(name=rec.name, version=rec.version, path=str(dest), via=via))
            refs = widget_code_refs(dest)
            if refs["connectors"]:
                if installed_connectors is None:
                    installed_connectors = {c.name for c in self.client.connectors.list_configured() if c.name}
                for c in sorted(refs["connectors"] & installed_connectors):
                    result.connectors.setdefault(c, []).append(rec.name)
            if nested:
                wanted = {(n, None) for n in refs["widgets"]} | {(n, v) for n, v in refs["widget_types"]}
                for n, v in sorted(wanted, key=lambda t: (t[0], t[1] or "")):
                    if n in by_name:
                        queue.append((n, v, rec.name))
        return result

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


def _peek_widget_manifest(path: str, _tarfile) -> tuple[str | None, str | None]:
    """Extract ``(name, version)`` from a widget ``.tgz`` without uploading.

    Reads the ``info.json`` manifest inside the archive (the same file
    :meth:`upload` sends to the appliance). Returns ``(None, None)`` if the
    archive can't be read or the manifest is absent — the caller falls through
    to :meth:`deploy` in that case, preserving the original behavior.
    """
    import json

    try:
        with _tarfile.open(path, mode="r:gz") as tar:
            # Find info.json (it's at the archive root or under a subdir)
            info_member = next(
                (m for m in tar.getmembers() if m.name.endswith("info.json") and m.isfile()),
                None,
            )
            if info_member is None:
                return None, None
            f = tar.extractfile(info_member)
            if f is None:
                return None, None
            data = json.loads(f.read())
            return data.get("name"), data.get("version")
    except Exception:
        return None, None


def _is_api_key(client: object) -> bool:
    """True when ``client`` authenticates with an API key (no JWT)."""
    return isinstance(getattr(client, "auth", None), APIKeyAuth)
