# Releasing pyfsr

The published version comes **entirely from the git tag** (hatch-vcs, configured
in `pyproject.toml` under `[tool.hatch.version]`). There is no version string to
bump in any file — `src/pyfsr/_version.py` is generated at build time and
`src/pyfsr/__init__.py` reads it. Tag, push, release.

## Cut a release

```sh
git tag v0.7.9            # next free version > PyPI's latest
git push origin v0.7.9
```

Then on GitHub: **Releases → Draft a new release → choose tag `v0.7.9` →
Publish**. The `Publish Python Package` workflow
(`.github/workflows/publish.yml`) fires on the created release, runs the test
suite, builds the wheel + sdist (`fetch-depth: 0` so hatch-vcs sees the tag),
and uploads to PyPI via Trusted Publishing (OIDC — no stored token).

- The version is derived from the tag: `v0.7.9` → `0.7.9` (the `v` is stripped).
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
If the header shows an older version right after a release, hard-refresh — the
release is already live.
