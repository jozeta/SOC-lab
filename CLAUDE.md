# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A blue-team SOC lab built on macOS with **Multipass + cloud-init**: a Samba4 AD domain
(`LAB.LOCAL`), Wazuh SIEM, Suricata/Zeek sensor, Ansible control node, file server, N
domain-joined clients, attacker and vulnerable-target boxes, plus add-ons (LDAP Account
Manager, MITRE Caldera, Velociraptor, Semaphore + Uptime Kuma). Ubuntu VMs only: no Windows.

## Commands

```sh
brew install bash multipass jq
/opt/homebrew/bin/bash ./deploy-soc-lab.sh     # needs bash 4+, NOT macOS's /bin/bash 3.2
./teardown-soc-lab.sh
multipass list
multipass exec <vm> -- sudo cloud-init status --long
```

Everything with `NUM_CLIENTS=2` is about 29 vCPU / 39 GB RAM allocated. Trim with `VM_ENABLED` and
`NUM_CLIENTS` at the top of the deploy script. There are no automated tests; validate shell
changes with `bash -n` and `shellcheck`, and template changes by deploying the affected VM.

## How it works

- `deploy-soc-lab.sh` holds all config (domain, `ADMIN_PASS`, `NUM_CLIENTS`, `CLIENT_SPEC`,
  `VM_ENABLED`, per-VM specs). It renders each `cloud-init-templates/<vm>.yaml.tmpl` (placeholder
  substitution), launches VMs in dependency order (`ansible-ctrl` → `dc01` → `siem` → rest in
  parallel), polls `cloud-init status` at each dependency point, then pushes a generated Ansible
  inventory to `ansible-ctrl`.
- `client.yaml.tmpl` is rendered once per client (`client01`…`clientNN`, zero-padded).
- Domain-joined and monitored VMs run Wazuh agents shipping to `siem`, so the pipeline is
  attack (`redteam`/`vulntarget`) → telemetry → Wazuh alert.

## Things to know (see README "Known rough edges")

- Multipass NAT means `sensor` only sees its own traffic: host-based, not span-port monitoring.
- Zeek runs as the `zeek/zeek:lts` Docker image with `--net=host` (no arm64 apt packages for
  Ubuntu 24.04).
- VM IPs come from DHCP and can shift after a host reboot; DNS pointing at `dc01` is applied via
  `resolvectl` (reliable) plus a best-effort netplan drop-in.
- `dc01` runs `samba-ad-dc` with a `Restart=on-failure` drop-in; domain passwords never expire.
  Members (`fs01`, clients) get an FQDN `/etc/hosts` entry and must end up with an AD DNS record
  (fs01 via `net ads join`, clients via sssd dyndns); their provisioning fails otherwise.
- Caldera needs Node 20.19+ for its UI build (Ubuntu 24.04 ships 18), so its template installs a
  pinned nodejs.org build and builds the UI during provisioning. Velociraptor is pinned
  (`VELO_VERSION`) because its CLI flags change between releases; re-check them when bumping.

## Rules

- **Intentionally weak by design:** seeded passwords (`Passw0rd!23`, `svc_sql` kerberoastable)
  and DVWA/Juice Shop exist for detection practice. Keep the lab on the Multipass NAT network;
  never expose it or reuse those credentials anywhere real.
- Keep the deploy script runnable on Apple-silicon macOS (arm64 Ubuntu images).
- Update the README architecture table and resource totals when adding or resizing a VM.
