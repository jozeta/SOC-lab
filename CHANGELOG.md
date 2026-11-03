# Changelog

All notable changes to SOC-lab are listed here, newest first.

The project has no tagged releases yet, so changes are grouped by date. Entries before November 2026 were reconstructed from the commit history.

## Unreleased

- CI/CD lab: new `gitea` VM (Gitea 28.2.0 with Actions runner and container registry) and `prod`
  VM. The seeded `demo-app` pipeline runs secret scan (gitleaks), SAST (Semgrep), dependency scan
  (Trivy), Dockerfile lint (hadolint), image build + Trivy image scan, push, and an SSH deploy to
  prod via a forced-command key that only accepts lab-registry images. Verified: first run passes
  all six jobs and deploys. A git hook, pipeline events, prod deploys and container starts are
  shipped to Wazuh; rules 100140-100147 added (CI/CD attack scenarios not yet exercised)
- redteam: Sliver C2 v1.7.8 (pinned, checksum-verified; tested live)
- Work in progress, not yet wired into the deploy: `honeypot` template (OpenCanary) and rules
  100150-100151

- Detection lab:
  - `dc01` now writes Samba's JSON audit trail (authentication, directory, group and password
    changes) and Kerberos KDC messages, and runs a Wazuh agent that ships them
  - 12 custom Wazuh rules in `wazuh/` (kerberoasting, AS-REP roasting, password spraying, brute
    force, privileged group changes, new accounts, password resets, network scans), installed on
    `siem` by the deploy script and each tested end to end from `redteam`
  - AS-REP roasting target `svc_legacy`; `svc_sql` is RC4-only. Samba needs the "no pre-auth" flag
    on krbtgt too, and `[kdc] require-preauth = false` in `/etc/samba/krb5.conf`
  - `redteam`: `lab-kerberoast` (MIT kinit/kvno; Samba 4.19 rejects impacket's TGS-REQ for lack of
    an authenticator checksum), `hashcat`, and a lab-realm `krb5.conf`
  - `sensor`: Suricata `EXTERNAL_NET: any`, so scans from the internal attacker are detected
  - Velociraptor agents on `dc01`, `fs01` and the clients (server builds a client `.deb` with its
    real address; new deploy stage installs it)
- Deploy order is now ansible-ctrl -> siem -> dc01 -> the rest (dc01's agent needs siem's IP)
- `lab-info.sh` prints every VM (role, IP), every domain account and every web app (URL, login,
  live UP/DOWN); the deploy script runs it at the end. Seeded passwords moved to the top of
  `deploy-soc-lab.sh` as the single source for dc01 and `lab-info.sh`

- `cloud-init status` is clean again: Multipass rewrites `permissions: '0755'` into the integer
  493, which failed cloud-init's schema on every VM (status exit code 2); templates now use
  `'0o755'`
- fs01 re-registers its AD DNS record after every boot and daily (`lab-dns-register.timer`,
  machine account), so the record follows a DHCP address change
- Templates pass shellcheck: removed an unused `IFACE` in redteam and unused loop variables

- Fixes found by a full 13-VM deploy:
  - DVWA: use the project's multi-arch image (`ghcr.io/digininja/dvwa`) with a MariaDB container;
    the old `vulnerables/web-dvwa` image is amd64-only and crash-looped on Apple silicon
  - Semaphore: pin `v2.19.17` with SQLite; `latest` dropped the BoltDB backend the template
    used, so the container crash-looped
  - redteam: build netexec with Ubuntu's newest versioned Rust (`cargo-1.NN`; the default 1.75
    can't read aardwolf's `Cargo.lock`, so netexec silently failed to install), and link
    `impacket-*`, `bloodhound-python` and `nxc`/`netexec` into `/usr/local/bin` so they work in
    any shell under the names the README uses
  - siem: let a re-created agent replace one with the same name immediately (Wazuh's default
    refused it for an hour), so deleting and rebuilding a VM keeps its telemetry

- Member AD DNS survives reboots: `fs01`, clients, `sensor` and `redteam` use a systemd-resolved
  drop-in instead of the netplan drop-in plus per-link `resolvectl` override, which was lost on
  every reboot
- VMs start patched: all templates run `package_upgrade`, and the deploy script reboots a VM after
  provisioning when an update requires it (`reboot_if_required`, one VM at a time)

- Deploy script hardened:
  - never runs `multipass` commands concurrently: stage-4 VMs launch one after another with a
    short launch timeout (`STAGE4_LAUNCH_TIMEOUT`, 120 s) and keep provisioning in parallel. The
    daemon (1.16.4 and 1.17.0 on macOS 27) corrupted its heap and crashed under parallel
    launches, then hung on the orphaned VMs. A stage-4 VM stuck in "Unknown" after launch (seen
    once in testing: the guest never got a DHCP lease) is deleted and relaunched once
  - pass `--timeout` (`READY_TIMEOUT`, 45 minutes) to `multipass launch`; its 5-minute default
    aborted the deploy while slow VMs such as `siem` were still provisioning
  - failed launches or provisioning are reported, the remaining VMs still finish, and the
    script exits non-zero without writing the inventory (re-running resumes, since existing
    VMs are skipped)
  - `wait_ready` never sends `exec` output to `/dev/null` (Multipass 1.16.4 and 1.17.0 hang on that), puts a
    watchdog on each call, and treats a timeout as a failure instead of carrying on
  - `render_template` no longer uses sed: `&`, `|` and `\` in values were mangled (a password
    with `&` silently changed); rendered files are now mode 600
  - refuse an `ADMIN_PASS` containing `"`, `$`, `` ` ``, `\` or a newline, which would break the
    templates' embedded shell scripts

- dc01: `samba-ad-dc` now restarts on failure (systemd drop-in). The packaged unit has
  `Restart=no`, so one failed restart -- seen after a long suspend, when unattended-upgrades
  restarted Samba mid network reconfiguration -- took the whole domain down
- fs01 and clients register themselves in AD DNS: they get an FQDN `/etc/hosts` entry before
  joining, fs01 falls back to `net ads dns register`, clients enable sssd dynamic DNS (immediate,
  no PTR), and provisioning fails if the record doesn't appear. Previously no member had a record
- README screenshots (Wazuh, Caldera, Velociraptor); gitignore `.playwright-mcp/`

## 2026-11-03

- Gitignore `.rendered-cloud-init/`, which holds rendered templates containing the lab passwords
- Teardown purges only the lab's own VMs (`multipass delete --purge <name>`) instead of a global
  `multipass purge`, and no longer lists the non-existent `misp` VM
- README links the security policy
- Velociraptor: pin `v0.77.3` and handle the gzipped release binaries (the old "latest" asset
  lookup no longer matched anything, so fresh deploys failed); bind the GUI to all interfaces so
  it is reachable at `https://<ip>:8889` (it was bound to `127.0.0.1` only); create the admin
  account with the verified `user add --role` syntax; cloud-init waits until the GUI answers
- Caldera: install a pinned, checksum-verified Node 22 from nodejs.org (Ubuntu's Node 18 is too
  old for the Vite UI build, so the service crash-looped and port 8888 never came up); build the
  UI during provisioning and wait for port 8888; document the real login (`red` / `admin`)
- dc01: set the domain's maximum password age to "never" -- Samba's 42-day default expired
  Administrator and every seeded account, which made later domain joins fail
- fs01: wait for winbind to resolve `LAB\Finance` before setting the finance share's group, and
  fail cloud-init with diagnostics instead of silently skipping it

## 2026-11-02

- Add security policy

## 2026-11-01

- Ignore macOS, Claude Code and secret files; untrack .DS_Store
- Add CLAUDE.md with project guidance for Claude Code

## 2026-08-05

- added ignore file
