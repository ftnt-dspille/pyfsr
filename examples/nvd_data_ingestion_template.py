#!/usr/bin/env python
"""Generate, check and (optionally) run a data-ingestion playbook for the NVD CVE API.

Shows ``pyfsr.authoring.data_ingestion_template`` end to end against a real,
keyless public API, so it runs without any vendor account:

    1. generate the Init Macros / Fetch / Ingest collection. The Fetch step is a
       Utilities > Make REST API Call stand-in against
       https://services.nvd.nist.gov/rest/json/cves/2.0, windowed on
       lastModStartDate/lastModEndDate (the watermark), one alert per CVE keyed
       on the CVE id, severity from the CVSS v3.1 base severity;
    2. write the YAML next to this file (the YAML is the source of truth: edit
       it, not the compiled JSON);
    3. compile it offline and run the data-ingest best-practice checks;
    4. with --deploy: import the collection, run "NVD > Ingest" once, wait for
       it, and count the alerts it wrote.

Offline (default)::

    .venv/bin/python examples/nvd_data_ingestion_template.py

Live (credentials from the environment, never this file)::

    FSR_BASE_URL=fortisoar.example.com FSR_PASSWORD='<your-password>' \\
      .venv/bin/python examples/nvd_data_ingestion_template.py --deploy

Data Ingestion Wizard note: the wizard lists ingestion collections per
INSTALLED connector. ``nvd-cve`` is only a tag here (the fetch runs through the
Utilities connector), so the wizard will not offer this collection; schedule
"NVD > Ingest" directly. Once a real connector exists, swap the "Fetch" step
for its action and tag the collection with its name, and the wizard picks it up.

NVD rate limits keyless clients (5 requests / 30 s) and caps a lastMod window at
120 days; the default 7-day backfill and a schedule of every 15 minutes or
slower stay well inside both. The Fetch playbook pages: the first request
reports totalResults, the remaining pages are read in one for_each. A window
whose total exceeds Maximum Records Per Run (10000, i.e. 5 pages: NVD's whole
keyless budget) fails the run rather than skip CVEs. With an NVD API key
(header ``apiKey``, 50 requests / 30 s) the cap can go higher.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pyfsr import Query
from pyfsr.authoring import data_ingestion_template, format_diagnostic

OUT = Path(__file__).parent / "playbooks" / "nvd_data_ingestion.yaml"
INGEST = "NVD > Ingest"

SPEC = {
    "slug": "nvd-cve",
    "label": "NVD",
    "url": "https://services.nvd.nist.gov/rest/json/cves/2.0",
    "query_params": {
        "lastModStartDate": "{{ vars.window_from }}",
        "lastModEndDate": "{{ vars.curr_timestamp }}",
    },
    # NVD pages with startIndex/resultsPerPage and reports totalResults on every page.
    "page_param": "startIndex",
    "page_size_param": "resultsPerPage",
    "page_size": 2000,
    "total_path": "totalResults",
    # NVD wants ISO-8601 with milliseconds and a zone.
    "time_format": "YYYY-MM-DDTHH:mm:ss.SSS[Z]",
    "records_path": "vulnerabilities",
    "source_id_field": "cve.id",
    "name_template": "{{ vars.item.cve.id }}: {{ (vars.item.cve.descriptions[0].value "
    "if vars.item.cve.descriptions else 'no description') | truncate(200) }}",
    "description_template": "{{ (vars.item.cve.descriptions[0].value if vars.item.cve.descriptions else '') | e }}",
    # Not every CVE has a v3.1 score yet; missing -> default_severity.
    "severity_expr": "((vars.item.cve.metrics.cvssMetricV31 | default([{}]))[0].cvssData | default({})).baseSeverity",
    "severity_map": {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"},
    "default_severity": "Minimal",
    "backfill_days": 7,
    # 5 pages of 2000: also NVD's keyless budget (5 requests / 30 s) for one run.
    "max_items": 10000,
    "sample_record": {
        "cve": {
            "id": "CVE-2026-0001",
            "published": "2026-01-01T00:00:00.000",
            "lastModified": "2026-01-02T00:00:00.000",
            "descriptions": [{"lang": "en", "value": "Sample CVE for the Data Mapping preview."}],
            "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]},
        }
    },
}


def _client(args):
    from pyfsr import FortiSOAR

    host = args.host or os.environ.get("FSR_BASE_URL")
    password = os.environ.get("FSR_PASSWORD")
    if not (host and password):
        raise SystemExit("set FSR_BASE_URL and FSR_PASSWORD (FSR_USERNAME defaults to csadmin)")
    return FortiSOAR(
        host,
        auth=(os.environ.get("FSR_USERNAME", "csadmin"), password),
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deploy", action="store_true", help="import, run Ingest once, count alerts")
    ap.add_argument("--host", help="appliance host (FSR_BASE_URL)")
    ap.add_argument("--out", type=Path, default=OUT, help=f"where to write the YAML (default {OUT})")
    args = ap.parse_args()

    # 1-2. generate and write the YAML
    yaml_text = data_ingestion_template(**SPEC)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml_text, encoding="utf-8")
    print("wrote              :", args.out)

    # 3. compile offline + best-practice checks
    compiled = data_ingestion_template(**SPEC, compile=True)
    print("compile ok         :", compiled.ok)
    print("playbooks          :", compiled.playbook_names)
    for diag in compiled.errors:
        print("diagnostic         :", format_diagnostic(diag))
    try:
        from fsr_playbooks.compiler import rulesets
    except ImportError:
        issues = []
    else:
        issues = rulesets.validate(compiled.fsr_json, ["data-ingest"])
    for i in issues:
        print(f"ruleset {i.severity:5}     : {i.rule_id}: {i.message}")
    if not compiled.ok or any(i.severity == "fail" for i in issues):
        return 1
    if not args.deploy:
        print("\n(offline only -- pass --deploy to import and run it)")
        return 0

    # 4. deploy and run once
    client = _client(args)
    created = client.workflow_collections.import_from_yaml(str(args.out), replace=True)
    print("imported           :", [c.get("name") for c in created])
    nvd = Query().eq("source", "NVD")
    before = client.records("alerts").count(nvd)
    run = client.playbooks.trigger(INGEST, follow=True, timeout=600)
    print("run                :", run.get("status"), run.get("error_message") or "")
    print("NVD alerts         :", before, "->", client.records("alerts").count(nvd))
    return 0 if run.get("status") == "finished" else 1


if __name__ == "__main__":
    sys.exit(main())
