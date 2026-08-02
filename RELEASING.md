# Releasing pyfsr

The published version comes **entirely from the git tag** (hatch-vcs, configured
in `pyproject.toml` under `[tool.hatch.version]`). There is no version string to
bump in any file -- `src/pyfsr/_version.py` is generated at build time and
`src/pyfsr/__init__.py` reads it. Tag, push, release.

## Branch protection: what `main` actually enforces

`main` is guarded by a repository **ruleset** named `protect-main` (rulesets, not
the older "branch protection" API -- `gh api repos/:owner/:repo/branches/main/protection`
returns 404 and that 404 is not a sign protection is off). It is `active` and it
enforces three things:

| Rule | Effect |
| --- | --- |
| `deletion` | `main` cannot be deleted. |
| `non_fast_forward` | `main` cannot be force-pushed. |
| `required_status_checks` | `lint`, `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `docs / docs` must be green. |

**Pull requests are deliberately not required.** There is no `pull_request` rule
in the ruleset. This is a single-maintainer repository and pushing straight to
`main` is a supported way to work -- that is the decision, and this section is
where it is recorded.

What makes that safe is that the six required checks now actually run on a push.
They previously did not: every one of those contexts is produced by
`pr-tests.yml`, which was triggered only by `pull_request`, so a direct push
could not produce a single one of them. GitHub reported this honestly as
`Bypassed 6 of 6 required status checks` -- the admin bypass (`RepositoryRole`
id 5, `bypass_mode: always`) was not a convenience being used, it was the only
reason the push was possible at all. `pr-tests.yml` now also triggers on
`push: branches: [main]`, so the matrix gates `main` directly and the bypass
stops being load-bearing.

Two related traps worth not re-introducing:

- **No `paths:` filters on the triggers of `pr-tests.yml`.** A required check
  that does not run never reports, and GitHub scores "not reported" as "not
  satisfied". A path filter on a required workflow makes docs-only changes
  permanently un-mergeable for anyone without bypass.
- **`docs.yml` has no `push` trigger.** It is invoked as `pr-tests.yml`'s `docs`
  job, which is what names the context `docs / docs`. A standalone push trigger
  would build the docs twice and report under `docs`, which is not the context
  the ruleset asks for.

If a second maintainer is ever added, revisit this: they will have no bypass, so
the checks must pass on their pushes -- which, after the change above, they will.

## Cut a release

```sh
make release-check VERSION=0.18.9   # preflight only, changes nothing
make release VERSION=0.18.9         # preflight, tag, release, verify
```

Both call `scripts/release.sh`. The preflight refuses to tag unless: the version
is PEP 440; `gh` is authenticated; an installability prober (`python3 -m pip` or
`uv pip`) is resolvable *now* rather than at the final wait; the tree is clean;
you are on `main` and in sync with `origin/main`; the tag is free locally and on
origin; `CHANGELOG.md` has a non-empty `## [VERSION]` section (the release notes
are cut from it); PyPI does not already have the version; and every check run on
the exact commit being tagged is green.

That last check only became meaningful once `pr-tests.yml` started running on
pushes to `main` (see above). Before that, a direct push produced almost no
check runs, so "all check runs green" was nearly vacuous.

It then tags, creates the GitHub release, and waits twice: first for the PyPI
JSON API to report the version, then for it to be genuinely resolvable from the
simple index. The two waits are separate on purpose -- the JSON API learns about
a version before the index serves it, so polling only the JSON API declares
success while `pip install` still fails.

> **Not yet proven end-to-end.** 0.18.8 shipped, but that run ended in a
> false-negative timeout from a broken prober (fixed in `abf03f74`). The
> tag → release → double-wait sequence has never completed cleanly in one go.
> Treat the next release as its first real exercise, not as a proven path.

The `Publish Python Package` workflow (`.github/workflows/publish.yml`) fires on
the created release, runs the full test matrix via `pr-tests.yml`, builds the
wheel + sdist (`fetch-depth: 0` so hatch-vcs sees the tag), and uploads to PyPI
via Trusted Publishing (OIDC -- no stored token). The upload is gated on that
matrix regardless of how the commit reached `main`.

- The version is derived from the tag: `v0.18.9` → `0.18.9` (the `v` is stripped).
- PyPI rejects re-uploading an existing version, so each release needs a fresh,
  higher tag. Check `https://pypi.org/project/pyfsr/#history` for the current
  latest.
- A source checkout that was never built reports `__version__ = "0.0.0+unknown"`;
  an installed build (wheel or editable) reports the real version.

## Note: PyPI page caching

The PyPI **project page header** can lag behind the actual latest release by a
few minutes (CDN cache). The source of truth is the JSON API:

```sh
curl -s https://pypi.org/pypi/pyfsr/json | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"
```

or hit the version page directly: `https://pypi.org/project/pyfsr/<version>/`.
If the header shows an older version right after a release, hard-refresh -- the
release is already live.
