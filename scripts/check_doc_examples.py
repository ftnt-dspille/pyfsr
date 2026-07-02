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
    python scripts/check_doc_examples.py --check-floor   # {doctest}-count anti-regression gate
    python scripts/check_doc_examples.py --coverage      # per-file block-count report (advisory)
    python scripts/check_doc_examples.py --update-floor  # regenerate the floor baseline
"""

from __future__ import annotations

import argparse
import glob
import importlib
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDES_DIR = os.path.join(REPO_ROOT, "docs", "source", "guides")
DOCS_SOURCE = os.path.join(REPO_ROOT, "docs", "source")
# Per-file {doctest} block-count floor. `--check-floor` fails CI if any file's
# doctest count drops below its baseline, so a {doctest} cannot quietly be
# replaced by a plain {code-block}. Regenerate with `--update-floor`.
BASELINE_PATH = os.path.join(REPO_ROOT, "docs", "doctest_counts.baseline.json")

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
# block classification + doctest-count floor / coverage report
# --------------------------------------------------------------------------
def classify_fence(info):
    """Classify a fence info-string as ``'doctest'``/``'python'``/``'shell'``/``None``.

    Covers both the plain form (``\\`\\`\\`python``) and the MyST directive form
    (``\\`\\`\\`{code-block} python``) so coverage counts every code block a
    reader sees, however it was authored.
    """
    info = info.strip()
    if info.startswith("{doctest"):
        return "doctest"
    if info.startswith("{"):
        m = re.match(r"\{(\S+)\}\s*(.*)", info)
        if not m or m.group(1) not in ("code-block", "sourcecode"):
            return None
        rest = m.group(2).split()
        lang = rest[0] if rest else ""
    else:
        parts = info.split()
        lang = parts[0] if parts else ""
    if lang in ("python", "py", "python3"):
        return "python"
    if lang in ("sh", "shell", "bash", "console", "shell-session"):
        return "shell"
    return None


def count_blocks(lines):
    """Return ``{'doctest': n, 'python': n, 'shell': n}`` for one file's lines."""
    counts = {"doctest": 0, "python": 0, "shell": 0}
    for ln in lines:
        if FENCE.match(ln):
            kind = classify_fence(ln[ln.index("```") + 3 :])
            if kind:
                counts[kind] += 1
    return counts


def _collect_counts():
    """Walk ``docs/source/**/*.md`` and return ``{relpath: counts}``."""
    out = {}
    for root, _dirs, files in os.walk(DOCS_SOURCE):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, DOCS_SOURCE)
            with open(path) as f:
                out[rel] = count_blocks(f.read().splitlines())
    return out


def run_coverage():
    """Print a per-file block-count table (advisory; always exits 0)."""
    counts = _collect_counts()
    print(f"{'file':<34} {'doctest':>8} {'python':>8} {'shell':>8}")
    tot = {"doctest": 0, "python": 0, "shell": 0}
    for rel in sorted(counts):
        c = counts[rel]
        for k in tot:
            tot[k] += c[k]
        print(f"{rel:<34} {c['doctest']:>8} {c['python']:>8} {c['shell']:>8}")
    print(f"{'TOTAL':<34} {tot['doctest']:>8} {tot['python']:>8} {tot['shell']:>8}")
    return 0


def run_update_floor():
    """Rewrite the baseline file with current {doctest} block counts."""
    counts = _collect_counts()
    data = {
        "_comment": (
            "Per-file {doctest} block counts under docs/source. "
            "`python scripts/check_doc_examples.py --check-floor` fails if any "
            "file's doctest count drops below its baseline, so a {doctest} cannot "
            "quietly be replaced by a plain {code-block}. Regenerate with "
            "`python scripts/check_doc_examples.py --update-floor` when a file "
            "legitimately changes its doctest count."
        ),
        "files": {rel: c["doctest"] for rel, c in sorted(counts.items())},
    }
    with open(BASELINE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"wrote {os.path.relpath(BASELINE_PATH, REPO_ROOT)} ({len(data['files'])} files)")
    return 0


def run_floor_check():
    """Fail (exit 1) if any file's {doctest} count dropped below its baseline."""
    if not os.path.exists(BASELINE_PATH):
        print(
            "ERROR: doctest-count baseline missing at "
            f"{os.path.relpath(BASELINE_PATH, REPO_ROOT)}; run "
            "`python scripts/check_doc_examples.py --update-floor` to create it",
            file=sys.stderr,
        )
        return 1
    with open(BASELINE_PATH) as f:
        baseline = json.load(f).get("files", {})
    current = {rel: c["doctest"] for rel, c in _collect_counts().items()}
    regressions = []
    for rel, floor in baseline.items():
        actual = current.get(rel, "missing")
        if actual == "missing" or actual < floor:
            regressions.append((rel, floor, actual))
    if regressions:
        print("ERROR: doctest-count floor violated (a {doctest} block disappeared):", file=sys.stderr)
        for rel, floor, actual in regressions:
            print(f"  {rel}: floor {floor}, now {actual}", file=sys.stderr)
        print(
            "  If this was intentional, regenerate the baseline: `python scripts/check_doc_examples.py --update-floor`",
            file=sys.stderr,
        )
        return 1
    print(f"doctest-count floor OK ({len(baseline)} files)")
    return 0


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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-cli", action="store_true", help="skip the CLI flag check")
    ap.add_argument("--cli-only", action="store_true", help="only run the CLI flag check")
    ap.add_argument("--guide", default=None, help="limit to a single guide basename")
    ap.add_argument(
        "--check-floor",
        action="store_true",
        help="fail if any docs/source file's {doctest} block count drops below "
        "docs/doctest_counts.baseline.json (anti-regression gate)",
    )
    ap.add_argument(
        "--update-floor",
        action="store_true",
        help="rewrite docs/doctest_counts.baseline.json with current {doctest} block counts",
    )
    ap.add_argument(
        "--coverage",
        action="store_true",
        help="print per-file {doctest}/python/shell block counts (advisory, exit 0)",
    )
    args = ap.parse_args()

    # Standalone modes that don't need the package imported.
    if args.coverage:
        return run_coverage()
    if args.update_floor:
        return run_update_floor()
    if args.check_floor:
        return run_floor_check()

    import pyfsr  # local import so --help works without the package

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
    # Scan the guides plus the root README — its CLI/python blocks live outside
    # docs/source, so without this a renamed flag or invalid command in README
    # would never be linted.
    scan_files = sorted(glob.glob(os.path.join(GUIDES_DIR, "*.md")))
    _readme = os.path.join(REPO_ROOT, "README.md")
    if os.path.exists(_readme):
        scan_files.append(_readme)
    for g in scan_files:
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
