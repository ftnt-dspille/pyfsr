# pyfsr examples

Runnable scripts demonstrating the pyfsr SDK. Each filename is
self-describing: `<domain>_<action>_<detail>.py`. Most scripts talk to a
**live FortiSOAR appliance** (marked **live**); a few run fully offline
(marked **offline**).

## Quick finder -- "I want to..."

| If you want to... | Start here |
|---|---|
| ...create and list alerts (hello world) | [`create_and_list_alerts.py`](create_and_list_alerts.py) |
| ...query records with the fluent DSL | [`query_dsl_tour.py`](query_dsl_tour.py) |
| ...upload a file attachment | [`upload_attachment_record.py`](upload_attachment_record.py) |
| ...create a module / fields | [`module_create_all_field_types.py`](module_create_all_field_types.py) |
| ...inspect a module's schema | [`describe_module.py`](describe_module.py) |
| ...build a full app (module + playbook + RBAC) | [`e2e_module_playbook_demo.py`](e2e_module_playbook_demo.py) |
| ...deploy a playbook from YAML | [`deploy_playbook_from_yaml.py`](deploy_playbook_from_yaml.py) |
| ...trigger a playbook and wait for it | [`playbook_api_smoke_test.py`](playbook_api_smoke_test.py) |
| ...search playbooks by shape (steps, joins) | [`playbook_query_by_shape.py`](playbook_query_by_shape.py) |
| ...drive a do-until loop with Manual Input | [`playbook_do_until_loop.py`](playbook_do_until_loop.py) |
| ...manage connectors / agents | [`manage_connectors.py`](manage_connectors.py) |
| ...pin a connector to a version | [`ensure_connector_version.py`](ensure_connector_version.py) |
| ...download a connector from the repo | [`connector_repo_download.py`](connector_repo_download.py) |
| ...export / install / build solution packs | [`solution_pack_lifecycle.py`](solution_pack_lifecycle.py) |
| ...run a FortiAI investigation on an alert | [`trigger_ai_investigation.py`](trigger_ai_investigation.py) |
| ...call a single FortiAI agent | [`run_single_ai_agent.py`](run_single_ai_agent.py) |
| ...use FortiSOAR's native MCP gateway | [`native_mcp_gateway.py`](native_mcp_gateway.py) |
| ...register an external MCP server | [`register_and_call_public_mcp_server.py`](register_and_call_public_mcp_server.py) |
| ...wire FortiSIEM into FortiAI via MCP | [`fortisiem_mcp_setup_and_test.py`](fortisiem_mcp_setup_and_test.py) |
| ...tune a brand-new appliance | [`tune_new_instance.py`](tune_new_instance.py) |
| ...run appliance CLI commands (service/mq/db) | [`appliance_cli_tour.py`](appliance_cli_tour.py) |
| ...serve a TAXII threat feed to FortiGate | [`taxii_threat_feed_to_fortigate.py`](taxii_threat_feed_to_fortigate.py) |
| ...mint an API key + provision agents | [`agent_provisioning_matrix.py`](agent_provisioning_matrix.py) |
| ...create a user, set and change passwords | [`user_password_lifecycle.py`](user_password_lifecycle.py) |

## Setup

Most scripts read connection details from a `config.toml` in this directory.
Copy the template and fill in your appliance:

```bash
cp config.toml.example config.toml
$EDITOR config.toml
```

```toml
# config.toml
[fortisoar]
base_url = "https://your-fortisoar.example.com"
verify_ssl = false              # lab appliances often use self-signed certs

[fortisoar.auth]
type = "api_key"                # or "user_pass" with username/password
key = "your-api-key"
```

A few of the newer scripts prefer environment variables via
`EnvConfig.from_env()` (`PYFSR_HOST`, `PYFSR_TOKEN`, ...) -- each script's
docstring states which it expects.

> WARNING: Several scripts **create, publish, or delete** content (modules,
> playbooks, connectors, solution packs). Run them against a lab appliance,
> not production.

---

## Records & queries

| Script | What it shows | |
|---|---|---|
| [`create_and_list_alerts.py`](create_and_list_alerts.py) | Minimal "hello world" -- create an alert (picklist auto-resolution) then list alerts via the typed records surface | live |
| [`query_dsl_tour.py`](query_dsl_tour.py) | Guided tour of the Query DSL -- see [querying guide](../docs/source/guides/querying.md) | live |
| [`upload_attachment_record.py`](upload_attachment_record.py) | Upload a file and link it to an attachment record | live |

## Modules & schema

| Script | What it shows | |
|---|---|---|
| [`module_create_all_field_types.py`](module_create_all_field_types.py) | Create a module exercising every supported field type | live |
| [`describe_module.py`](describe_module.py) | Pretty-print a module's fields with type, required-ness, and conditions -- offline tour (synthetic record) + live tour (`--module`, `--staging`) | offline / live |
| [`export_import_records.py`](export_import_records.py) | Round-trip a record through config **export -> import**: filtered `export_record_data` -> delete -> `import_file` restores it (same uuid) | live |
| [`module_export_import_wizard.py`](module_export_import_wizard.py) | Export Wizard -> Import Wizard round-trip for a custom module (create -> export .zip -> drop-orphan-tables -> re-import) | live |

## Playbooks

| Script | What it shows | |
|---|---|---|
| [`create_safe_playbook.py`](create_safe_playbook.py) | Create a harmless playbook collection and verify round-trip | live |
| [`deploy_playbook_from_yaml.py`](deploy_playbook_from_yaml.py) | Author a playbook in YAML and deploy it (uses [`playbooks/yaml_demo.yaml`](playbooks/yaml_demo.yaml)) | live |
| [`e2e_module_playbook_demo.py`](e2e_module_playbook_demo.py) | **Big end-to-end demo:** two linked modules -> publish -> RBAC grant -> on-create YAML playbook ([`playbooks/heist_intake.yaml`](playbooks/heist_intake.yaml)) -> a record that triggers it. Syntax in the [YAML reference](../docs/source/guides/playbook-yaml-reference.md). | live |
| [`playbook_query_by_shape.py`](playbook_query_by_shape.py) | Query playbooks by *shape* across all three tiers: server filter, server `aggregate()`, and the client-side `match()`/`match_across()` structural matcher (same-step precision, step quantities, parent<->child joins) | live |
| [`playbook_api_smoke_test.py`](playbook_api_smoke_test.py) | Live smoke test for the whole `client.playbooks` surface -- exercises every method against a real appliance and prints a summary | live |
| [`playbook_do_until_loop.py`](playbook_do_until_loop.py) | **Parent/child do-until loop:** a parent `workflow_reference` step re-runs a child playbook ([`playbooks/do_until_validation_demo.yaml`](playbooks/do_until_validation_demo.yaml)) until its Manual Input passes a jinja validation. Answers the prompt wrong a few times (loop re-prompts), then right (loop exits), and reads the child's output back via `vars.steps.<ref>.*`. | live |
| [`playbooks/version_lifecycle_demo.yaml`](playbooks/version_lifecycle_demo.yaml) | **Playbook snapshot ("Versions") lifecycle** -- no driver script; it is exercised by [`tests/integration/test_playbook_versions_integration.py`](../tests/integration/test_playbook_versions_integration.py): run -> snapshot v1 -> edit -> snapshot v2 -> `list_versions` -> run (output *differs*) -> `diff_versions` -> `restore_version` -> run (output *reverts*). | live |

## Connectors & solution packs

| Script | What it shows | |
|---|---|---|
| [`manage_connectors.py`](manage_connectors.py) | Full connector lifecycle via `client.connectors` / `client.agents` | live |
| [`ensure_connector_version.py`](ensure_connector_version.py) | Pin a connector version, preserving its configurations | live |
| [`connector_repo_download.py`](connector_repo_download.py) | Discover + download a connector from the public repo (no appliance) | repo |
| [`solution_pack_export.py`](solution_pack_export.py) | Export a solution pack to a file | live |
| [`solution_pack_lifecycle.py`](solution_pack_lifecycle.py) | Solution-pack install -> status -> uninstall | live |
| [`solution_pack_full_lifecycle.py`](solution_pack_full_lifecycle.py) | Author, export, and reinstall a solution pack -- the full lifecycle (builder -> upload -> 503-tolerant poll) | live |

## Appliance administration

| Script | What it shows | |
|---|---|---|
| [`appliance_cli_tour.py`](appliance_cli_tour.py) | Every `pyfsr appliance` command against a live box (service/mq/logs/db) | live |
| [`tune_new_instance.py`](tune_new_instance.py) | Apply the standard tuning every new FortiSOAR instance needs | live |
| [`agent_provisioning_matrix.py`](agent_provisioning_matrix.py) | Mint an API key, verify agent registration over the message bus, and prove the round trip (install connector on remote agent, execute an op on its host) | live |
| [`user_password_lifecycle.py`](user_password_lifecycle.py) | Admin creates a user and sets its password, the user logs in and changes its own password (`users.reset_password` / `whoami` / `change_password`) | live |

## FortiAI, MCP & threat feeds

| Script | What it shows | |
|---|---|---|
| [`trigger_ai_investigation.py`](trigger_ai_investigation.py) | Trigger a FortiAI investigation on an alert and print the verdict | live |
| [`run_single_ai_agent.py`](run_single_ai_agent.py) | Run ONE FortiAI agent directly -- read its declared `inputformat`, call it with validation, print answer/evidence/confidence | live |
| [`probe_fortiai_agents.py`](probe_fortiai_agents.py) | Discover the real inputformat of every FortiAI agent on the box | live |
| [`native_mcp_gateway.py`](native_mcp_gateway.py) | Call the appliance's own native `/mcp/*` gateway directly via `client.mcp` (list + trigger soc/playbooks/modules/utility tools) | live |
| [`register_and_call_public_mcp_server.py`](register_and_call_public_mcp_server.py) | Register an *external* MCP server (public DeepWiki) with FortiSOAR and call its tools | live |
| [`fortisiem_mcp_setup_and_test.py`](fortisiem_mcp_setup_and_test.py) | FortiSIEM <-> FortiAI MCP: one-file setup + test -- register the FortiSIEM MCP server, grant it to triage agents, run an investigation, and inspect tool-usage evidence | live |
| [`taxii_threat_feed_to_fortigate.py`](taxii_threat_feed_to_fortigate.py) | Stand up FortiSOAR's native TAXII 2.1 server as a live threat feed a FortiGate can pull (enable TAXII -> API-key binding -> dataset collection) | live |

## Data artifacts

`alert_investigation.txt` is captured output from `trigger_ai_investigation.py`,
kept as a reference fixture. `sample_csv.csv` is a tiny test file used by
`upload_attachment_record.py`.
