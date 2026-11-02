# Contributing

## Documentation examples

pyfsr docs live in `docs/source/` as Markdown (rendered by Sphinx + MyST). Every
code example falls into one of three tiers — pick the tier by what the example
needs to prove, not by habit. The goal is simple: **examples that show output
must be executed and output-matched in CI**, so they cannot silently drift from
the code.

### 1. `{doctest}` — return-shape examples (executed + output-matched)

Use when the example shows **what a call returns**. The block runs under
`make doctest` and CI fails if the shown output drifts from the real return
shape.

- Start from the global fixtures pre-imported by `doctest_global_setup` in
  `conf.py`: `demo_client()` (a `FortiSOAR` over a replay REST session) and
  `demo_box()` (a healthy `Appliance` over a replay transport). Neither touches
  the network.
- Match the exact output. Mask volatile fields (UUIDs, timestamps, sizes that
  vary run-to-run) with `# doctest: +ELLIPSIS` and add a short comment saying
  what varies.
- Reserve `# doctest: +SKIP` for calls that genuinely need a live box or SSH and
  cannot be replayed — and say why in a comment.

```{doctest}
>>> client = demo_client()
>>> conn = client.connectors
>>> [c.name for c in conn.list_configured()[:3]]   # doctest: +ELLIPSIS
['smtp', 'code-snippet', ...]
>>> conn.resolve_version("mitre-attack")
'2.0.2'
```

### 2. `{code-block} python` — illustrative / copy-paste (linted, not run)

Use for examples that need a live appliance to run — write ops, `dev_publish`,
`install`, anything mutating state — and so cannot be replayed. Show the expected
shape as an inline `#` comment, never as matched output. `scripts/check_doc_examples.py`
lints that every `from pyfsr... import` and `pyfsr.<attr>` resolves, so a rename
cannot silently rot the snippet — but it does **not** execute the body.

```python
conn.install("fortinet-fortisiem", "6.1.0", wait=True)   # by name from Content Hub
conn.install_from_file("hello-world-1.0.0.tgz", replace=True)
```

### 3. `{code-block} sh` — CLI commands (flags linted; offline ones executed)

`check_doc_examples.py` checks every `--flag` against the real `pyfsr --help`.
Offline-runnable commands (`pyfsr playbook ...`) are additionally executed
end-to-end by `scripts/exec_cli_examples.py` with exit-code + stdout
assertions. Live-only commands (`pyfsr appliance ...`) are documented with their
return shape as `{doctest}` blocks in `appliance-cli.md` instead.

### Adding a new return-shape example

1. **If a `/api/3` capture is missing**, record it: extend
   `scripts/capture_responses.py`, run it against a lab box (creds via
   `tests/config.toml`), then trim the raw JSON from
   `tests/resources/mock_responses/` into a `*_RESPONSE` constant in
   `src/pyfsr/_testing/client_captures.py` and register it in `_FIXTURES` in
   `src/pyfsr/_testing/replay_http.py` (extend `_path_and_match` if the path has
   a volatile segment to collapse). Appliance-CLI examples use
   `scripts/capture_appliance_fixtures.py` + `appliance_captures.py` instead.
2. **Write the `{doctest}` block** with `demo_client()` / `demo_box()`.
3. **Run** `cd docs && make doctest && make check-examples`; both must be green.

Captures must use placeholder hosts/credentials only — never real lab IPs, host
names, passwords, or capture dates in tracked source. Say "captured from a live
8.0 appliance" generically; keep box details in gitignored locations. The
existing `CAPTURE_HOST = "fortisoar.example.com"` convention is the model.

### Validation commands

- `make doctest` — execute + output-match every `{doctest}` block.
- `make check-examples` — lint symbols/flags in plain blocks, run offline CLI
  commands, and enforce the doctest-count floor so a `{doctest}` cannot quietly
  be replaced by a plain `{code-block}`.
- `make html -W -n` — strict warnings-as-errors build (xrefs must resolve).
- `pytest tests/unit/test_doc_examples.py` — every `>>>` block in docstrings and
  `index.rst` resolves to a real symbol.

Run `python scripts/check_doc_examples.py --coverage` for a per-file
`{doctest}` / `python` / `shell` block-count report — it shows where doctest
coverage exists and where plain-block gaps remain.
