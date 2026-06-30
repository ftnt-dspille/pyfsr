# Foundational playbook library

The worked-examples layer: complete, compiling, use-case-shaped FortiSOAR playbooks
an agent retrieves by intent and adapts. Distinct from the per-step `step-help`
reference (one atom) and from the tutorial corpus (the Jinja cookbook) — this is
the *use-case cookbook* (whole molecules).

Browse it offline:
```bash
pyfsr playbook examples                 # list every playbook + intent + step types
pyfsr playbook examples --intent incident
pyfsr playbook show <slug>              # metadata + full friendly YAML
pyfsr playbook examples --manifest      # retrieval JSON (NL->playbook payload)
```

**27 playbooks** across 6 SOC stages. Every playbook compiles
(`cold*` = compiles but needs `--refresh-catalog` to resolve connector names on a
live box). Each carries a `goal`/`trigger`/`inputs`/`outputs`/`connectors`/
`adapts-to` front-matter block. Source: `authored` (written for the library) or
`tutorial-corpus:<name>` (promoted + cleaned from the 30 tutorial playbooks).

## Triggers (5)

| slug | intent | step types | source |
|---|---|---|---|
| [`api-webhook-intake`](triggers/api-webhook-intake.yaml) | API/webhook intake | insert_record,set_variable,CyopsUtilites,decision,set_api_keys… | tutorial-corpus |
| [`on-create-count-bad-indicators`](triggers/on-create-count-bad-indicators.yaml) | On-create alert trigger | decision,insert_record,set_variable,find_record,start… | tutorial-corpus |
| [`scheduled-daily-recon`](triggers/scheduled-daily-recon.yaml) | Scheduled daily recon | insert_record,connector,set_variable,decision,find_record… | tutorial-corpus |
| [`scheduled-hunt-summarize-notify`](triggers/scheduled-hunt-summarize-notify.yaml) | Scheduled hunt | start,set_variable,find_record,insert_record,CyopsUtilites | authored |
| [`threat-feed-ingestion`](triggers/threat-feed-ingestion.yaml) | Threat-feed ingestion | set_variable,insert_record,connector,decision,set_api_keys… | tutorial-corpus |

## Enrichment (4)

| slug | intent | step types | source |
|---|---|---|---|
| [`ad-lookup-by-email`](enrichment/ad-lookup-by-email.yaml) | Enrich a record with Active Directory attributes looked up by email; r | set_variable,CyopsUtilites,connector,start | tutorial-corpus |
| [`enrich-indicator-extract-artifacts-rollup`](enrichment/enrich-indicator-extract-artifacts-rollup.yaml) | Connector-light indicator enrichment | start,set_variable,connector,CyopsUtilites | authored |
| [`fortisiem-ip-context-enrichment`](enrichment/fortisiem-ip-context-enrichment.yaml) | Enrich an indicator with FortiSIEM IP context, comment, optional email | insert_record,connector,set_variable,CyopsUtilites,manual_input… | tutorial-corpus |
| [`multi-source-ip-reputation-rollup`](enrichment/multi-source-ip-reputation-rollup.yaml) | Multi-source IP reputation rollup (VirusTotal/IPStack/IPInfo/AbuseIPDB | ApprovalManualInput,manual_input,connector,CyopsUtilites,set_variable… | tutorial-corpus |

## Decision (3)

| slug | intent | step types | source |
|---|---|---|---|
| [`correlate-cve-to-alert`](decision/correlate-cve-to-alert.yaml) | Correlate a CVE record with its associated alert by finding and cross- | start,update_record,CyopsUtilites,cybersponse.action,set_variable… | tutorial-corpus |
| [`false-positive-suppression-autoclose`](decision/false-positive-suppression-autoclose.yaml) | Decide whether an alert matches a known false-positive signature; if s | start_on_create,set_variable,decision,update_record,insert_record… | authored |
| [`severity-scoring-from-enrichment`](decision/severity-scoring-from-enrichment.yaml) | Weighted severity scoring from enrichment fields feeding a decision st | start,set_variable,code_snippet,decision,update_record | authored |

## Action (8)

| slug | intent | step types | source |
|---|---|---|---|
| [`ai-enrichment-openai`](action/ai-enrichment-openai.yaml) | AI enrichment | insert_record,update_record,CyopsUtilites,set_variable,start… | tutorial-corpus |
| [`change-incident-lead-critical`](action/change-incident-lead-critical.yaml) | Change the incident lead for critical incidents (the child of the pair | CyopsUtilites,start,update_record | tutorial-corpus |
| [`close-fortisiem-incident`](action/close-fortisiem-incident.yaml) | Close a FortiSIEM incident with a reason, comment the alert, and updat | insert_record,connector,set_variable,CyopsUtilites,start… | tutorial-corpus |
| [`connector-error-handling-branch`](action/connector-error-handling-branch.yaml) | Call a connector step with ignore_errors, then branch on the step's ru | start_on_create,set_variable,connector,decision,insert_record… | authored |
| [`create-incident-from-alert`](action/create-incident-from-alert.yaml) | Create incident from alert | start_on_create,set_variable,create_record,update_record,CyopsUtilites | authored |
| [`host-containment-isolate`](action/host-containment-isolate.yaml) | Host containment | start,set_variable,connector,update_record,SendMail… | authored |
| [`parent-calls-child-workflow-reference`](action/parent-calls-child-workflow-reference.yaml) | Parent playbook calls a child synchronously via workflow_reference and | workflow_reference,find_record,set_variable,start | tutorial-corpus |
| [`quarantine-ip-fortigate`](action/quarantine-ip-fortigate.yaml) | Quarantine a malicious IP on FortiGate behind an optional approval gat | insert_record,connector,ApprovalManualInput,CyopsUtilites,set_variable… | tutorial-corpus |

## Notify (4)

| slug | intent | step types | source |
|---|---|---|---|
| [`audit-run-stamp-wrapper`](notify/audit-run-stamp-wrapper.yaml) | Audit run-stamp | start,set_variable,create_record,CyopsUtilites | authored |
| [`incident-detail-comment`](notify/incident-detail-comment.yaml) | Add a comment to an incident with its first/last seen timestamps and i | insert_record,connector,set_variable,CyopsUtilites,start_on_create | tutorial-corpus |
| [`multi-system-provisioning`](notify/multi-system-provisioning.yaml) | Provision a new employee across AD, Linux, FortiGate and send a confir | start,insert_record,decision,connector,set_variable… | tutorial-corpus |
| [`sla-escalation-timer`](notify/sla-escalation-timer.yaml) | SLA escalation | start_on_create,set_variable,decision,update_record,manual_input | authored |

## Control (3)

| slug | intent | step types | source |
|---|---|---|---|
| [`async-fanout-collect-child-runs`](control/async-fanout-collect-child-runs.yaml) | Async fan-out + collect | start,set_variable,workflow_reference,code_snippet,end | authored |
| [`loop-over-records-max-parallel`](control/loop-over-records-max-parallel.yaml) | Loop with max-parallel | start,set_variable,workflow_reference,code_snippet,end | authored |
| [`multi-stage-approval-audit-trail`](control/multi-stage-approval-audit-trail.yaml) | Multi-stage approval with audit trail | start,ApprovalManualInput,set_variable,update_record,CyopsUtilites | authored |

## Generation

This index and `manifest.json` are generated from the playbooks themselves
(compile + facet extraction via `pyfsr.playbook_catalog`). Regenerate after
adding/editing a playbook: `pyfsr playbook examples --manifest >
examples/playbooks/library/manifest.json` (or run `scratch/promote_library.py`).
