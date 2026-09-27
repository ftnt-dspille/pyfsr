"""Typed models for a FortiSOAR **AI agent package** -- the installable bundle.

An AI agent (the FortiSOAR 8.0 *agentic AI* kind, run by the ``fsr-ai`` service --
**not** the remote execution :class:`~pyfsr.models._agents.Agent`) ships as a zip
whose top-level folder is the agent's ``name``. Both the Fortinet-published agents
and a custom one you author share the same layout::

    <name>/
      info.json            # the manifest (this module's AgentInfo)
      agent.py             # defines the class named by info.json "agentclass"
      __init__.py
      prompt.yaml          # prompt registry keyed by uuid (AgentPromptFile)
      config/
        memory.yaml        # allowed_tools: {<mcp_config_uuid>: [tool, ...]}  (AgentMemory)
      images/
        small.png
        large.png
      constants.py         # optional helper modules

These models exist to (a) validate a package before you upload it -- a bad
``agentclass`` or a prompt uuid the code references but the yaml omits is a silent
runtime failure on the appliance -- and (b) give tooling typed access to the
manifest. They stay dict-compatible (``extra="allow"``) because the manifest
carries more keys than are curated here.

Shapes verified against the Fortinet-published ``metric-computation`` and
``fortisoar-data-access`` agents (fsrMinCompatibility 8.0.0).

See :func:`pyfsr.api.ai.pack_agent` to bundle a source dir and
:meth:`pyfsr.api.ai.AIApi.import_agent` to upload it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: fsr-ai 8.0.1's ``requirements_validation.dangerous_patterns`` (``app_config.yaml``),
#: copied verbatim. On import, each non-blank, non-``#`` line of an agent's
#: ``requirements.txt`` is ``re.search``-ed case-insensitively against these and
#: the first hit rejects the package. 8.0.0 had no such check. The effect: an
#: agent can only install from the appliance's default package index -- no
#: private mirror, no git or URL requirement.
#:
#: Kept byte-for-byte, quirks included: ``--find-link\s+`` does not match the
#: real pip spelling ``--find-links``, and ``-i\s+`` also hits any ``-i `` in a
#: line. Matching the server exactly is the point.
REQUIREMENTS_BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"--index-url\s+", "Custom index URL is not allowed"),
    (r"--extra-index-url\s+", "Custom index URL is not allowed"),
    (r"--trusted-host\s+", "Custom trusted host is not allowed"),
    (r"-i\s+", "Custom index URL is not allowed"),
    (r"-f\s+", "Custom find link is not allowed"),
    (r"--find-link\s+", "Custom find link is not allowed"),
    (r"git\+", "Git repository URLs are not allowed"),
    (r"http[s]?://", "External HTTP URLs are not allowed"),
    (r"ftp://", "FTP URLs are not allowed"),
)


def requirements_problems(text: str) -> list[str]:
    """Return why FortiSOAR 8.0.1 would refuse this ``requirements.txt``, one entry per bad line.

    Same rule as fsr-ai's ``_validate_requirements``: strip each line, skip
    blanks and ``#`` comments, report the first pattern in
    ``REQUIREMENTS_BLOCKED_PATTERNS`` that matches. The server stops at the
    first bad line; this lists every one so they can all be fixed at once.
    """
    import re

    problems: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for pattern, message in REQUIREMENTS_BLOCKED_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                problems.append(f"requirements.txt: {message}: {line!r}")
                break
    return problems


class _Lenient(BaseModel):
    """Base: preserve unknown manifest keys, allow population by field name."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# Manifest fields behind NOT NULL ai_agent columns (name/version are always set).
# "" is accepted: stock agents ship category "".
_REQUIRED_INFO_FIELDS = ("label", "agentclass", "category", "publisher")


class AgentInfo(_Lenient):
    """The ``info.json`` manifest of an AI agent package.

    ``agentclass`` must name a class defined in ``agent.py`` (and be unique among
    installed agents); :class:`AgentPackage` cross-checks it and the required
    fields. :func:`~pyfsr.pack_agent` names the zip's top folder after ``name``.
    ``configuration.fields`` is the per-agent config form the FortiSOAR UI renders
    (config-type toggle, LLM-provider picker, MCP-server multiselect, masking
    agent) -- left untyped here as it's a free-form field schema.
    """

    name: str
    label: str | None = None
    agentclass: str | None = None
    version: str = "1.0.0"
    description: str | None = None
    publisher: str | None = None
    cs_approved: bool | None = None
    cs_compatible: bool | None = None
    contributor: str | None = None
    category: str | None = None
    icon_small_name: str | None = None
    icon_large_name: str | None = None
    tags: list[str] = Field(default_factory=list)
    fsrMinCompatibility: str | None = None
    help_online: str | None = None
    additional_information: list[dict[str, Any]] = Field(default_factory=list)
    inputformat: dict[str, Any] = Field(default_factory=dict)
    outputformat: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)


class AgentPrompt(_Lenient):
    """One entry in ``prompt.yaml``'s ``prompts`` map (keyed by a uuid).

    ``agent.py`` pulls a prompt by that uuid (``self.get_prompt_by_uuid(...)``)
    and ``.format(**inputs)`` s ``system_instruction`` / ``user_instruction`` -- so
    any ``{placeholder}`` in those strings must be supplied at call time.
    """

    name: str | None = None
    system_instruction: str | None = None
    user_instruction: str | None = None
    validation_instruction: str | None = None
    response_format: Any | None = None
    description: str | None = None


class AgentPromptFile(_Lenient):
    """The whole ``prompt.yaml``: ``{"prompts": {<uuid>: AgentPrompt}}``."""

    prompts: dict[str, AgentPrompt] = Field(default_factory=dict)


class AgentMemory(_Lenient):
    """``config/memory.yaml`` -- the agent's MCP-tool allowlist.

    ``allowed_tools`` maps a registered **MCP-configuration uuid** (see
    ``client.ai.mcp_configs()``) to the list of tool names on that server the
    agent may call. An empty list means "server is bound but no tools yet
    allowed"; the key must be a uuid that actually resolves on the target
    appliance or the binding is inert.
    """

    allowed_tools: dict[str, list[str]] = Field(default_factory=dict)

    def mcp_configuration_uuids(self) -> list[str]:
        """The MCP-configuration uuids this agent is wired to."""
        return list(self.allowed_tools.keys())


class AgentPackage(BaseModel):
    """A fully-parsed AI agent package: manifest + prompts + memory + file list.

    Build one with :meth:`from_dir` to validate a source folder before packing,
    or construct directly. :meth:`validate_consistency` catches the mistakes that
    fail *silently on the appliance* rather than at upload:

    - a manifest field fsr-ai stores NOT NULL missing (``label``, ``agentclass``,
      ``category``, ``publisher``) -- the import then fails half-way and leaves
      ``ai-agents/<name>_<version>/`` behind, which blocks every later import of
      that name, ``replace=true`` included (8.0.1);
    - ``agent.py`` missing, or not defining the class named by ``agentclass``;
    - a prompt uuid referenced in ``agent.py`` that ``prompt.yaml`` doesn't define;
    - icons named in the manifest that aren't in the package.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    info: AgentInfo
    prompts: AgentPromptFile = Field(default_factory=AgentPromptFile)
    memory: AgentMemory = Field(default_factory=AgentMemory)
    #: Package-relative file paths present in the bundle (e.g. ``"agent.py"``).
    files: list[str] = Field(default_factory=list)
    #: Contents of ``requirements.txt`` when the package has one -- checked
    #: against ``REQUIREMENTS_BLOCKED_PATTERNS``.
    requirements: str | None = None
    #: Source of ``agent.py`` when known -- used to check ``agentclass`` and
    #: cross-check referenced prompt uuids.
    agent_source: str | None = None

    @classmethod
    def from_dir(cls, source_dir: str) -> AgentPackage:
        """Parse and validate an agent package from a source directory.

        ``source_dir`` is the package root (the folder that *is* the agent, e.g.
        ``.../metric-computation``). Reads ``info.json`` (required),
        ``prompt.yaml`` and ``config/memory.yaml`` (both optional), and records
        the file list + ``agent.py`` source. Raises on a missing/invalid manifest
        or a failed consistency check.
        """
        import json
        from pathlib import Path

        import yaml

        root = Path(source_dir)
        info_path = root / "info.json"
        if not info_path.is_file():
            raise FileNotFoundError(f"no info.json in agent package dir: {source_dir}")
        info = AgentInfo.model_validate(json.loads(info_path.read_text()))

        prompts = AgentPromptFile()
        prompt_path = root / "prompt.yaml"
        if prompt_path.is_file():
            prompts = AgentPromptFile.model_validate(yaml.safe_load(prompt_path.read_text()) or {})

        memory = AgentMemory()
        memory_path = root / "config" / "memory.yaml"
        if memory_path.is_file():
            memory = AgentMemory.model_validate(yaml.safe_load(memory_path.read_text()) or {})

        agent_py = root / "agent.py"
        requirements_path = root / "requirements.txt"
        files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        pkg = cls(
            info=info,
            prompts=prompts,
            memory=memory,
            files=files,
            agent_source=agent_py.read_text() if agent_py.is_file() else None,
            requirements=requirements_path.read_text() if requirements_path.is_file() else None,
        )
        pkg.validate_consistency()
        return pkg

    def validate_consistency(self) -> None:
        """Raise :class:`ValueError` on package defects that fail silently on-box.

        Checks the ``agentclass`` is defined in ``agent.py``, every prompt uuid
        the source references exists in ``prompt.yaml``, and manifest-named icons
        are present, and that ``requirements.txt`` passes the 8.0.1 import check
        (see ``requirements_problems()``). A no-op for fields it can't see
        (e.g. no ``agent_source``).
        """
        problems: list[str] = []

        missing = [f for f in _REQUIRED_INFO_FIELDS if getattr(self.info, f) is None]
        if missing:
            problems.append(f"info.json is missing required field(s): {', '.join(missing)}")

        if self.requirements is not None:
            problems.extend(requirements_problems(self.requirements))

        if self.agent_source is not None and self.info.agentclass:
            if f"class {self.info.agentclass}" not in self.agent_source:
                problems.append(f'info.json agentclass "{self.info.agentclass}" is not defined in agent.py')

        if self.agent_source is not None:
            import re

            referenced = set(
                re.findall(
                    r"get_prompt_by_uuid\(\s*['\"]([0-9a-fA-F-]{36})['\"]",
                    self.agent_source,
                )
            )
            missing = referenced - set(self.prompts.prompts)
            for uuid in sorted(missing):
                problems.append(f"agent.py references prompt uuid {uuid} not in prompt.yaml")

        if self.files:
            for icon in (self.info.icon_small_name, self.info.icon_large_name):
                if icon and not any(f.endswith(icon) for f in self.files):
                    problems.append(f'manifest icon "{icon}" not found in package files')

        if problems:
            raise ValueError("AI agent package failed validation:\n  - " + "\n  - ".join(problems))

    @model_validator(mode="after")
    def _name_present(self) -> AgentPackage:
        if not self.info.name:
            raise ValueError("agent package info.json is missing a 'name'")
        return self
