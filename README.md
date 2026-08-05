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
| `dc01`          | Samba4 AD DC -- DNS, Kerberos, LDAP for `LAB.LOCAL`     | -   |
| `fs01`          | File server (Samba shares, AD-authenticated)           | Y   |
| `client01..N`   | Domain-joined "workstations" -- count set by `NUM_CLIENTS` | Y |
| `siem`          | Wazuh manager + indexer + dashboard                    | -   |
| `sensor`        | Suricata + Zeek network monitoring + Wazuh agent       | -   |
| `redteam`       | Attacker box: nmap, impacket, bloodhound, netexec      | -   |
| `vulntarget`    | DVWA + Juice Shop in Docker, for web-attack telemetry  | -   |
| `lam`           | LDAP Account Manager -- web UI for `dc01`               | -   |
| `caldera`       | MITRE Caldera -- adversary emulation, ATT&CK-mapped     | -   |
| `velociraptor`  | Velociraptor -- endpoint DFIR/hunting server            | -   |
| `mgmt`          | Semaphore UI (Ansible web UI) + Uptime Kuma (status dashboard) | - |

Every domain-joined/monitored VM ships logs to `siem` via a Wazuh agent, so
detections can be built and tested end-to-end: attack on `redteam` or
`vulntarget` -> telemetry on `sensor`/endpoint agents -> alert in Wazuh.

Total footprint with everything enabled and `NUM_CLIENTS=2`: roughly
**29 vCPU / ~39GB RAM** allocated (not all used simultaneously). Edit
`VM_ENABLED` and `NUM_CLIENTS` at the top of `deploy-soc-lab.sh` to trim
this down -- a good minimal start is `ansible-ctrl`, `dc01`, `fs01`, `siem`,
plus 1 client, with everything else disabled.

### Adding more clients

Change one line in `deploy-soc-lab.sh`:

```bash
NUM_CLIENTS=5   # creates client01 through client05
```

That's it -- no other edits needed. The script renders `client.yaml.tmpl`
once per client with a zero-padded hostname (`client01`, `client02`, ...),
launches them all in parallel, and adds each to the generated Ansible
inventory under `[clients]`. Set `NUM_CLIENTS=0` to skip clients entirely.
`CLIENT_SPEC` (right below it) controls the CPU/RAM/disk applied to every
client uniformly.

## Usage

```bash
# 1. review/edit domain name + admin password + NUM_CLIENTS in deploy-soc-lab.sh
# 2. requires bash 4+ (macOS default bash is 3.2)
brew install bash multipass jq   # jq isn't required by the script but handy for ad-hoc queries
/opt/homebrew/bin/bash ./deploy-soc-lab.sh
```

The script launches VMs in dependency order (ansible-ctrl -> dc01 -> siem ->
everything else in parallel), polls `cloud-init status` at each dependency
point, and finishes by pushing a generated Ansible inventory to
`ansible-ctrl`.

To tear the whole lab down:

```bash
./teardown-soc-lab.sh
```

## Default lab accounts (change the password in the script before real use)

| Account | Notes |
|---|---|
| `Administrator` | Domain Admin, password set by `ADMIN_PASS` in the script |
| `alice.user`, `bob.user` | Plain domain users |
| `jane.finance` | Member of `Finance`, can reach the `finance` share on `fs01` |
| `mike.helpdesk` | Member of `HelpDesk` and `IT-Admins` |
| `svc_sql` | Service account with an SPN set (`MSSQLSvc/fs01...`) -- **intentionally kerberoastable** for detection practice |

All seeded passwords are `Passw0rd!23` except `svc_sql` (`SqlServ1ce#2024`).

## Add-on tool access

| Tool | URL / how to reach it | Credentials |
|---|---|---|
| Wazuh dashboard | `https://<siem-ip>/` | `multipass exec siem -- sudo cat /root/wazuh-credentials.txt` |
| DVWA | `http://<vulntarget-ip>/` | `admin` / `password`, then set difficulty from the setup page |
| Juice Shop | `http://<vulntarget-ip>:3000/` | no login needed to start |
| LDAP Account Manager | `http://<lam-ip>/lam` | Complete the one-time server-profile wizard first -- see below |
| MITRE Caldera | `http://<caldera-ip>:8888` | Auto-generated on first boot: `multipass exec caldera -- sudo journalctl -u caldera \| grep -i password` |
| Velociraptor | check `/etc/velociraptor/server.config.yaml` on that VM for the GUI port (commonly 8889) | `admin` / the lab `ADMIN_PASS` (auto-created, best-effort -- see rough edges) |
| Uptime Kuma | `http://<mgmt-ip>:3001` | set up your own admin account on first visit |
| Semaphore UI | `http://<mgmt-ip>:3000` | `admin` / the lab `ADMIN_PASS` |

**LDAP Account Manager first-time setup**: open the URL above and complete
the server profile wizard with:
- Tree suffix / Base DN: `DC=lab,DC=local` (adjust if you changed `DOMAIN_REALM`)
- Server URL: `ldap://<dc01-ip>:389`
- Bind DN: `administrator@lab.local`
- Bind password: the domain Administrator password
- LAM's own config-login password (separate from the above) defaults to `lam`

## Example detection/hunting scenarios to build against

- **Kerberoasting**: from `redteam`, request a TGS for `svc_sql`'s SPN with
  `impacket-GetUserSPNs -request` and crack it offline -- then build a
  Sentinel/Wazuh-style rule for excessive TGS requests with RC4 encryption.
- **Password spraying / brute force**: `hydra` against `fs01` SMB or `client0x`
  SSH, watch it land in Wazuh via the endpoint agents.
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
- **Endpoint DFIR**: once you've deployed a Velociraptor client (see rough
  edges below), run a live hunt across endpoints for a specific artifact
  after simulating compromise.
- **Config drift / hardening checks**: use the Ansible control node (or
  Semaphore's web UI on `mgmt`) to run CIS-style checks across
  `fs01`/`client01..N` and remediate drift.

## Known rough edges (read before you file a bug against yourself)

- **fs01 AD idmap**: `security = ads` + winbind idmap is one of the more
  finicky corners of Samba. If AD group ownership on `/srv/shares/finance`
  doesn't resolve, check `wbinfo -t` and `wbinfo -g` on `fs01` first.
- **sensor visibility**: Multipass VMs sit behind NAT with no shared bridge,
  so both Suricata and Zeek on `sensor` only see their own traffic, not a
  real span port. Treat it as host-based monitoring, not full network
  visibility.
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
- **DNS override doesn't survive a reboot on domain-joined/monitoring VMs**:
  each of `fs01`, `client01..N`, `sensor`, and `redteam` points its DNS at
  `dc01` two ways -- a netplan drop-in (meant to survive reboots) and a
  direct `resolvectl` call (guaranteed to take effect immediately, which is
  what provisioning actually relies on). The netplan drop-in is best-effort;
  if you reboot one of these VMs and AD-related lookups stop working,
  re-apply by hand: `multipass exec <name> -- sudo resolvectl dns <iface>
  <dc01-ip>` and `sudo resolvectl domain <iface> lab.local`.
- **Caldera's first boot takes a while**: `server.py --build` compiles the
  Magma web UI (npm/webpack) the *first time the systemd service actually
  starts*, which happens after cloud-init itself already reports done --
  so give it several extra minutes before assuming port 8888 is broken.
- **Velociraptor's exact CLI flags are version-sensitive**: the automated
  `user add` step may not match every release's syntax. If the admin
  account wasn't created, do it by hand: `multipass exec velociraptor --
  sudo velociraptor --config /etc/velociraptor/server.config.yaml user add
  admin --role administrator`.
- **Velociraptor client agents are not auto-deployed**: only the server is
  stood up. Deploying an endpoint agent is left as a deliberate step --
  extract a client config from the server (`velociraptor config show
  --client` or similar, check `velociraptor help config` for the exact
  current flag) and install it on `fs01`/`client01..N`.
- **Semaphore on `mgmt` doesn't share ansible-ctrl's inventory/keys**: it
  runs its own containerized Ansible install. Add the lab's SSH key and a
  hosts inventory through Semaphore's own web UI after logging in, rather
  than expecting it to already know about the lab.

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
- **AS-REP roasting target**: add a second service account with
  "Do not require Kerberos pre-authentication" set (via `ldbmodify` on
  `dc01` against `userAccountControl`) for a second classic AD attack path.
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
