# Changelog

All notable changes to SOC-lab are listed here, newest first.

The project has no tagged releases yet, so changes are grouped by date. Entries before November 2026 were reconstructed from the commit history.

## Unreleased

- `webtarget2`: WebGoat and OWASP VulnerableApp are pinned by digest as well as tag, so a re-pushed tag can't change what runs
- DevSecOps `sast`: Semgrep's rules come from `semgrep-rules` at a pinned commit (fetched once on `gitea`, checked by git), not the live registry, so scans are repeatable and need no internet
- Fix: `teardown-soc-lab.sh` ignored its arguments, so `--help` (or a typo) purged the running lab. It now
  takes `--yes`/`-y` and `--help`/`-h`, exits 2 on anything else without deleting, lists the VMs it will
  delete and asks `[y/N]` (default No; end of input is No), and refuses with exit 2 when there is no
  terminal to ask on and no `--yes`
- Fix: `running_lab_vms` swallowed `multipass list` failures, so a teardown could print "Done" and remove
  `.lab-state`, the roster and the IRIS key while VMs still ran, and a deploy could pass the one-lab guard.
  It now fails; teardown, deploy, `lab-info.sh` and `lab-doctor.sh` stop with "can't reach Multipass;
  nothing changed"
- The end-of-deploy banner names the lab and size, and shows the Ansible hint only when `ansible-ctrl` is
  in the lab; `lab-info.sh` says honeypot attempts go to Wazuh only when `siem` exists; the `redteam` role
  mentions the Kali tools in the pentest lab; the README marks `mgmt` as in no lab (its definitions in
  `lib/labs.sh` stay, because the template, seed role and `lab-info.sh` still refer to it)

- Lab menu: measured deploy times (SOC small 15, pentest small 8 and medium 13, DevSecOps medium 9
  minutes) are shown as plain minutes; sizes not yet measured keep the rough formula and show as
  `≈N min` (`LAB_MEASURED_MINUTES`, `lab_time_label` in `lib/labs.sh`). The README now explains the
  menu, the flags, resume and teardown, and has the per-lab, per-size resource table pasted from
  `--list`; `CLAUDE.md` and the deploy script's header no longer describe the single big lab
- `--dry-run` skips the confirmation (it launches nothing)
- Fix: `progress.sh` printed "Deployed in …" only to a terminal; when the output goes to a log
  (`| tee`) the line is now written too

- DevSecOps lab (`--lab devsecops`). `gitea` and `prod` get a DevSecOps set-up there (template flag
  `DEVSECOPS`; the other labs are unchanged): a pipeline with Gitleaks 8.30.1, Semgrep 1.182.0
  (image pinned by digest), OSV-Scanner 2.7.0, Trivy 0.76.1 (config and image scans), cosign 3.1.4
  signing by digest, a policy gate (`lab-policy`: pushed, signed with the lab key, non-root) and a
  deploy that `prod` accepts only for images signed with the lab's cosign key; every binary is
  pinned and SHA-256 checked on download (trivy and cosign were also checked against their Sigstore
  signatures when pinned). The `demo-app` repo is seeded with planted exercises: an AWS-style key
  in an old commit, `requests==2.31.0`, tag-pinned third-party actions, a `pull_request_target`
  workflow that runs PR code with secrets, and a runner with Docker socket access; the deploy ships
  a signed "release 1.0" so `prod` runs from the start. New VMs: `devclient01..` (git, vim, `tea`
  0.16.0; Gitea account `devNN` and a clone of `demo-app`, set up in stage 6, no domain join) and
  `dast` (OWASP ZAP 2.16.1 pinned by digest, baseline scan of `prod` every 2 hours, reports in
  `/var/lib/zap/reports`). In large, `siem` also gets the runner log (registry pushes and pulls are in Gitea's log, which it already read)
  and the ZAP summaries. `lab-info.sh` and `lab-doctor.sh` cover the new VMs (ZAP report, demo app,
  runner and ZAP timer)

- Network forensics lab (`--lab forensics`). New VM `arkime` (4 vCPU / 8 GB): Arkime 6.9.1 (arm64
  .deb, SHA-256 checked) on OpenSearch 3.9.0 (localhost only), viewer on `https://<arkime>:8005`
  (`admin` / `ADMIN_PASS`, in `lab-info.sh`), plus tshark, Zeek (Docker), ngrep and tcpflow.
  `sensor` becomes the lab's gateway and network tap: deploy stage 4b routes every other VM through
  it with a persistent netplan drop-in once all VMs are ready, and `sensor` forwards, NATs and copies
  the forwarded traffic to `arkime` (`lab-gateway.service`). Case PCAPs: five malware-traffic-analysis.net
  exercises pinned in `seed/case-pcaps.txt`, downloaded on `arkime` into `/srv/pcaps/cases` and
  checked by SHA-256 (a mismatch is deleted and logged), left in their password-protected zips.
  `redteam` runs a scheduled attack scenario every 30 minutes (`lab-attack.timer`: port scan,
  Kerberoast, SMB grab from `fs01`, DVWA/Juice Shop requests, DNS-tunnel-style exfil) with tools the
  lab already ships. None of this is set up in the other labs. `lab-doctor.sh` checks the routes,
  the tap, the timer, Arkime and the case PCAPs
- Forensics stage 4b fails safe: no VM is routed through `sensor` unless its tap is active, and a VM
  that can't resolve through `dc01` or reach the Ubuntu mirror afterwards gets its route removed
- `redteam` retries the Sliver downloads (GitHub's release CDN returned 504s for a while during testing)
- Pentest lab: off-the-shelf training material only. `redteam` gets the Kali tool set there
  (template flag `KALI_TOOLS`): Metasploit Framework (Rapid7's nightly apt build), Burp Suite
  Community 2026.10.1 (SHA-256 checked), sqlmap, hydra, john, gobuster, ffuf, nikto, dirb, and
  SecLists, Responder and enum4linux-ng from pinned, checksum-verified GitHub tags. New VM
  `webtarget2` runs OWASP WebGoat (`:8080`, WebWolf `:9090`) and OWASP VulnerableApp (`:9091`) from
  pinned images; `lab-info.sh` lists them. The planned privesc boxes (`privesc01..04`) are dropped
- The deploy asks which lab and size to build (`--lab`, `--size`, `--yes`, `--list`, `--dry-run`) and
  refuses while any lab VM exists. It writes `.lab-state` (lab, size, start time); `teardown-soc-lab.sh`
  deletes every lab VM it finds (all labs, `client01..20`, `devclient01..20`) and removes the file;
  `lab-info.sh` shows `Lab: <title> (<size>)` and takes roles from `lib/labs.sh`; `lab-doctor.sh`
  checks only lab VMs that exist. `VM_ENABLED` and `NUM_CLIENTS` are no longer edited by hand
- Labs without some servers provision cleanly: the Wazuh agent is installed only when the lab has
  `siem`, `redteam` and `sensor` point DNS at `dc01` only when it exists, and without `ansible-ctrl`
  no VM gets an empty authorized key (`ssh_authorized_keys: []`). The test-data config lists only the
  lab's VMs and the simulated staff visit only apps that are there. The SOC lab no longer builds the
  CI/CD servers (they move to the DevSecOps lab)

- First automated tests (`tests/test_templates.py`: placeholders, cloud-config YAML, embedded scripts
  through `bash -n` and ShellCheck) and CI that also runs ShellCheck and ruff

- Fix: dc01's test data could stop halfway when the random roster listed the same computer name
  twice ("Entry already exists"). New rosters never repeat a name, and dc01 skips repeats in
  rosters made before this fix
- Fix: the deploy's closing `lab-info.sh` run could pick macOS's bash 3.2 when the deploy was
  started as plain `bash` (bash sets `$BASH` from the PATH lookup); it now checks for a bash 4+
- New VM `mail` (2 vCPU / 2 GB): Postfix, Dovecot (passwords checked against AD over LDAPS,
  verified against dc01's CA) and Roundcube. The test-data stage adds a mailbox for everyone, group
  addresses and a few weeks of old mail; `lab-mailflow` delivers normal outside mail all day; the
  simulated staff read (IMAP or webmail), write and tidy their mail
- New VM `iris` (2 vCPU / 4 GB): DFIR-IRIS v2.4.29 with its own certificate for its IP. Wazuh alerts
  of level 10+ arrive as IRIS alerts through a `custom-iris` integration on `siem`, which verifies
  that certificate. The API key is generated once per lab (`.rendered-cloud-init/iris-api-key`)
- Wazuh dashboard "SOC-lab overview" and four saved searches (`wazuh/dashboards/`), installed at
  the end of the deploy over the dashboard API on `siem` (certificate verified against the Wazuh
  root CA). Creates the `wazuh-alerts-*` index pattern with its fields if the Wazuh app hasn't
- Lab totals are now about 36 vCPU / 50 GB RAM with everything enabled
- Web proxy: Squid on `sensor` (port 3128, lab networks only). The simulated staff browse
  through it, so `sensor` -- which otherwise only sees its own traffic -- sees every client's web
  traffic in Squid's access log and on the proxy's outgoing leg in Zeek/Suricata. Rule 100190
  keeps successful proxied requests (Wazuh's own Squid rules only alert on errors)
- `lab-doctor.sh`: read-only health check (VMs, disk, failed units, DNS drop-ins vs dc01's IP,
  Wazuh agents, Velociraptor clients, activity services); exits 1 on any failure
- Fix: Suricata never started on fresh `sensor` builds. apt starts it on the default `eth0`
  at install, it fails, hits systemd's start limit, and the later `enable --now` was refused. The
  template now clears the failure, restarts it and fails provisioning if it isn't running.
  (Found by `lab-doctor.sh` on the fresh deploy.)
- Wazuh agents on `mgmt`, `lam` and `caldera`. Kuma and Semaphore logs are copied from their
  containers to `/var/log/lab-apps/` (`lab-container-log@` services). New decoders and rules
  100160-100181: Kuma logins, brute force and monitor outages; LAM logins, brute force and logins
  by non-IT accounts; Semaphore task results
- Deploy: each test-data line shows the role's own result (`[labdata]`-prefixed output) instead of
  whatever came last, which on `velociraptor` was a gRPC warning; `lam` is seeded before the
  clients, so the simulated IT staff can log in to it from their first pass
- Live deploy status panel (`lib/progress.sh`): a total progress bar with elapsed time, the
  current stage, a bar per VM with its provisioning step (from `lab_step` markers in every
  template's `provision.sh`, read from `/run/lab-progress`), the image download while a VM
  launches, and a bar for the deploy's own tasks. Plain timestamped lines when the output isn't a
  terminal or with `NO_PROGRESS=1`. Stages 1-3 now launch like stage 4 (return once booted, then
  poll), so they show progress too
- Real logins in the remaining web apps, set up by the test-data stage:
  - Uptime Kuma (`mgmt`) gets an admin account (generated password, shown by `lab-info.sh`) and
    ~17 monitors for the lab's services, checked every minute. IT staff log in to it over
    socket.io (plain HTTP long-polling, stdlib only);
  - LDAP Account Manager (`lam`) is configured for the domain over LDAPS, verified against
    `dc01`'s Samba CA, instead of being left to the setup wizard. IT staff log in with their own
    domain accounts, so a mistyped password shows up as Wazuh rule 100110 on `dc01`, and LAM now
    logs logins to syslog (log level 5). A profile you already changed is left alone;
  - IT staff log in to Caldera as `blue` and read agents and operations (never starting one).
    Caldera doesn't log logins at its default level.
- Velociraptor hunting: an API user (`lab-analyst`), four baseline hunts at deploy, and a
  `lab-velociraptor` service that starts a random built-in Linux hunt every 1-4 hours in office
  hours (`/var/log/lab-velociraptor.log`)
- `teardown-soc-lab.sh` deletes the staff roster and seed config, so a new lab gets new staff
  instead of the deleted lab's people (and their passwords don't linger)
- `labdata.py services <roster>` adds new service accounts (Kuma's) to a roster from an older
  version; the deploy runs it when the roster lacks them
- Security: the simulated staff (running as root on the clients) passed file names from the
  world-writable `\\fs01\public` share into an `smbclient -c` command line, so a file named with
  `"`, `;` and `!` could run commands as root on a client. Names outside a plain character set are
  now skipped (found by the automated security review)
- Test data (deploy stage 7, `seed/labdata.py`, stdlib Python run on the VMs). It is random per
  lab, and all dates are relative to the deploy day. The data: a fictional company with ~46 staff
  in department OUs, groups, managers and computer accounts on `dc01`; ~120 documents, invoices,
  budgets and payroll on `fs01`; home folders on the clients; an `engineering` org on `gitea` with
  backdated history and issues; a Semaphore project on `mgmt`; playbooks on `ansible-ctrl`;
  DVWA's database. It also plants credential mistakes to find. The roster is kept in
  `.rendered-cloud-init/lab-roster.json`, and re-runs add nothing twice
- Simulated activity that never stops (systemd services with `Restart=always`, random gaps,
  office hours):
  - staff on every client: Kerberos logons with typos, SMB, web apps, FTP, telnet, SSH,
    downloads, git, and occasional `demo-app` pushes through the pipeline;
  - Ansible runs from `ansible-ctrl` and scheduled Semaphore jobs from `mgmt`, both over SSH;
  - a twice-daily backup job on `fs01`.

  Verified on the live lab: the events reach Wazuh (SSH, sudo, FTP and telnet logins, Suricata
  HTTP), Zeek (`ftp.log`, `ssh.log`, `http.log`) and each app's own logs
- `sensor` hosts the office's legacy services: an IT status page with downloads, a vsftpd
  scan-to-FTP drop and a telnet console. NAT means it only sees its own traffic, so this is where
  Suricata and Zeek see HTTP, FTP and telnet sessions
- New Wazuh rules: 100104 for a wrong password at a Kerberos logon, and 100105 for Kerberos
  password spraying (5+ accounts from one address in 2 minutes). Kerberos failures used to raise
  no alert at all
- `lab-info.sh` lists the generated staff (with passwords, desk and weak spots), the FTP and
  telnet accounts, and the status page. DVWA no longer needs the manual "Create / Reset
  Database" step
- README: Mermaid diagrams of the servers, the directory, alert flow, deploy stages, the CI/CD
  pipeline and the activity; a step-by-step guide from VS Code to production; a test-data
  section; spraying and credential-hunting scenarios that use the generated data

- sensor: Suricata failed to start on a fresh build -- the packaged service takes its capture
  interface from `/etc/default/suricata` (`IFACE=eth0`), which the template now sets to the real
  interface too. Found by a full fresh deploy; verified that scans alert again (100131/100132)
- Full fresh deploy of all 16 VMs verified: exit 0 in 28 minutes, no Multipass daemon crash,
  `cloud-init status` exit 0 everywhere, 9 Wazuh agents and 4 Velociraptor clients online, first
  pipeline run deployed, and every AD, network, CI/CD and honeypot detection re-tested

- `deploy-soc-lab.sh` and `teardown-soc-lab.sh` are executable: `./teardown-soc-lab.sh`, as the
  README says to run it, failed with "permission denied" since the first commit

- CI/CD lab: new `gitea` VM (Gitea 28.2.0 with Actions runner and container registry) and `prod`
  VM. The seeded `demo-app` pipeline runs secret scan (gitleaks), SAST (Semgrep), dependency scan
  (Trivy), Dockerfile lint (hadolint), image build + Trivy image scan, push, and an SSH deploy to
  prod via a forced-command key that only accepts lab-registry images. Verified: first run passes
  all six jobs and deploys. A git hook, pipeline events, prod deploys and container starts are
  shipped to Wazuh; rules 100140-100147. Verified with the README exercises: leaked credential
  (secret-scan blocks the deploy), force push, pipeline edit, foreign image via the deploy key
  (rejected), and a manual `docker run` on prod each raise their alert
- redteam: Sliver C2 v1.7.8 (pinned, checksum-verified; tested live)
- `honeypot` VM (OpenCanary 0.9.10: fake FTP, HTTP login, SSH on 2222, Telnet, MySQL, Redis, VNC)
  with rules 100150-100151; verified that login attempts from redteam alert at level 12
- `lab-info.sh` lists the CI/CD, honeypot and demo-app entries; the deploy runs it with its own
  bash 4+ (the `#!/usr/bin/env bash` line found macOS's 3.2)

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
