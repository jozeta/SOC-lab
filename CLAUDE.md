# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A blue-team SOC lab built on macOS with **Multipass + cloud-init**: a Samba4 AD domain
(`LAB.LOCAL`), Wazuh SIEM, Suricata/Zeek sensor, Ansible control node, file server, N
domain-joined clients, attacker and vulnerable-target boxes, plus add-ons (LDAP Account
Manager, MITRE Caldera, Velociraptor, Semaphore + Uptime Kuma). Ubuntu VMs only: no Windows.

## Commands

```sh
brew install bash jq                           # plus the Multipass 1.16.4 .pkg
/opt/homebrew/bin/bash ./deploy-soc-lab.sh     # needs bash 4+, NOT macOS's /bin/bash 3.2
./teardown-soc-lab.sh
multipass list
multipass exec <vm> -- sudo cloud-init status --long
```

Everything with `NUM_CLIENTS=2` is about 29 vCPU / 39 GB RAM allocated. Trim with `VM_ENABLED` and
`NUM_CLIENTS` at the top of the deploy script. There are no automated tests; validate shell
changes with `bash -n` and `shellcheck` (also on the `/root/provision.sh` embedded in each template,
after rendering), and template changes by deploying the affected VM.

## How it works

- `deploy-soc-lab.sh` holds all config (domain, `ADMIN_PASS`, `NUM_CLIENTS`, `CLIENT_SPEC`,
  `VM_ENABLED`, per-VM specs). It renders each `cloud-init-templates/<vm>.yaml.tmpl` (placeholder
  substitution), launches VMs in dependency order (`ansible-ctrl` → `siem` → `dc01` → rest
  launched one at a time, provisioning in parallel), polls `cloud-init status` at each dependency point, then pushes a generated Ansible
  inventory to `ansible-ctrl`.
- Deploy order: `ansible-ctrl` -> `siem` -> `dc01` -> the rest (dc01's Wazuh agent needs siem's IP).
  Custom Wazuh decoders/rules are files in `wazuh/`, pushed to siem by `install_wazuh_rules`; test a
  rule change with `wazuh-logtest` on siem and an end-to-end attack from `redteam`.
- Test data and the never-ending simulated activity come from `seed/labdata.py` (stdlib only,
  runs on the VMs; one role per VM, run by `seed_lab_data` in deploy stage 7). The roster is
  generated once into `.rendered-cloud-init/lab-roster.json`; roles must stay safe to re-run
  (seeded folders get a marker in `/var/lib/labdata/`). Roles also set up apps that need the
  domain first: Uptime Kuma's account and monitors (`mgmt`), LAM's LDAPS profile (`lam`, with
  `dc01`'s CA passed in the config -- never disable TLS checks), Velociraptor's API user
  (`velociraptor`). The traffic loops (`lab-activity`, `lab-ansible`, `lab-semaphore`,
  `lab-velociraptor`) are services running `labdata.py loop <config>`; test one pass
  with `sudo LAB_ACTIVITY_BUSY=1 python3 /usr/local/sbin/lab-activity run /etc/lab-activity/config.json`.
  Every command in it must run with no stdin and a timeout (smbclient/git/ssh prompt and hang
  otherwise). Legitimate traffic must never touch `honeypot`, and the simulation never attacks.
- `lab-info.sh` must list every VM and web app: when adding a VM or app, add it to its `ROLE` map and
  `app` lines (it flags unknown VMs, but can't invent URLs or credentials).
- `client.yaml.tmpl` is rendered once per client (`client01`…`clientNN`, zero-padded).
- Domain-joined and monitored VMs run Wazuh agents shipping to `siem`, so the pipeline is
  attack (`redteam`/`vulntarget`) → telemetry → Wazuh alert.

## Things to know (see README "Known rough edges")

- Multipass NAT means `sensor` only sees its own traffic: host-based, not span-port monitoring.
- Zeek runs as the `zeek/zeek:lts` Docker image with `--net=host` (no arm64 apt packages for
  Ubuntu 24.04).
- VM IPs come from DHCP and can shift after a host reboot. Members point DNS at `dc01` with a
  persistent systemd-resolved drop-in (`/etc/systemd/resolved.conf.d/lab-dns.conf`), which holds
  `dc01`'s IP at deploy time -- if `dc01`'s IP changes, update that file.
- Templates run `package_upgrade`; the deploy script reboots a VM (`reboot_if_required`) when an
  update leaves `/var/run/reboot-required`, so everything must come back up after a reboot.
- `dc01` runs `samba-ad-dc` with a `Restart=on-failure` drop-in; domain passwords never expire.
  Members (`fs01`, clients) get an FQDN `/etc/hosts` entry and must end up with an AD DNS record
  (fs01 via `net ads join`, clients via sssd dyndns); their provisioning fails otherwise.
- Caldera needs Node 20.19+ for its UI build (Ubuntu 24.04 ships 18), so its template installs a
  pinned nodejs.org build and builds the UI during provisioning. Velociraptor is pinned
  (`VELO_VERSION`) because its CLI flags change between releases; re-check them when bumping.

- Tested on Multipass 1.16.4 (QEMU driver), macOS 27. Never run `multipass` commands
  concurrently: the daemon (1.16.4 and 1.17.0) crashes under parallel launches, so stage 4
  launches sequentially with `STAGE4_LAUNCH_TIMEOUT`. Never redirect `multipass exec` output to
  `/dev/null` (it hangs on 1.16.4 and 1.17.0). `READY_TIMEOUT` bounds `multipass launch --timeout` and `wait_ready`.

## Rules

- **Intentionally weak by design:** seeded passwords (`Passw0rd!23`, `svc_sql` kerberoastable)
  and DVWA/Juice Shop exist for detection practice. Keep the lab on the Multipass NAT network;
  never expose it or reuse those credentials anywhere real.
- Keep the deploy script runnable on Apple-silicon macOS (arm64 Ubuntu images).
- Update the README architecture table and resource totals when adding or resizing a VM.
