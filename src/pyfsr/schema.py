"""Live module schema: one cache, one pre-write check.

``client.schema`` fetches a module's **published** metadata the first time a write
touches it -- one module per call (``GET /api/3/model_metadatas?type=<module>``,
~0.2 MB) instead of every module (~1.3 MB, several seconds) -- and keeps it:

- **in memory** for the client's lifetime, so a loop of creates fetches once;
- **on disk** under ``~/.cache/pyfsr/schema/<instance>/`` for
  ``PYFSR_SCHEMA_TTL`` seconds (default 600; ``0`` turns the disk layer off;
  ``PYFSR_SCHEMA_CACHE_DIR`` moves it), so
  separate processes (CLI runs, workers) share it. One directory per instance, so
  boxes never mix.

The cache is dropped when it can no longer be trusted:

- a module publish through pyfsr clears the instance's schema (publish is
  appliance-wide);
- **switching instance** (the first client for a different box than the last one
  used) clears that box's disk cache and re-warms the modules it had cached;
- a check that fails against a *cached* schema on an unknown field or picklist
  value refetches the module once and checks again, so a field added in the UI
  costs one extra fetch, not a false refusal;
- ``client.schema.clear()`` / ``pyfsr schema clear`` by hand.

:meth:`SchemaCache.check` reports every problem in a payload at once, before
anything is sent: unknown fields, ``owners`` on a module that is not team-ownable
(FortiSOAR answers 200 and drops it), missing required fields on create, values
over the field's max length, read-only fields, picklist values not in the list,
relationship IRIs that point at the wrong module, and the value rules in
:mod:`pyfsr.field_values`.

:meth:`ModuleSchema.record_model` builds a pydantic class for the module as it
exists on *this* box (``pydantic.create_model``), for callers that want typed
validation of their own.
"""

from __future__ import annotations

import builtins
import contextlib
import difflib
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from .exceptions import ValidationError
from .field_values import SYSTEM_KEYS, check_value
from .pagination import extract_members

logger = logging.getLogger("pyfsr")

_META = "/api/3/model_metadatas"
# Above this many modules, one full fetch beats N single-module fetches (~2.3s vs ~0.3s each).
_BATCH_THRESHOLD = 6
# FortiSOAR stamps this "no limit" sentinel on most text fields.
_MAXLENGTH_UNSET = 10485761
DEFAULT_TTL = 600.0
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]{1,128}$")
_IRI = re.compile(r"^/api/3/([A-Za-z0-9_]+)/[0-9a-fA-F-]{36}$")
_RELATION_FORMS = frozenset({"lookup", "manyToMany", "oneToMany", "manyToOne", "oneToOne"})
_PICKLIST_FORMS = frozenset({"picklist", "multiselectpicklist"})

Op = Literal["create", "update"]


# --------------------------------------------------------------------------- models
class FieldSchema(BaseModel):
    """One attribute of a module, as the box defines it."""

    model_config = ConfigDict(extra="ignore")

    name: str
    type: str | None = None  # storage type: string, integer, picklists, <module> for relations
    form_type: str | None = None  # display type: text, datetime, picklist, lookup, manyToMany ...
    title: str | None = None
    required: bool = False
    max_length: int | None = None
    unique: bool = False
    writeable: bool = True
    encrypted: bool = False
    collection: bool = False
    default: Any = None
    picklist: str | None = None  # picklist listName for picklist fields
    target: str | None = None  # related module for relationship fields

    @property
    def is_relation(self) -> bool:
        return (self.form_type or "") in _RELATION_FORMS

    @property
    def is_picklist(self) -> bool:
        return (self.form_type or "").lower() in _PICKLIST_FORMS or self.type == "picklists"


class ModuleSchema(BaseModel):
    """A published module's flags and fields."""

    model_config = ConfigDict(extra="ignore")

    type: str
    module: str | None = None  # collection name; differs from type on some boxes
    label: str | None = None
    ownable: bool = False
    user_ownable: bool = False
    queueable: bool = False
    taggable: bool = False
    writable: bool = True
    unique_constraints: list[list[str]] = Field(default_factory=list)
    fields: dict[str, FieldSchema] = Field(default_factory=dict)
    fetched_at: float = 0.0

    @classmethod
    def from_metadata(cls, meta: dict[str, Any]) -> ModuleSchema:
        """Build from one ``model_metadatas`` member (``$relationships=true``)."""
        fields: dict[str, FieldSchema] = {}
        for a in meta.get("attributes") or []:
            if isinstance(a, dict) and a.get("name"):
                f = _field_from_attr(a)
                fields[f.name] = f
        uniques: list[list[str]] = []
        for c in meta.get("uniqueConstraint") or []:
            if isinstance(c, dict):
                for spec in c.values():
                    cols = (spec or {}).get("columns") if isinstance(spec, dict) else None
                    if cols:
                        uniques.append([str(x) for x in cols])
        desc = meta.get("descriptions") or {}
        return cls(
            type=str(meta.get("type") or ""),
            module=meta.get("module"),
            label=desc.get("singular") if isinstance(desc, dict) else None,
            ownable=bool(meta.get("ownable")),
            user_ownable=bool(meta.get("userOwnable")),
            queueable=bool(meta.get("queueable")),
            taggable=bool(meta.get("taggable")),
            writable=meta.get("writable") is not False,
            unique_constraints=uniques,
            fields=fields,
            fetched_at=time.time(),
        )

    def record_model(self, *, op: Op = "update") -> builtins.type[BaseModel]:
        """A pydantic model of this module as it exists on this box.

        ``op="create"`` makes required fields required; ``"update"`` (default)
        makes every field optional, for partial updates. Unknown keys are
        forbidden, so a misspelt field fails instead of being dropped by the box.
        System keys (``uuid``, ``owners`` ...) are accepted.
        """
        defs: dict[str, Any] = {}
        for f in self.fields.values():
            if not f.name.isidentifier() or f.name.startswith("_"):
                continue
            py = _py_type(f)
            kw: dict[str, Any] = {}
            if f.max_length and py is str:
                kw["max_length"] = f.max_length
            if op == "create" and f.required and f.default in (None, ""):
                defs[f.name] = (py, Field(..., **kw))
            else:
                defs[f.name] = (py | None, Field(None, **kw))
        for k in SYSTEM_KEYS:
            defs.setdefault(k, (Any, None))
        name = "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]", self.type) if p) or "Record"
        return create_model(
            f"{name}{op.capitalize()}",
            __config__=ConfigDict(extra="forbid", populate_by_name=True),
            **defs,
        )


def _field_from_attr(a: dict[str, Any]) -> FieldSchema:
    from .api.picklists import _picklist_name_from_attr

    v = a.get("validation") if isinstance(a.get("validation"), dict) else {}
    maxlen = v.get("maxlength") if v else None
    form = a.get("formType")
    if (
        not isinstance(maxlen, int)
        or maxlen <= 0
        or maxlen >= _MAXLENGTH_UNSET
        or form
        not in (
            "text",
            "textarea",
            "email",
            "url",
            "phone",
            "ipv4",
            "ipv6",
            "domain",
            "filehash",
        )
    ):
        maxlen = None
    ds = a.get("dataSource") if isinstance(a.get("dataSource"), dict) else {}
    target = ds.get("model") if form in _RELATION_FORMS and ds else None
    desc = a.get("descriptions") or {}
    return FieldSchema(
        name=a["name"],
        type=a.get("type"),
        form_type=form,
        title=(desc.get("singular") if isinstance(desc, dict) else None) or a.get("title"),
        required=bool(v and v.get("required") is True),
        max_length=maxlen,
        unique=bool(a.get("unique")),
        writeable=a.get("writeable") is not False,
        encrypted=bool(a.get("encrypted")),
        collection=bool(a.get("collection")),
        default=a.get("defaultValue"),
        picklist=_picklist_name_from_attr(a),
        target=target,
    )


def _py_type(f: FieldSchema) -> Any:
    form = (f.form_type or "").lower()
    if f.is_relation:
        return list[Any] if f.collection else Any
    if f.is_picklist:
        return list[Any] if form == "multiselectpicklist" else Any
    if form in ("datetime", "date"):
        return int | float
    if form == "checkbox" or f.type == "boolean":
        return bool
    if form == "integer" or f.type == "integer":
        return int
    if form == "decimal" or f.type in ("decimal", "float"):
        return float
    if form in ("json", "object") or f.type in ("object", "json", "array"):
        return Any
    return str


# --------------------------------------------------------------------------- results
class Problem(BaseModel):
    """One reason a payload would be refused, dropped or stored wrongly."""

    field: str
    code: str  # unknown_field, not_ownable, required, too_long, read_only, picklist, relation, value
    message: str


class CheckResult(BaseModel):
    """What :meth:`SchemaCache.check` found. ``data`` is the payload to send."""

    module: str
    op: Op
    data: dict[str, Any]
    problems: list[Problem] = Field(default_factory=list)
    conversions: list[str] = Field(default_factory=list)
    refetched: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems

    def raise_for_problems(self) -> None:
        if self.problems:
            raise ValidationError(
                f"refused before writing for {self.module}: " + "; ".join(p.message for p in self.problems)
            )


class OwnershipNotSupported(ValidationError):
    """``owners`` sent to a module that is not team-ownable (FortiSOAR drops it with a 200)."""


# --------------------------------------------------------------------------- disk cache
def _cache_root() -> Path:
    override = os.environ.get("PYFSR_SCHEMA_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "pyfsr" / "schema"


def instance_key(base_url: str) -> str:
    """Short, stable directory name for one box (hash of its base URL)."""
    return hashlib.sha1(base_url.rstrip("/").encode()).hexdigest()[:12]


def _ttl() -> float:
    raw = os.environ.get("PYFSR_SCHEMA_TTL") or ""
    try:
        return float(raw) if raw else DEFAULT_TTL
    except ValueError:
        return DEFAULT_TTL


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def clear_disk(base_url: str | None = None) -> int:
    """Delete cached schema files for one instance (or every instance). Returns files removed."""
    root = _cache_root()
    dirs = (
        [root / instance_key(base_url)]
        if base_url
        else ([p for p in root.iterdir() if p.is_dir()] if root.exists() else [])
    )
    n = 0
    for d in dirs:
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            if p.name == "manifest.json":
                continue
            with contextlib.suppress(OSError):
                p.unlink()
                n += 1
    return n


# --------------------------------------------------------------------------- cache
class SchemaCache:
    """Per-client schema cache (memory + per-instance disk). Accessed as ``client.schema``."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._mem: dict[str, ModuleSchema] = {}
        self._missing: set[str] = set()
        self._switch_checked = False
        #: Network fetches made (for tests and ``pyfsr schema`` output).
        self.fetches = 0

    # ---- paths
    @property
    def _base_url(self) -> str:
        return str(getattr(self.client, "base_url", "") or "")

    @property
    def _dir(self) -> Path:
        return _cache_root() / instance_key(self._base_url)

    def _disk_enabled(self) -> bool:
        return bool(self._base_url) and _ttl() > 0

    def _manifest(self) -> dict[str, Any]:
        try:
            return json.loads((self._dir / "manifest.json").read_text())
        except (OSError, ValueError):
            return {}

    def _write_manifest(self, modules: list[str]) -> None:
        with contextlib.suppress(OSError):
            _atomic_write(
                self._dir / "manifest.json",
                json.dumps({"base_url": self._base_url, "modules": sorted(set(modules)), "updated": time.time()}),
            )

    # ---- instance switch
    def _check_switch(self) -> None:
        """First use per client: if the last box used was a different one, refresh this one."""
        if self._switch_checked or not self._disk_enabled():
            return
        self._switch_checked = True
        marker = _cache_root() / "active"
        mine = instance_key(self._base_url)
        try:
            last = marker.read_text().strip()
        except OSError:
            last = ""
        if last == mine:
            return
        with contextlib.suppress(OSError):
            _atomic_write(marker, mine)
        if not last:
            return
        previously = self._manifest().get("modules") or []
        removed = clear_disk(self._base_url)
        logger.info("pyfsr schema: switched instance to %s; cleared %d cached modules", self._base_url, removed)
        if previously:
            try:
                self.warm(previously, refresh=True)
            except Exception as exc:  # noqa: BLE001 - re-warm is best effort; writes fetch lazily
                logger.warning("pyfsr schema: re-warm after instance switch failed: %s", exc)

    # ---- load
    def _cache_file(self, module: str) -> Path | None:
        """The disk path for ``module``, or ``None`` when the name isn't a plain identifier.

        Module names come from callers and from the server; anything that could
        leave the cache directory (``../x``, separators) is never used as a path.
        """
        if not _SAFE_NAME.match(module):
            return None
        p = self._dir / f"{module}.json"
        return p if p.resolve().parent == self._dir.resolve() else None

    def _from_disk(self, module: str) -> ModuleSchema | None:
        if not self._disk_enabled():
            return None
        p = self._cache_file(module)
        if p is None:
            return None
        try:
            if time.time() - p.stat().st_mtime > _ttl():
                return None
            return ModuleSchema.model_validate_json(p.read_text())
        except (OSError, ValueError):
            return None

    def _store(self, schema: ModuleSchema, *aliases: str) -> None:
        for key in {schema.type, schema.module or schema.type, *aliases}:
            self._mem[key.lower()] = schema
        if self._disk_enabled():
            with contextlib.suppress(OSError):
                p = self._cache_file(schema.type)
                if p is not None:
                    _atomic_write(p, schema.model_dump_json())
                    self._write_manifest([*(self._manifest().get("modules") or []), schema.type])

    def _fetch_one(self, module: str) -> ModuleSchema | None:
        want = module.strip().lower()
        for key in ("type", "module"):
            self.fetches += 1
            data = self.client.get(_META, params={key: module, "$relationships": "true", "$limit": 5})
            hit = _pick(extract_members(data), want)
            if hit is not None:
                return ModuleSchema.from_metadata(hit)
        return None

    def get(self, module: str, *, refresh: bool = False) -> ModuleSchema | None:
        """The module's published schema, or ``None`` if the box has no such module."""
        self._check_switch()
        want = module.strip().lower()
        if not refresh:
            if want in self._mem:
                return self._mem[want]
            if want in self._missing:
                return None
            disk = self._from_disk(want)
            if disk is not None:
                self._store(disk, want)
                return disk
        schema = self._fetch_one(module)
        if schema is None:
            self._missing.add(want)
            return None
        self._missing.discard(want)
        self._store(schema, want)
        return schema

    def warm(self, modules: list[str] | tuple[str, ...], *, refresh: bool = False) -> dict[str, bool]:
        """Load several modules up front. Picks one full fetch when that is cheaper.

        Returns ``{module: found}``.
        """
        self._check_switch()
        todo: list[str] = []
        for m in dict.fromkeys(modules):
            want = m.lower()
            if not refresh:
                if want in self._mem:
                    continue
                disk = self._from_disk(want)
                if disk is not None:
                    self._store(disk, want)
                    continue
            todo.append(m)
        if len(todo) >= _BATCH_THRESHOLD:
            self.fetches += 1
            data = self.client.get(_META, params={"$relationships": "true", "$limit": 2147483647})
            members = extract_members(data)
            for m in todo:
                hit = _pick(members, m.lower())
                if hit is not None:
                    self._store(ModuleSchema.from_metadata(hit), m.lower())
                else:
                    self._missing.add(m.lower())
        else:
            for m in todo:
                self.get(m, refresh=refresh)
        return {m: m.lower() in self._mem for m in modules}

    def clear(self, *, disk: bool = True) -> int:
        """Drop this instance's cached schemas (memory, and disk unless ``disk=False``)."""
        self._mem.clear()
        self._missing.clear()
        return clear_disk(self._base_url) if disk and self._base_url else 0

    def cached_modules(self) -> list[str]:
        """Module types currently cached on disk for this instance (fresh or not)."""
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json") if p.name != "manifest.json")

    def is_cached(self, module: str) -> bool:
        want = module.strip().lower()
        return want in self._mem or self._from_disk(want) is not None

    # ---- check
    def check(self, module: str, data: dict[str, Any], *, op: Op = "create") -> CheckResult:
        """Check a payload against the module's live schema. Never sends anything.

        Returns a :class:`CheckResult` with every problem found and the payload
        to send (values converted where the meaning is clear, e.g. ISO date to
        epoch seconds). Call :meth:`CheckResult.raise_for_problems` to refuse.
        If the module isn't on the box, the result has no problems and the data
        unchanged (the server will answer for itself).
        """
        cached = self.is_cached(module)
        schema = self.get(module)
        if schema is None:
            return CheckResult(module=module, op=op, data=dict(data))
        result = self._check(schema, module, data, op)
        stale_codes = {"unknown_field", "picklist"}
        if cached and any(p.code in stale_codes for p in result.problems):
            fresh = self.get(module, refresh=True)
            if fresh is not None:
                result = self._check(fresh, module, data, op)
                result.refetched = True
        return result

    def _check(self, s: ModuleSchema, module: str, data: dict[str, Any], op: Op) -> CheckResult:
        out = dict(data)
        problems: list[Problem] = []
        notes: list[str] = []
        names = set(s.fields)

        if "owners" in data and not s.ownable:
            problems.append(
                Problem(
                    field="owners",
                    code="not_ownable",
                    message=(
                        f"'owners': module '{s.type}' is not team-ownable, so FortiSOAR would answer 200 and drop it "
                        "(this is not a permission problem). Turn on ownership for the module in Module Editor "
                        "and publish, or leave 'owners' out"
                    ),
                )
            )

        for key, value in data.items():
            if key.startswith(("@", "__")) or key in SYSTEM_KEYS:
                continue
            f = s.fields.get(key)
            if f is None:
                close = difflib.get_close_matches(key, names, n=1)
                hint = f" (did you mean {close[0]!r}?)" if close else ""
                problems.append(
                    Problem(
                        field=key,
                        code="unknown_field",
                        message=f"{key!r} is not a field{hint} -- FortiSOAR would drop it silently",
                    )
                )
                continue
            if not f.writeable:
                problems.append(Problem(field=key, code="read_only", message=f"{key!r} is read-only"))
                continue
            if value is None:
                continue
            if f.max_length and isinstance(value, str) and len(value) > f.max_length:
                problems.append(
                    Problem(
                        field=key,
                        code="too_long",
                        message=f"{key!r} is {len(value)} characters; the field allows {f.max_length}",
                    )
                )
            if f.is_picklist and f.picklist:
                bad = self._picklist_problem(f, value)
                if bad:
                    problems.append(Problem(field=key, code="picklist", message=bad))
            if f.is_relation and f.target:
                bad = _relation_problem(f, value)
                if bad:
                    problems.append(Problem(field=key, code="relation", message=bad))
            if f.form_type and not f.is_relation and not f.is_picklist:
                sent, note, problem = check_value(f.form_type, value)
                if problem:
                    problems.append(Problem(field=key, code="value", message=f"{key}: {problem}"))
                else:
                    out[key] = sent
                    if note:
                        notes.append(f"{key}: {note}")

        if op == "create":
            for f in s.fields.values():
                if f.required and f.default in (None, "") and data.get(f.name) in (None, "", []):
                    problems.append(
                        Problem(
                            field=f.name,
                            code="required",
                            message=f"{f.name!r} is required to create a record in {s.type}",
                        )
                    )

        return CheckResult(module=module, op=op, data=out, problems=problems, conversions=notes)

    def _picklist_problem(self, f: FieldSchema, value: Any) -> str | None:
        values = value if isinstance(value, list) else [value]
        try:
            items = self.client.picklists.values(f.picklist)
        except Exception:  # noqa: BLE001 - no picklist data: let the resolver/server decide
            return None
        if not items:
            return None
        friendly = {str(i["itemValue"]).lower() for i in items if i.get("itemValue") is not None}
        iris = {i["iri"] for i in items if i.get("iri")} | {
            f"/api/3/picklists/{i['uuid']}" for i in items if i.get("uuid")
        }
        bad = []
        for v in values:
            if isinstance(v, dict):
                v = v.get("@id") or v.get("itemValue")
            if not isinstance(v, str):
                continue
            if v.startswith("/api/3/picklists/"):
                if v not in iris:
                    bad.append(f"{v} (not an item of {f.picklist!r})")
            elif v.lower() not in friendly:
                bad.append(repr(v))
        if not bad:
            return None
        valid = ", ".join(str(i["itemValue"]) for i in items[:25])
        return f"{f.name!r}: {', '.join(bad)} is not in picklist {f.picklist!r} (valid: {valid})"


def _relation_problem(f: FieldSchema, value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    wrong = []
    for v in values:
        if isinstance(v, dict):
            v = v.get("@id")
        if isinstance(v, str):
            m = _IRI.match(v)
            if m and m.group(1) != f.target:
                wrong.append(v)
    if not wrong:
        return None
    return f"{f.name!r} links to {f.target!r} records, not {', '.join(wrong)}"


def _pick(members: list[dict[str, Any]], want: str) -> dict[str, Any] | None:
    for key in ("type", "module"):
        for m in members:
            if str(m.get(key, "")).lower() == want:
                return m
    return None
