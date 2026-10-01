# Contributed playbooks

Symlinks to FortiSOAR playbook YAMLs developed across multiple projects.
These are real-world playbooks, not library examples -- they cover specific
use cases, demos, repros, and framework collections.

## Structure

| Directory | Source | Count | Description |
|-----------|--------|-------|-------------|
| `fsrpb/` | fsr-playbook-framework/examples/ | 32 | Compiler demos, test playbooks, recipes, and the all-step-types capstone |
| `uc/` | Miscellaneous/fortisoar/demos/uc*/ | 13 | Use-case playbooks (UC-01 through UC-14: phishing, triage, isolation, blocking, etc.) |
| `demos/` | Miscellaneous/fortisoar/demos/ | 6 | Demo playbooks (FortiEDR, FortiSOC, vuln management, etc.) |
| `fortisoar/` | Miscellaneous/fortisoar/ | 13 | Standalone playbooks, repros, research, troubleshooting |
| `ztpf2/` | Miscellaneous/fortisoar/ztpf2/ | 17 | ZTPF2 framework playbooks (buttons, triggers, functions, proxy API, etc.) |

**Total: 81 playbooks**

All symlinks point to tracked source files. Sensitive content (lab IPs,
capture dates) has been scrubbed from the source files per public-repo
hygiene rules.
