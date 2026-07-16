# pyfsr examples

Runnable scripts demonstrating the pyfsr SDK. Most talk to a **live FortiSOAR
appliance** (marked 🔌 below); a couple run fully offline (marked 💻).

## Setup

Most scripts read connection details from a `config.toml` in this directory.
Copy the template and fill in your appliance:

```bash
cp config.toml.example config.toml
$EDITOR config.toml
```

```toml
# config.toml
host = "https://your-fortisoar.example.com"
token = "your-api-key"          # or use username/password
verify_ssl = false              # lab appliances often use self-signed certs
```

A few of the newer scripts prefer environment variables via
`EnvConfig.from_env()` (`PYFSR_HOST`, `PYFSR_TOKEN`, …) — each script's
docstring states which it expects.

> ⚠️ Several scripts **create, publish, or delete** content (modules, playbooks,
> connectors, solution packs). Run them against a lab appliance, not production.

## Records & queries

| Script | What it shows | |
|---|---|---|
| [`list_alerts.py`](list_alerts.py) | Minimal "hello world" — list alerts | 🔌 |
| [`queries.py`](queries.py) | Guided tour of the Query DSL — see [querying guide](../docs/source/guides/querying.md) | 🔌 |
| [`upload_attachment_record.py`](upload_attachment_record.py) | Upload a file and link it to an attachment record | 🔌 |

## Connectors & solution packs

| Script | What it shows | |
|---|---|---|
| [`manage_connectors.py`](manage_connectors.py) | Full connector lifecycle via `client.connectors` / `client.agents` | 🔌 |
| [`ensure_connector_version.py`](ensure_connector_version.py) | Pin a connector version, preserving its configurations | 🔌 |
| [`repo_discover_and_download.py`](repo_discover_and_download.py) | Discover + download a connector from the public repo (no appliance) | 📦 |
| [`export_solution_pack.py`](export_solution_pack.py) | Export a solution pack to a file | 🔌 |
| [`solution_pack_lifecycle.py`](solution_pack_lifecycle.py) | Solution-pack install → status → uninstall | 🔌 |

## Modules & schema

| Script | What it shows | |
|---|---|---|
| [`all_field_types_module.py`](all_field_types_module.py) | Create a module exercising every supported field type | 🔌 |
| [`export_import_records.py`](export_import_records.py) | Round-trip a record through config **export → import**: filtered `export_record_data` → delete → `import_file` restores it (same uuid) | 🔌 |

## Playbooks

| Script | What it shows | |
|---|---|---|
| [`create_safe_playbook.py`](create_safe_playbook.py) | Create a harmless playbook collection and verify round-trip | 🔌 |
| [`deploy_playbook_from_yaml.py`](deploy_playbook_from_yaml.py) | Author a playbook in YAML and deploy it (uses [`playbooks/yaml_demo.yaml`](playbooks/yaml_demo.yaml)) | 🔌 |
| [`heist_tracker.py`](heist_tracker.py) | **Big end-to-end demo:** two linked modules → publish → RBAC grant → on-create YAML playbook ([`playbooks/heist_intake.yaml`](playbooks/heist_intake.yaml)) → a record that triggers it. Syntax in the [YAML reference](../docs/source/guides/playbook-yaml-reference.md). | 🔌 |
| [`playbook_structural_queries.py`](playbook_structural_queries.py) | Query playbooks by *shape* across all three tiers: server filter, server `aggregate()`, and the client-side `match()`/`match_across()` structural matcher (same-step precision, step quantities, parent↔child joins) | 🔌 |
| [`do_until_validation_loop.py`](do_until_validation_loop.py) | **Parent/child do-until loop:** a parent `workflow_reference` step re-runs a child playbook ([`playbooks/do_until_validation_demo.yaml`](playbooks/do_until_validation_demo.yaml)) until its Manual Input passes a jinja validation. Answers the prompt wrong a few times (loop re-prompts), then right (loop exits), and reads the child's output back via `vars.steps.<ref>.*`. | 🔌 |
| [`playbooks/version_lifecycle_demo.yaml`](playbooks/version_lifecycle_demo.yaml) | **Playbook snapshot ("Versions") lifecycle** — no driver script; it is exercised by [`tests/integration/test_playbook_versions_integration.py`](../tests/integration/test_playbook_versions_integration.py): run → snapshot v1 → edit → snapshot v2 → `list_versions` → run (output *differs*) → `diff_versions` → `restore_version` → run (output *reverts*). | 🔌 |

## Appliance administration CLI

| Script | What it shows | |
|---|---|---|
| [`appliance_cli_live_example.py`](appliance_cli_live_example.py) | Every `pyfsr appliance` command against a live box | 🔌 |
| [`appliance_cli_test_demo.py`](appliance_cli_test_demo.py) | Offline demonstration of the same commands | 💻 |

## FortiAI & FortiSIEM MCP integration

| Script | What it shows | |
|---|---|---|
| [`tune_new_instance.py`](tune_new_instance.py) | Apply the standard tuning every new FortiSOAR instance needs | 🔌 |
| [`connect_fortisiem_mcp.py`](connect_fortisiem_mcp.py) | Register FortiSIEM's MCP server with FortiAI and grant it to triage agents | 🔌 |
| [`trigger_ai_investigation.py`](trigger_ai_investigation.py) | Trigger a FortiAI investigation on an alert and print the verdict | 🔌 |
| [`investigate_fortisiem_incident.py`](investigate_fortisiem_incident.py) | End-to-end FortiSIEM incident investigation with tool-usage audit | 🔌 |
| [`test_fortisiem_mcp_evidence.py`](test_fortisiem_mcp_evidence.py) | Prove the FortiSIEM MCP binding by inspecting tool-usage evidence | 🔌 |
| [`validate_fortisiem_used.py`](validate_fortisiem_used.py) | Validate a FortiSIEM MCP tool was actually used in an investigation | 🔌 |
| [`siem_influence_trials.py`](siem_influence_trials.py) | Run N investigations on one alert and measure MCP influence | 🔌 |

## Data artifacts

`fortisiem_investigation_*.json`, `alert_investigation.txt`, and
`SIEM_MCP_INFLUENCE_REPORT.md` are captured outputs from the FortiAI/SIEM
examples above, kept as reference fixtures.
