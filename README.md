# SOC Lab on Multipass

A blue-team lab built from Multipass + cloud-init on macOS: an Active
Directory domain, a SIEM, a network sensor, config management, and
attacker/target boxes to generate telemetry against -- plus a growing set
of add-on tools (LDAP UI, adversary emulation, DFIR, threat intel, status
dashboards).

## Important constraint: no Windows

Multipass only launches Ubuntu VMs. There is no Windows Server or Windows
10/11 support. The domain controller and everything else here runs on
Linux:

- **Directory service** = **Samba4 as an AD DC** (real AD-compatible LDAP,
  Kerberos KDC, and DNS -- not a simulation, but not Windows Server either).
- **Domain-joined boxes** authenticate against it exactly like a Windows
  estate would (Kerberos, LDAP, group policy concepts largely absent since
  full GPO tooling is a Windows/RSAT thing -- see "Ideas for later" below).

If you later want real Windows Server / Windows clients in the mix, you'll
need a second hypervisor (UTM, Parallels, VMware Fusion) since Multipass
can't provide them; point those VMs' DNS at `dc01`'s IP and they can join
the same `LAB.LOCAL` domain.

## Architecture

| VM              | Role                                                  | Domain-joined |
|-----------------|--------------------------------------------------------|:---:|
| `ansible-ctrl`  | Config management (Ansible control node)              | -   |
| `dc01`          | Samba4 AD DC -- DNS, Kerberos, LDAP for `LAB.LOCAL`; audit logging + Wazuh agent | -   |
| `fs01`          | File server (Samba shares, AD-authenticated)           | Y   |
| `client01..N`   | Domain-joined "workstations" -- count set by `NUM_CLIENTS` | Y |
| `siem`          | Wazuh manager + indexer + dashboard                    | -   |
| `sensor`        | Suricata + Zeek network monitoring + Wazuh agent       | -   |
| `redteam`       | Attacker box: nmap, impacket, bloodhound, netexec, hashcat, `lab-kerberoast` | -   |
| `vulntarget`    | DVWA + Juice Shop in Docker, for web-attack telemetry  | -   |
| `lam`           | LDAP Account Manager -- web UI for `dc01`               | -   |
| `caldera`       | MITRE Caldera -- adversary emulation, ATT&CK-mapped     | -   |
| `velociraptor`  | Velociraptor -- DFIR/hunting server; agents on `dc01`, `fs01`, clients | -   |
| `mgmt`          | Semaphore UI (Ansible web UI) + Uptime Kuma (status dashboard) | - |

Every domain-joined/monitored VM ships logs to `siem` via a Wazuh agent, so
detections can be built and tested end-to-end: attack on `redteam` or
`vulntarget` -> telemetry on `sensor`/endpoint agents -> alert in Wazuh.

Total footprint with everything enabled and `NUM_CLIENTS=2`: roughly
**29 vCPU / ~39GB RAM** allocated (not all used simultaneously). Edit
`VM_ENABLED` and `NUM_CLIENTS` at the top of `deploy-soc-lab.sh` to trim
this down -- a good minimal start is `ansible-ctrl`, `dc01`, `fs01`, `siem`,
plus 1 client, with everything else disabled.

### Screenshots

| Wazuh dashboard (`siem`) | MITRE Caldera | Velociraptor |
|---|---|---|
| ![Wazuh dashboard overview with agent summary and alert counts](docs/screenshots/wazuh.png) | ![Caldera dashboard after logging in as red](docs/screenshots/caldera.png) | ![Velociraptor welcome screen logged in as admin](docs/screenshots/velociraptor.png) |

### Adding more clients

Change one line in `deploy-soc-lab.sh`:

```bash
NUM_CLIENTS=5   # creates client01 through client05
```

That's it -- no other edits needed. The script renders `client.yaml.tmpl`
once per client with a zero-padded hostname (`client01`, `client02`, ...),
launches them one after another (they provision in parallel), and adds each to the generated Ansible
inventory under `[clients]`. Set `NUM_CLIENTS=0` to skip clients entirely.
`CLIENT_SPEC` (right below it) controls the CPU/RAM/disk applied to every
client uniformly.

## Usage

```bash
# 1. review/edit domain name + admin password + NUM_CLIENTS in deploy-soc-lab.sh
# 2. requires bash 4+ (macOS default bash is 3.2)
brew install bash jq   # jq isn't required by the script but handy for ad-hoc queries
# Multipass: the 1.16.4 .pkg from https://github.com/canonical/multipass/releases
/opt/homebrew/bin/bash ./deploy-soc-lab.sh
```

The script launches VMs in dependency order (ansible-ctrl -> siem -> dc01 ->
everything else, launched one at a time but provisioning in parallel), polls
`cloud-init status` at each dependency point, installs the lab's Wazuh
detection rules on `siem` and the Velociraptor agent on `dc01`, `fs01` and the
clients, pushes a generated Ansible inventory to `ansible-ctrl`, and finally
prints an overview of the lab:

- every VM with its role and IP,
- every domain account and its password,
- every web app with its URL, login and whether it answers right now.

Re-print that overview any time (IPs can change after a host reboot) with:

```bash
/opt/homebrew/bin/bash ./lab-info.sh
```

Each VM gets `READY_TIMEOUT` (45 minutes by default) to finish
provisioning. If a VM fails to launch or provision, the others still finish,
then the script prints which ones failed and exits non-zero without writing
the inventory. Fix the cause, delete the broken VMs with `multipass delete
--purge <name>`, and re-run it: VMs that already exist are skipped. The
domain password `ADMIN_PASS` must not contain `"`, `$`, `` ` ``, `\` or a
newline; the script refuses to start otherwise.

Tested with Multipass 1.16.4 (QEMU driver) on macOS 27. The script never runs
two `multipass` commands at once: stage-4 VMs are launched one after another
(each launch returns after `STAGE4_LAUNCH_TIMEOUT` while the VM keeps
provisioning), because the Multipass daemon crashes under concurrent launches
-- see "Known rough edges".

To tear the whole lab down:

```bash
./teardown-soc-lab.sh
```

Teardown deletes and purges only the lab's own VMs; other Multipass
instances on your Mac are untouched. The rendered cloud-init files in
`.rendered-cloud-init/` contain the lab passwords: they are gitignored and
left on disk for inspection, so delete the folder when you no longer need it.

## Default lab accounts (change the password in the script before real use)

| Account | Notes |
|---|---|
| `Administrator` | Domain Admin, password set by `ADMIN_PASS` in the script |
| `alice.user`, `bob.user` | Plain domain users |
| `jane.finance` | Member of `Finance`, can reach the `finance` share on `fs01` |
| `mike.helpdesk` | Member of `HelpDesk` and `IT-Admins` |
| `svc_sql` | Service account with an SPN (`MSSQLSvc/fs01...`) that only supports RC4 -- **intentionally kerberoastable** |
| `svc_legacy` | Service account with "Do not require Kerberos pre-authentication" -- **intentionally AS-REP roastable** |

All seeded passwords are `Passw0rd!23` except `svc_sql` (`SqlServ1ce#2024`)
and `svc_legacy` (`Backup2024!`). They are set at the top of
`deploy-soc-lab.sh` (`SEED_PASS`, `SVC_SQL_PASS`, `SVC_LEGACY_PASS`).
Domain passwords never expire (`dc01` sets the maximum password age to 0),
so a lab left running for weeks still accepts them; complexity rules stay on.

## Add-on tool access

| Tool | URL / how to reach it | Credentials |
|---|---|---|
| Wazuh dashboard | `https://<siem-ip>/` | `admin` / generated at install -- `lab-info.sh` prints it |
| DVWA | `http://<vulntarget-ip>/` | First open `/setup.php` and click **Create / Reset Database**, then log in as `admin` / `password` and set the difficulty |
| Juice Shop | `http://<vulntarget-ip>:3000/` | no login needed to start |
| LDAP Account Manager | `http://<lam-ip>/lam` | Complete the one-time server-profile wizard first -- see below |
| MITRE Caldera | `http://<caldera-ip>:8888` | `red` / `admin` (also `blue` / `admin`, `admin` / `admin`): Caldera's `--insecure` defaults |
| Velociraptor | `https://<velociraptor-ip>:8889` (self-signed certificate) | `admin` / the lab `ADMIN_PASS` |
| Uptime Kuma | `http://<mgmt-ip>:3001` | set up your own admin account on first visit |
| Semaphore UI | `http://<mgmt-ip>:3000` | `admin` / the lab `ADMIN_PASS` |

**LDAP Account Manager first-time setup**: open the URL above and complete
the server profile wizard with:
- Tree suffix / Base DN: `DC=lab,DC=local` (adjust if you changed `DOMAIN_REALM`)
- Server URL: `ldap://<dc01-ip>:389`
- Bind DN: `administrator@lab.local`
- Bind password: the domain Administrator password
- LAM's own config-login password (separate from the above) defaults to `lam`

## Detections

Custom Wazuh decoders and rules live in [`wazuh/`](wazuh/) and are installed
on `siem` by the deploy script. Every rule was tested end to end by running
the attack from `redteam`.

| Rule | Level | Detects | Source |
|---|---|---|---|
| 100101 | 10 | Kerberoasting: RC4 service ticket for a non-krbtgt service (T1558.003) | `dc01` KDC log |
| 100102 | 12 | Kerberoasting burst: 3+ RC4 service tickets for one user in 5 min | `dc01` KDC log |
| 100103 | 10 | AS-REP roasting: TGT issued without pre-authentication (T1558.004) | `dc01` KDC log |
| 100110 | 3 | Failed logon on the DC (SMB, LDAP, Kerberos, ...) | `dc01` auth audit |
| 100111 | 10 | Password spraying: 5+ different accounts fail from one IP in 2 min (T1110.003) | `dc01` auth audit |
| 100112 | 10 | Brute force: 8+ failures for one account in 2 min (T1110.001) | `dc01` auth audit |
| 100120 | 12 | Account added to Domain/Enterprise/Schema Admins or Administrators (T1098) | `dc01` group audit |
| 100121 | 5 | Any AD group membership change | `dc01` group audit |
| 100122 | 8 | New AD user/object created (T1136.002) | `dc01` directory audit |
| 100123 | 8 | Password reset by another account (T1098) | `dc01` password audit |
| 100131 | 8 | Network scan signature from Suricata (T1046) | `sensor` Suricata |
| 100132 | 10 | Port scan: 4+ scan signatures from one source in 2 min (T1046) | `sensor` Suricata |

Wazuh's built-in rules cover the rest of the endpoint telemetry (SSH
invalid users, sudo, PAM, file integrity, Suricata alerts at level 3).

## Example detection/hunting scenarios to build against

- **Kerberoasting**: from `redteam`, get `svc_sql`'s service ticket and crack
  it offline:
  `lab-kerberoast alice.user 'Passw0rd!23' MSSQLSvc/fs01.lab.local:1433 svc_sql > tgs.hash`
  then `hashcat -m 13100 tgs.hash <wordlist>`. (`lab-kerberoast` wraps MIT
  `kinit`/`kvno`: Samba 4.19 rejects impacket's TGS request, so
  `impacket-GetUserSPNs -request` and `nxc --kerberoasting` fail here --
  listing SPNs with `impacket-GetUserSPNs` still works.) Wazuh: rule 100101.
- **AS-REP roasting**: `impacket-GetNPUsers lab.local/svc_legacy -dc-ip
  <dc01-ip> -no-pass -format hashcat > asrep.hash`, then
  `hashcat -m 18200 asrep.hash <wordlist>`. Wazuh: rule 100103.
- **Password spraying / brute force**: one password against many accounts,
  e.g. `nxc smb <dc01-ip> -u users.txt -p 'Autumn2026!' --continue-on-success`
  (or `kinit` in a loop for Kerberos spraying). Wazuh: rules 100110-100112.
  SSH password logins are disabled on the Ubuntu cloud images, so `hydra`
  against SSH only produces invalid-user alerts.
- **Persistence in AD**: as Administrator over LDAP, create an account,
  add it to Domain Admins or reset someone's password
  (`samba-tool ... -H ldap://dc01.lab.local -U administrator`). Wazuh: rules
  100120-100123. (Changes made with `samba-tool` locally on `dc01` without
  `-H` bypass the server and its audit log.)
- **Network scanning**: `sudo nmap -sS -O <sensor-ip>` from `redteam`.
  Suricata's Emerging Threats scan signatures fire on `sensor`; Wazuh:
  rules 100131/100132.
- **Web attack telemetry**: DVWA and Juice Shop on `vulntarget` (SQLi, XSS,
  command injection, broken auth/access control) for triage/detection-rule
  practice.
- **AD recon**: `bloodhound-python` / `ldapsearch` from `redteam` against
  `dc01` to map attack paths, then discuss what LDAP-recon detections would
  look like in a real environment.
- **ATT&CK-mapped adversary emulation**: run a Caldera operation against a
  client/fs01 and use the known-ground-truth technique list to check
  whether your Wazuh rules actually catch it.
- **Network-metadata hunting**: pivot from a Suricata alert on `sensor` into
  Zeek's `conn.log`/`dns.log`/`http.log` for the same traffic -- practice
  the "alert says X, what does the connection metadata actually show"
  workflow.
- **Endpoint DFIR**: `dc01`, `fs01` and the clients run the Velociraptor
  agent; run a hunt across them from the Velociraptor GUI after simulating
  compromise.
- **Config drift / hardening checks**: use the Ansible control node (or
  Semaphore's web UI on `mgmt`) to run CIS-style checks across
  `fs01`/`client01..N` and remediate drift.

## Known rough edges (read before you file a bug against yourself)

- **Multipass daemon bugs** (found while testing this lab on macOS 27, and
  reported upstream):
  - The daemon corrupts its heap and crashes (macOS's allocator aborts it:
    `SIGTRAP`/`SIGSEGV`/`SIGABRT` in malloc, libssh or gRPC) when several VMs
    launch at once -- seen on both 1.16.4 and 1.17.0, with as few as two
    parallel launches. Don't run `multipass` commands in parallel; the deploy
    script doesn't. ([#5476](https://github.com/canonical/multipass/issues/5476#issuecomment-6615330811))
  - `multipass exec` hangs forever when the command's output is redirected to
    `/dev/null` on the Mac (pipes and files work) -- 1.16.4 and 1.17.0. The
    deploy script never does this. ([#5498](https://github.com/canonical/multipass/issues/5498))
  - After a daemon crash, the restarted daemon can hang at 100% CPU because
    the old VMs' QEMU processes still hold their disk locks; the CLI and GUI
    then show "waiting for daemon". Recover with `sudo kill -9 <qemu pids>`
    and `sudo launchctl kickstart -k system/com.canonical.multipassd`.
    ([#5499](https://github.com/canonical/multipass/issues/5499))
  - Multipass 1.17.0's own failures (launch timeout, unknown instance) exit
    with 2, not the 255 its release notes describe.
    ([#5500](https://github.com/canonical/multipass/issues/5500))
  - A `multipass launch --timeout` that expires before the VM has booted
    leaves it permanently in "Unknown" state; the deploy script's
    `STAGE4_LAUNCH_TIMEOUT` is sized to avoid this, and it deletes and
    relaunches a stuck VM once.
    ([#5501](https://github.com/canonical/multipass/issues/5501))

- **fs01 AD idmap**: `security = ads` + winbind idmap is one of the more
  finicky corners of Samba. Provisioning waits (up to 2 minutes) for winbind
  to resolve `LAB\Finance` before setting the group on `/srv/shares/finance`,
  and fails cloud-init with `wbinfo` diagnostics in `/var/log/provision.log`
  if it never does. To debug, run `wbinfo -t` and `wbinfo -g` on `fs01`.
- **sensor visibility**: Multipass VMs sit behind NAT with no shared bridge,
  so both Suricata and Zeek on `sensor` only see their own traffic, not a
  real span port. Treat it as host-based monitoring, not full network
  visibility: scan or attack `sensor` itself to see network detections.
  Suricata's `EXTERNAL_NET` is set to `any` because the attacker (`redteam`)
  sits inside `HOME_NET`; with the default, internal attacks never match.
- **Zeek runs in Docker, not via apt**: Zeek's own apt repository does not
  publish arm64 packages for Ubuntu 24.04 specifically (only amd64), so
  `sensor` runs the official `zeek/zeek:lts` Docker image with
  `--net=host` instead. Logs land in `/var/log/zeek` on the host and feed
  Wazuh the same way Suricata's `eve.json` does.
- **Wazuh credentials file path**: the install script's generated
  credentials file name has moved around across Wazuh versions; if
  `/root/wazuh-credentials.txt` on `siem` is empty, run
  `tar -tf ~/wazuh-install-files.tar` on that VM to find it directly.
- **DHCP IPs**: VM IPs come from Multipass's DHCP and are generally stable
  for a VM's lifetime, but aren't guaranteed static. If you reboot the host
  Mac and IPs shift, re-run the relevant section of the script or manually
  update DNS/inventory.
- **Member DNS records**: `fs01` and `client01..N` register an A record in
  AD DNS when they join, and keep it current if their address changes:
  clients through sssd's dynamic DNS (daily), `fs01` through the
  `lab-dns-register.timer` systemd timer (after every boot and daily, using
  its machine account). There is no reverse (PTR) zone.
- **`samba-ad-dc` restarts itself**: a systemd drop-in on `dc01` sets
  `Restart=on-failure`, because a failed Samba restart (for example after
  a long suspend, when catch-up security updates restart it mid network
  reconfiguration) otherwise takes the whole domain down. If AD looks dead,
  check `systemctl status samba-ad-dc` and `journalctl -u samba-ad-dc`.
- **Member DNS points at `dc01`'s deploy-time IP**: `fs01`, `client01..N`,
  `sensor` and `redteam` send `lab.local` lookups to `dc01` via a
  systemd-resolved drop-in, `/etc/systemd/resolved.conf.d/lab-dns.conf`,
  which survives reboots. If `dc01`'s IP ever changes (DHCP, see above),
  edit `DNS=` in that file on each member and run `sudo systemctl restart
  systemd-resolved`.
- **VMs are patched at deploy time**: every template runs `package_upgrade`,
  and the deploy script reboots a VM right after provisioning if an update
  requires it (`[reboot]` in the output). This makes the deploy a few
  minutes longer.
- **Caldera needs a newer Node.js than Ubuntu ships**: the Magma web UI's
  Vite build needs Node 20.19+/22.12+, but Ubuntu 24.04's apt `nodejs` is
  18.x. `caldera` therefore installs a pinned, checksum-verified Node 22
  build from nodejs.org into `/usr/local`, and builds the UI during
  provisioning, so a finished cloud-init means port 8888 is answering.
  Caldera itself is cloned from `master` (its last release tag is much
  older), so a future upstream change can still break the build: check
  `/var/log/provision.log` on `caldera` first.
- **Velociraptor is pinned to a release**: its CLI flags change between
  versions, so `velociraptor.yaml.tmpl` pins `VELO_VERSION` (currently
  `v0.77.3`), whose `config generate --merge` and `user add --role` syntax
  was checked. When bumping it, re-check those with the new binary's
  `--help`. To reset the admin password by hand: `multipass exec
  velociraptor -- sudo velociraptor --config
  /etc/velociraptor/server.config.yaml user add --role administrator admin`.
- **Velociraptor agents follow the server's deploy-time IP**: the agents on
  `dc01`, `fs01` and the clients connect to `https://<velociraptor-ip>:8000/`
  as it was at deploy time. If that IP changes, update `server_urls` in
  `/etc/velociraptor/client.config.yaml` on each endpoint (or delete
  `velociraptor` and re-run the deploy script).
- **DCSync isn't reproducible against Samba 4.19 with impacket**:
  `impacket-secretsdump -just-dc` fails against this DC (a DRSUAPI parsing
  error), so there is no DCSync scenario or rule.
- **Semaphore on `mgmt` doesn't share ansible-ctrl's inventory/keys**: it
  runs its own containerized Ansible install. Add the lab's SSH key and a
  hosts inventory through Semaphore's own web UI after logging in, rather
  than expecting it to already know about the lab.

## Security

The lab is intentionally weak (see above), so seeded credentials and the
vulnerable web apps are not bugs. To report a real problem in the scripts or
templates, see [SECURITY.md](SECURITY.md).

## Ideas for extending this further

- **MISP** for threat-intel management -- deliberately left out of the
  automated build. It's the heaviest stack here (MariaDB + Valkey/Redis +
  Exim + misp-core + misp-modules) and its official images' arm64 support
  couldn't be confirmed at the time this was written, which on Apple
  Silicon risks either an outright failure or very slow QEMU-emulated
  containers. If you want to try it: `git clone
  https://github.com/MISP/misp-docker`, follow its own README, and check
  current arm64 status in that repo's issues first.
- **TheHive + Cortex** for case management -- would add the "alert becomes
  a case" workflow that neither Wazuh nor the above covers.
- **Metasploit** on `redteam` -- skipped from automatic provisioning
  (large, slow, unattended-install-unfriendly) but can be added with
  Rapid7's official installer once the box is up.
- **GPO-equivalent hardening baselines**: Samba AD supports GPOs to a
  degree; scripting their creation is out of scope here, but `samba-tool
  gpo` plus RSAT from a Windows admin box (outside Multipass) is the
  realistic path if you want that piece.
- **Real network visibility for the sensor**: move to a bridged-networking
  hypervisor (Proxmox/ESXi with a span port, or Multipass's `--network`
  bridge mode where supported) so Suricata/Zeek can actually see inter-VM
  traffic instead of just their own.
- **Real network segmentation + a firewall VM**: Multipass's default
  networking is one flat NAT subnet; true segmentation needs either
  host-level isolated bridge interfaces + a multi-NIC firewall VM (real
  topology, biggest lift) or per-host nftables policy enforcement on the
  existing flat network (logical segmentation, no host changes). Ask if
  you want either built out.
