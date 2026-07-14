# Connector RPM rebuild

Rebuilds a FortiSOAR connector RPM at a chosen version so the appliance
installs the version the mirror **advertises** through the normal
yum/content-hub path — instead of leaving the real upstream package installed
while the catalog claims a different version.

## Why this exists

A FortiSOAR connector RPM (`cyops-connector-<name>`) is a thin wrapper: its
only payload is `/opt/cyops-connector-<name>/<name>.tgz`, and its `%post`
scriptlet hands that tgz to the integrations framework
(`manage.py connectors`). A connector `.tgz` on its own is **not** installable
through yum — it goes through FortiSOAR's separate "import connector" flow and
never registers as a yum-managed package. To close the loop through the mirror
we must produce an actual **RPM**.

## Generic across connectors

Every `cyops-connector-*` RPM has the identical shape, so one spec template
(`cyops-connector.spec.in`) + `build.sh` rebuilds **any** connector — not just
http. The version bump stays in lockstep across the three places that matter:

1. `<name>/info.json` `version` inside the tgz
2. the RPM `Version`
3. the `%post` `mod_version` (which derives the `<name>_<v_v_v>` extract dir)

## Usage

```sh
# put the upstream connector tgz under src/ (pull from an appliance:
#   scp <appliance>:/opt/cyops-connector-<name>/<name>.tgz src/<name>-<oldver>.tgz )
./build.sh <name> <new_version> [release] [src_tgz]

# example — rebrand the http connector as 2.1.0:
./build.sh http 2.1.0 1 src/http-1.0.0.tgz
# -> dist/cyops-connector-http-2.1.0-1.x86_64.rpm
```

`build.sh` runs `rpmbuild` inside a throwaway `centos:7` container
(`--platform linux/amd64`), so no host rpm toolchain is needed. `src/` and
`dist/` are gitignored — vendor tarballs and built RPMs stay local.

## Serving it from the mirror

Drop the built RPM into the mirror's local connector repo and index it:

```sh
mkdir -p ../connectors-local/x86_64
cp dist/cyops-connector-<name>-<ver>-*.x86_64.rpm ../connectors-local/x86_64/
createrepo_c ../connectors-local/x86_64/
```

The appliance's `fsr-mirror-connectors-override` repo (priority=1, wired by
`setup-appliance.sh`) then installs this build over Fortinet's:

```sh
yum clean all && yum -y install cyops-connector-<name>   # or: upgrade
```
