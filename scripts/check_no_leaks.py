#!/usr/bin/env python3
"""Pre-commit gate: no private/lab IPs or credential values leak into the repo.

Two leak classes are refused

1. **Private/lab IPs** -- any RFC-1918 / CGNAT IPv4 literal that is NOT a
   known placeholder. A future lab host on any private subnet (e.g.
   ``10.50.x.x`` or ``172.16.x.x``) is caught. Placeholder ranges are
   allowlisted so the example IP ``10.0.0.1`` used throughout the guides/tests
   is NOT flagged:

     * ``10.0.0.0/24``        -- documented example host
     * ``192.0.2.0/24``       -- TEST-NET-1 (RFC 5737, for documentation)
     * ``198.51.100.0/24``    -- TEST-NET-2
     * ``203.0.113.0/24``     -- TEST-NET-3
     * ``127.0.0.0/8`` loopback is excluded from matching entirely.

   IPs aren't secrets, but they identify an internal lab host and must not
   ship publicly (examples, docstrings, provenance constants, validation docs).

2. **Credential values** -- any literal assigned to a credential keyword
   (``password`` / ``passwd`` / ``secret`` / ``token`` / ``api_key``) that is
   NOT an obvious placeholder. This is value-agnostic: it catches a default
   password AND any rotated real password (``<real-secret>``), because the
   gate can't tell a placeholder from a real secret by value alone.
   Placeholders that pass: ``<your-password>``, ``<api-key>``, ``'...'`` /
   ``"..."`` (exactly three dots), ``'$VAR'`` / ``"$VAR"`` (env-var
   expansion), and an empty string. This keeps examples/docs working as long
   as credentials are written as placeholders or env-var references.

   Matched only in credential context (keyword then value), so the company
   name, the connector named ``<vendor>-<product>``, and an
   ``@<vendor>.com`` email are NOT flagged -- none sit behind a password
   keyword.

Run as a pre-commit hook it scans every tracked file (``pass_filenames: false``
+ ``always_run``), so a partial commit can't sneak a leak through by simply
not staging the offending file. Run directly (``uv run python
scripts/check_no_leaks.py --all``) to scan the whole tree -- the "is main
clean?" check before a release.

This is a pre-commit-only gate (deliberately not wired into CI): it runs before
a leak ever reaches a public commit/push, which is the right place to stop it
on a public repo. The tradeoff is that it IS bypassable (``--no-verify``, the
GitHub web editor, the squash-merge button), so a leak that slips past it has
no CI backstop and can reach PyPI -- run ``--all`` manually before publishing.

**Output is redacted** (file:line + class, never the line) so even a failed
local run can't re-leak the secret into a terminal screenshot or paste.

Exit 0 when clean, 1 on any violation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: Any RFC-1918 (10/8, 172.16-31, 192.168) or CGNAT (100.64/10) IPv4 literal,
#: full four-octet form only (so a bare ``10.0`` version fragment isn't a hit).
#: Loopback 127/8 is intentionally excluded.
_PRIV_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
)

#: Placeholder IPs that are safe to ship (example host + RFC 5737 TEST-NETs).
#: A matched private IP whose first two octets (or exact value) land here is
#: NOT a violation. ``10.0.0.x`` is the documented example host used across
#: the guides; the TEST-NETs are the RFC-sanctioned documentation ranges.
_PLACEHOLDER_IP_PREFIXES = {
    "10.0.0",  # example host (10.0.0.1 etc.) used throughout guides/tests
    "192.0.2",  # TEST-NET-1
    "198.51.100",  # TEST-NET-2
    "203.0.113",  # TEST-NET-3
}

#: Credential detection is split into two forms, because a bare (unquoted)
#: word means different things in each:
#:   * CLI flag form (``--password <value>``) -- the value is ALWAYS a literal
#:     (shell doesn't require quotes), so a bare word here is a real value and
#:     must be checked. This is the exact shape of the 2026-06-25 leak.
#:   * Assignment form (``password=<value>`` / ``password: <value>``) -- a
#:     QUOTED value is a literal (check it); a BARE word is a variable/type
#:     reference (``password=pwd``, ``password: str``, ``token=plaintext2``),
#:     not a spelled-out secret, so it's safe. (A real secret in assignment
#:     form is virtually always quoted, e.g. ``password="<real-secret>"``.)
#: Both forms ignore the keyword when it's prose ("password auth", "password
#: handling") by requiring a separator (``=`` / ``:`` for assignment, or the
#: ``--``/``-`` flag prefix + whitespace for CLI).
_CRED_CLI = re.compile(
    r"(?i)(?<![a-z0-9])(?P<dash>-{1,2})"
    r"(?P<kw>password|passwd|secret|token|api[_-]?key)\b"
    r"(?:\s+|=)\s*"  # space or '=' after the flag
    r"(?P<val>[\"']?[^\s\"',;)}]+[\"']?)"  # the value token
)
_CRED_ASSIGN = re.compile(
    r"(?i)(?<![a-z0-9_-])(?P<kw>password|passwd|secret|token|api[_-]?key)"
    r"(?:_PASSWORD|_TOKEN|_KEY|_SECRET)*"  # FSR_PASSWORD, API_TOKEN, ...
    r"\b[\"']?\s*(?::|=(?!=))\s*"  # : or = separator (NOT ==); allow "key": JSON form
    r"(?P<val>[\"']?[^\s\"',;)}]+[\"']?)"  # the value token
)

#: Known-safe literal credential VALUES -- obviously-fake strings used in
#: examples, config templates, and test fixtures, that the gate lets through.
#: A real secret is NEVER added here; this list is curated and reviewed. If a
#: real secret ever lands as a literal value that ISN'T in this list, the gate
#: flags it -- which is the point. (Lowercased before lookup, so 'Str0ng!Pass'
#: matches 'str0ng!pass'.)
_SAFE_LITERALS = {
    # placeholder text in examples / templates
    "your-password",
    "your_password",
    "your-api-key",
    "your_api_key",
    "your-api-token",
    "your-token",
    "your-secret",
    # field-name stand-ins used as a literal value (e.g. password = "password"
    # in config.toml.example) -- the field name itself as a stand-in
    "password",
    "passwd",
    "secret",
    "token",
    "api-key",
    "api_key",
    "apikey",
    # classic placeholder conventions
    "changeme",
    "placeholder",
    "redacted",
    "example",
    "xxx",
    "xxxx",
    # documented docstring / test-sample values (clearly fabricated)
    "str0ng!pass",
    "test-key-123",
    "test_pass",
    "demo-token",
    "demo-key",
    "demo-key-123",
    "test-token",
    "fake-key",
    "fake-token",
    "invalid-key",
    "mock-token-123",
    "mock-jwt-token-123",
    "string",  # mock-response fixtures
    "null",  # FortiSOAR's own sentinel for a secret it never echoes back (not a value)
}

#: Credential values that are placeholders, not real secrets. Matched
#: case-insensitively, after stripping surrounding quotes:
#:   * ``<your-password>`` / ``<api-key>`` / ``<token>`` / ``<secret>`` (angle)
#:   * ``...`` / ``'...'`` / ``"..."`` (exactly three dots)
#:   * ``$VAR`` / ``${VAR}`` / ``\"$VAR\"`` (env-var expansion -- a real
#:     secret is never spelled out as a literal here)
#:   * empty string
_PLACEHOLDER_VALUE = re.compile(
    r"""(?ix)
    ^(?:
        <(?:your-)?(?:password|passwd|secret|token|api[_-]?key|redacted|api-key)>
      | \.{3}                         # ...
      | \$\{?[A-Z_][A-Z0-9_]*\}?        # $VAR / ${VAR}  (uppercase = env var)
      | \$[a-zA-Z_][a-zA-Z0-9_]*       # $Var (e.g. $FSR_PASSWORD)
      | \$\{\{[^}]+\}\}               # ${{ secrets.X }}  (GitHub Actions expr)
      | os\.environ\.get              # os.environ.get(...) -- env lookup, not a literal
      | [a-z_][a-z0-9_]*\.[a-z_][a-z0-9_.]*  # attr chain WITH a dot (args.password); a bare
                                             #   lowercase word is NOT a placeholder -- it
                                             #   could be a real secret (a default or
                                             #   rotated password), so don't treat it as one
      | (?:password|passwd|secret|token|api[_-]?key)  # field-name stand-in in a template
      |                                # empty
    )$
    """
)


def _is_placeholder_ip(ip_str: str) -> bool:
    """True if ``ip_str`` is a documented placeholder (10.0.0.x / TEST-NET)."""
    # Match on the first three octets: 10.0.0.x, 192.0.2.x, etc.
    octets = ip_str.split(".")
    return ".".join(octets[:3]) in _PLACEHOLDER_IP_PREFIXES


def _normalize_value(raw: str) -> tuple[str, bool]:
    """Strip a captured credential value to its inner form.

    Returns ``(inner, is_quoted)``. ``inner`` has surrounding quotes and a
    trailing ``(...)`` call removed (``fsr.get("x", "y")`` -> ``fsr.get``) so
    an attr/call reference can be recognized as a non-literal.
    """
    v = raw.strip()
    is_quoted = len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]
    inner = v.strip("\"'").strip()
    # drop a trailing call expression: fsr.get("password", "changeme") -> fsr.get
    inner = re.sub(r"\s*\(.*$", "", inner).strip().rstrip(".,;").strip()
    return inner, is_quoted


def _is_nonliteral_reference(inner: str) -> bool:
    """True if a bare (unquoted) assignment value is a variable/type/call, not
    a spelled-out secret. ``password=pwd`` / ``password: str`` / ``token=
    fsr.get(...)`` / ``password=None`` -- the value is a programmatic reference,
    so no literal secret is present."""
    if inner in {"None", "True", "False", "str", "int", "bool", "float", "Optional", "Any"}:
        return True
    # attribute/method chain (args.password, os.environ.get, fsr.get, self.pwd)
    if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*", inner):
        return True
    return False


#: Credential values that follow a "clearly fabricated" naming convention --
#: a real secret is never prefixed mock-/test-/demo-/fake-/sample-/example-/
#: tok-/key-. Lets test fixtures (mock-token-123, tok-2, test-key-123) through
#: without enumerating each.
_FAKE_VALUE = re.compile(r"(?i)^(tok|key|mock|test|demo|fake|sample|example|dummy)([-_][a-z0-9]+)*$")


def _is_safe_literal(inner: str) -> bool:
    """True if a literal credential value is a known-safe example/placeholder
    string (allowlisted or following a fake-naming convention)."""
    return inner.lower() in _SAFE_LITERALS or bool(_FAKE_VALUE.match(inner))


#: Characters a real secret value is made of. A captured value that contains
#: anything else (``{ } / ( ) < >`` -- JSON shape, path sep, angle brackets,
#: GitHub-Actions ``${{``) or is shorter than 3 chars is not a plausible secret
#: and is skipped, so prose like ``--password or FSR_PASSWORD`` (captures
#: ``or``), ``api_key: {key, ...}`` (captures ``{key``), and 1-2 char test
#: stubs (``"p"``, ``"pw"``) don't false-trigger.
_SECRET_CHARS = re.compile(r"^[A-Za-z0-9!@#$%^&*_\-+.~]{4,}$")


def _is_plausible_secret(inner: str) -> bool:
    """True if a value *could* be a real secret (>=3 chars, secret-like chars
    only). Short stubs, prose connectors, and punctuation-bearing shapes are not
    plausible and are skipped before the allowlist check."""
    return bool(_SECRET_CHARS.match(inner))


#: Path prefixes where IPv4 literals are legitimate *content*, not lab
#: provenance: the example / tutorial playbook corpus. Real FortiSOAR playbooks
#: carry sample IPs in Jinja filter demos (``... | ipaddr('192.0.0.0/8')``),
#: extract_artifacts inputs, and default field values -- editing them would
#: corrupt the teaching examples. The IP check is skipped for these paths; the
#: credential check still runs (a real secret in an example is never wanted),
#: and the lab-IP guard stays fully active everywhere else (src/, scripts/,
#: docs/, tests/) where a real lab host would actually leak.
_IP_EXEMPT_PREFIXES = ("examples/",)


def _ip_exempt(path: Path) -> bool:
    """True if ``path`` is example/tutorial content where sample IPs belong."""
    return path.as_posix().startswith(_IP_EXEMPT_PREFIXES)


#: Suffixes where a bare (unquoted) assignment value is a *variable reference*
#: (Python kwargs, type annotations, embedded code in docs) rather than a
#: literal. In config files (.yaml/.toml/.ini/.sh/.env) a bare value IS a
#: literal (``password: hunter2`` in YAML), so it's checked there.
_BARE_IS_VAR_SUFFIXES = {".py", ".pyi", ".md", ".rst", ".ipynb"}


def _scan(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(line_no, label, line)`` for each pattern hit in ``path``."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []
    bare_is_var = path.suffix.lower() in _BARE_IS_VAR_SUFFIXES
    ip_exempt = _ip_exempt(path)
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # 1. private/lab IPs (minus placeholders). Skipped for example/tutorial
        #    playbook content, where sample IPs are intrinsic (see _ip_exempt).
        if not ip_exempt:
            ip_hit = False
            for m in _PRIV_IP.finditer(line):
                if not _is_placeholder_ip(m.group(0)):
                    hits.append((i, "private/lab IP", line))
                    ip_hit = True
                    break  # one IP violation per line is enough
            if ip_hit:
                continue
        # 2. credential values -- value-agnostic. A literal is flagged unless
        #    it is a placeholder ($VAR / <...> / ...), implausible (too short /
        #    prose / punctuation), a known-safe example string (allowlisted),
        #    or a non-literal reference (bare variable/type in assignment form,
        #    in code suffixes only).
        cred_hit = False
        # 2a. CLI flag form (--password VALUE): value is ALWAYS a literal.
        for m in _CRED_CLI.finditer(line):
            inner, _ = _normalize_value(m.group("val"))
            if _PLACEHOLDER_VALUE.match(inner) or not _is_plausible_secret(inner) or _is_safe_literal(inner):
                continue
            hits.append((i, "credential value", line))
            cred_hit = True
            break
        if cred_hit:
            continue
        # 2b. Assignment form (password=VALUE / password: VALUE / "password":
        #     VALUE): quoted value = literal (check); bare word = variable
        #     reference (safe) -- but only in code suffixes. In config files a
        #     bare value is a literal and is checked.
        for m in _CRED_ASSIGN.finditer(line):
            inner, is_quoted = _normalize_value(m.group("val"))
            if _PLACEHOLDER_VALUE.match(inner) or not _is_plausible_secret(inner) or _is_safe_literal(inner):
                continue
            if not is_quoted and bare_is_var and _is_nonliteral_reference(inner):
                continue
            hits.append((i, "credential value", line))
            cred_hit = True
            break
    return hits


#: This file itself documents the patterns -- skip it so the gate doesn't flag
#: its own docstrings/comments.
_SELF = Path(__file__).resolve()


def _tracked_files() -> list[Path]:
    """All git-tracked files (the set a release would ship)."""
    out = subprocess.check_output(["git", "ls-files"], text=True, stderr=subprocess.DEVNULL)
    return [Path(line) for line in out.splitlines() if line]


def main(argv: list[str]) -> int:
    # Pre-commit passes staged filenames when pass_filenames is true; with the
    # always_run + pass_filenames:false config it passes none and we scan the
    # whole tracked tree. ``--all`` forces the full scan either way.
    if "--all" in argv:
        targets = _tracked_files()
    elif argv:
        targets = [Path(a) for a in argv if a != "--"]
    else:
        targets = _tracked_files()

    violations: list[str] = []
    for path in targets:
        if path.resolve() == _SELF:
            continue
        if not path.is_file():
            continue
        for line_no, label, _line in _scan(path):
            # Do NOT echo the offending line: this repo is public, and a
            # failure that prints the leaked value (a credential or lab IP)
            # would re-publish the very secret it exists to catch into a log
            # or terminal paste that outlives a later scrub. The label names
            # the leak class; file:line is enough to locate it locally.
            violations.append(f"{path}:{line_no}: [{label}]")

    if violations:
        print(
            "Refusing to commit: lab IP / default-password leak(s) found in tracked files.\n"
            "  These identify an internal lab host / default credential and must not ship publicly.\n"
            "  Sanitize to a placeholder (e.g. 'fortisoar.example.com', '<your-password>').\n"
            "  (Line content is omitted so a CI failure can't re-leak the secret.)\n",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
