#!/usr/bin/env python3
"""Validate unenforced code examples in the guide docs.

`make doctest` already executes every ``{doctest}`` directive (the return-shape
examples). This script covers the *other* examples that nothing runs: the plain
fenced ```` ```python ```` and ```` ```sh ```` blocks readers copy-paste. It
proves the symbols and CLI flags the prose names actually exist, so a namespace
regroup or CLI flag rename can't silently rot the docs.

Two checks:

1. **Python references** (offline, fast). For every fenced ```` ```python ````
   block that is *not* a ``{doctest}`` directive, resolve each
   ``from pyfsr... import`` / ``import pyfsr...`` and each ``pyfsr.<attr>``
   access against the live package. Submodules that ``import pyfsr`` does not
   auto-load (e.g. ``pyfsr.authoring``) are imported explicitly so they resolve.

2. **CLI invocations** (spawns ``pyfsr <chain> --help`` per unique command
   chain, cached). For every ```` ```sh ```` block, descend the command chain
   word-by-word and check the trailing ``--flags`` against the deepest
   subcommand's real ``--help`` output. Shell ``#`` comments are stripped first.

The script does NOT execute example bodies (most need a live appliance) — it
only proves the named symbols/flags exist. Exit code is 1 if any drift is
found, 0 otherwise.

Usage::

    python scripts/check_doc_examples.py            # both checks
    python scripts/check_doc_examples.py --no-cli    # python refs only
    python scripts/check_doc_examples.py --guide records.md
"""

from __future__ import annotations

import argparse
import glob
import importlib
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDES_DIR = os.path.join(REPO_ROOT, "docs", "source", "guides")

FENCE = re.compile(r"^`{3,}")


# --------------------------------------------------------------------------
# fence parsing
# --------------------------------------------------------------------------
def iter_blocks(lines, want):
    """Yield ``(start_line_1indexed, [code_lines])`` for fenced blocks whose
    info-string matches ``want(info)``. ``{doctest}`` directives are skipped."""
    stack = []
    buf = []
    for i, ln in enumerate(lines):
        if FENCE.match(ln):
            if stack:
                kind, start = stack.pop()
                if kind == want and buf:
                    yield (start + 1, buf)
                buf = []
            else:
                info = ln[ln.index("```") + 3 :].strip()
                if info.startswith("{doctest"):
                    stack.append(("doctest", i))
                elif want(info):
                    stack.append((want, i))
                    buf = []
                else:
                    stack.append(("other", i))
            continue
        if stack and stack[-1][0] == want:
            buf.append(ln)


def is_python(info):
    return info == "python" or info.startswith("python ")


def is_shell(info):
    return info in ("sh", "shell", "bash", "console", "shell-session")


# --------------------------------------------------------------------------
# python reference resolution
# --------------------------------------------------------------------------
def resolve_dotted(pyfsr, dotted):
    """Resolve ``a.b.c`` against the ``pyfsr`` package. Submodules that are not
    auto-loaded by ``import pyfsr`` are imported explicitly. Returns
    ``(ok, detail)``."""
    parts = [p for p in dotted.split(".") if p]
    # try the full path as a submodule first (covers pyfsr.authoring etc.)
    try:
        importlib.import_module("pyfsr." + ".".join(parts))
        return (True, "module")
    except ModuleNotFoundError:
        pass
    # otherwise walk attributes, importing intermediate submodules as we go
    obj = pyfsr
    walked = []
    for p in parts:
        walked.append(p)
        if not hasattr(obj, p):
            try:
                obj = importlib.import_module("pyfsr." + ".".join(walked))
                continue
            except ModuleNotFoundError:
                return (False, f"'{dotted}' stops at '{p}' (not on {type(obj).__name__})")
        try:
            obj = getattr(obj, p)
        except Exception as e:  # pragma: no cover - defensive
            return (False, f"'{dotted}' raised {type(e).__name__}: {e}")
    return (True, type(obj).__name__)


# strip a trailing ``# comment`` from a python line, ignoring ``#`` inside
# double quotes (good enough for the doc examples, none of which embed ``#``
# inside single-quoted SQL).
def strip_py_comment(line):
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i]
    return line


FROM_IMPORT_RE = re.compile(
    r"^\s*from\s+(pyfsr[\w.]*)\s+import\s+\(([^)]*)\)"  # parenthesized multi-line
    r"|^\s*from\s+(pyfsr[\w.]*)\s+import\s+(.+)$",  # single-line
    re.M,
)
PLAIN_IMPORT_RE = re.compile(r"^\s*import\s+(pyfsr[\w.]*)", re.M)
ATTR_RE = re.compile(r"\bpyfsr\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")


def check_python_block(code, pyfsr):
    issues = []
    seen = set()
    # drop comments line-by-line so ``# pyfsr.foo`` prose isn't checked
    code = "\n".join(strip_py_comment(ln) for ln in code.splitlines())
    for m in FROM_IMPORT_RE.finditer(code):
        mod = m.group(1) or m.group(3)
        names = m.group(2) if m.group(2) is not None else m.group(4)
        rel = mod.replace("pyfsr", "", 1).lstrip(".")
        target = pyfsr
        if rel:
            ok, detail = resolve_dotted(pyfsr, rel)
            if not ok:
                key = f"mod:{mod}"
                if key not in seen:
                    issues.append(f"import {mod} -> {detail}")
                    seen.add(key)
                continue
            target = importlib.import_module("pyfsr." + rel)
        for nm in re.split(r"[,\s]+", names):
            nm = nm.strip()
            if not nm or nm in ("\\", "*", "") or nm.startswith("#"):
                continue
            nm = nm.split(" as ")[0].strip()
            if not nm:
                continue
            if not hasattr(target, nm):
                key = f"{mod}.{nm}"
                if key not in seen:
                    issues.append(f"from {mod} import {nm} -> not found")
                    seen.add(key)
    for m in PLAIN_IMPORT_RE.finditer(code):
        mod = m.group(1)
        rel = mod.replace("pyfsr", "", 1).lstrip(".")
        if rel:
            ok, detail = resolve_dotted(pyfsr, rel)
            if not ok and f"imp:{mod}" not in seen:
                issues.append(f"import {mod} -> {detail}")
                seen.add(f"imp:{mod}")
    for m in ATTR_RE.finditer(code):
        dotted = m.group(1)
        ok, detail = resolve_dotted(pyfsr, dotted)
        if not ok and f"attr:pyfsr.{dotted}" not in seen:
            issues.append(f"pyfsr.{dotted} -> {detail}")
            seen.add(f"attr:pyfsr.{dotted}")
    return issues


# --------------------------------------------------------------------------
# CLI validation via on-demand --help
# --------------------------------------------------------------------------
_HELP_CACHE = {}
_FLAG_RE = re.compile(r"(--[\w-]+)")
_SUB_RE = re.compile(r"^\s{2,}(\w[\w-]*)\s{2,}", re.M)


def help_for(cmd_words):
    """Return ``(ok, flags_set, child_subs_set)`` for ``pyfsr <cmd> --help``."""
    if cmd_words in _HELP_CACHE:
        return _HELP_CACHE[cmd_words]
    try:
        r = subprocess.run(
            ["pyfsr"] + list(cmd_words) + ["--help"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = r.stdout + r.stderr
    except Exception:
        res = (False, set(), set())
        _HELP_CACHE[cmd_words] = res
        return res
    flags = set(_FLAG_RE.findall(text))
    subs = set(_SUB_RE.findall(text)) - {"show", "this", "program", "usage"}
    res = (True, flags, subs)
    _HELP_CACHE[cmd_words] = res
    return res


def check_shell_lines(lines):
    issues = []
    for ln in lines:
        s = ln.strip().lstrip("$>").strip()
        s = re.sub(r"\s#.*$", "", s)  # strip trailing shell comment
        m = re.match(r"pyfsr\s+(.*)$", s)
        if not m:
            continue
        tokens = m.group(1).split()
        cmd = ()
        i = 0
        while i < len(tokens) and not tokens[i].startswith("-"):
            ok, _flags, subs = help_for(cmd)
            if not ok and cmd:
                break
            if not cmd or tokens[i] in subs:
                cmd = cmd + (tokens[i],)
                i += 1
                continue
            break
        ok, known, _subs = help_for(cmd)
        if not ok or not cmd:
            issues.append(f"subcommand 'pyfsr {tokens[0]}...' not recognized")
            continue
        remainder = " ".join(tokens[i:])
        for f in _FLAG_RE.findall(remainder):
            f = f.split("=")[0]
            if f not in known and not any(k.startswith(f) for k in known):
                issues.append(f"flag '{f}' on 'pyfsr {' '.join(cmd)}' not in --help (known: {sorted(known)[:8]})")
    return issues


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def main():
    import pyfsr  # local import so --help works without the package

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-cli", action="store_true", help="skip the CLI flag check")
    ap.add_argument("--cli-only", action="store_true", help="only run the CLI flag check")
    ap.add_argument("--guide", default=None, help="limit to a single guide basename")
    args = ap.parse_args()

    do_cli = not args.no_cli and not args.cli_only
    do_py = not args.cli_only
    # if pyfsr binary missing, skip CLI check gracefully
    if do_cli:
        try:
            subprocess.run(["pyfsr", "--help"], capture_output=True, timeout=20)
        except Exception:
            print("WARNING: `pyfsr` CLI not on PATH; skipping CLI-flag check", file=sys.stderr)
            do_cli = False

    total_issues = 0
    total_py = total_sh = 0
    for g in sorted(glob.glob(os.path.join(GUIDES_DIR, "*.md"))):
        if args.guide and os.path.basename(g) != args.guide:
            continue
        lines = open(g).read().splitlines()
        name = os.path.basename(g)
        py_issues, sh_issues = [], []
        if do_py:
            for start, code in iter_blocks(lines, is_python):
                total_py += 1
                iss = check_python_block("\n".join(code), pyfsr)
                if iss:
                    py_issues.append((start, iss))
        if do_cli:
            for start, slines in iter_blocks(lines, is_shell):
                total_sh += 1
                si = check_shell_lines(slines)
                if si:
                    sh_issues.append((start, si))
        if py_issues or sh_issues:
            print(f"\n=== {name} ===")
            for start, iss in py_issues:
                for line in iss:
                    print(f"  py@L{start}: {line}")
                    total_issues += 1
            for start, iss in sh_issues:
                for line in iss:
                    print(f"  sh@L{start}: {line}")
                    total_issues += 1

    checked = []
    if do_py:
        checked.append(f"{total_py} python blocks")
    if do_cli:
        checked.append(f"{total_sh} shell blocks")
    print(f"\n--- {', '.join(checked)} checked; {total_issues} issue(s) ---")
    return 1 if total_issues else 0


if __name__ == "__main__":
    sys.exit(main())
