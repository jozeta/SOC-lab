#!/usr/bin/env bash
#
# lab-info.sh -- print every lab VM (role, IP, how to log in), the domain
# accounts, and every web app (URL, credentials, and whether it answers).
#
# deploy-soc-lab.sh runs this at the end; run it again any time, e.g. after
# a host reboot when DHCP may have handed out new IPs. It reads live state
# from Multipass plus the settings deploy-soc-lab.sh saved in
# .rendered-cloud-init/lab.env, so it always matches the running lab.
#
# Output contains the lab's (intentionally weak) passwords.
#
set -euo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "ERROR: needs bash 4+:  /opt/homebrew/bin/bash ./lab-info.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_ENV="${SCRIPT_DIR}/.rendered-cloud-init/lab.env"
if [ ! -f "${LAB_ENV}" ]; then
    echo "ERROR: ${LAB_ENV} not found -- run deploy-soc-lab.sh first."
    exit 1
fi
# shellcheck source=/dev/null
source "${LAB_ENV}"   # DOMAIN_REALM, DOMAIN_NETBIOS, ADMIN_PASS, SEED_PASS, SVC_SQL_PASS, SVC_LEGACY_PASS

realm_lc="${DOMAIN_REALM,,}"

# Role of every VM the lab can create. Anything running that is not listed
# here is still printed (as "unknown") so nothing is ever left out.
declare -A ROLE=(
    [ansible-ctrl]="Ansible control node (inventory: ~/soc-lab-ansible)"
    [dc01]="Samba4 AD domain controller: DNS, Kerberos, LDAP (${DOMAIN_REALM})"
    [fs01]="File server: Samba shares public + finance (domain member)"
    [siem]="Wazuh SIEM: manager, indexer, dashboard"
    [sensor]="Network sensor: Suricata + Zeek; legacy office services (status page, FTP, telnet)"
    [redteam]="Attacker box: nmap, impacket, bloodhound, netexec, hashcat, Sliver C2"
    [vulntarget]="Vulnerable web apps: DVWA + Juice Shop"
    [lam]="LDAP Account Manager (web UI for AD)"
    [caldera]="MITRE Caldera adversary emulation"
    [velociraptor]="Velociraptor DFIR server (agents on dc01, fs01, clients)"
    [mgmt]="Semaphore (Ansible web UI) + Uptime Kuma (status)"
    [gitea]="CI/CD: Gitea Git server, Actions runner, container registry"
    [prod]="CI/CD deploy target: runs the app container the pipeline ships"
    [honeypot]="Deception: OpenCanary (fake FTP 21, HTTP 80, SSH 2222, Telnet, MySQL, Redis, VNC)"
)
ORDER=(ansible-ctrl siem dc01 fs01 client sensor redteam vulntarget lam caldera
       velociraptor mgmt gitea prod honeypot)

# name -> first IPv4, for every instance Multipass knows about
declare -A IP STATE
while IFS=, read -r name state ipv4 _rest; do
    [ "${name}" = "Name" ] && continue
    IP["${name}"]="${ipv4:-'-'}"
    STATE["${name}"]="${state}"
done < <(multipass list --format csv)

ip_of() { echo "${IP[$1]:--}"; }
exists() { [ -n "${IP[$1]:-}" ]; }

# HTTP(S) status of a URL as seen from the Mac: UP (2xx/3xx/401), else the code.
check() {
    local code
    code=$(curl -sk -o /dev/null -m 6 -w '%{http_code}' "$1" 2>/dev/null || true)
    case "${code}" in
        2??|3??|401) echo "UP" ;;
        000|"")      echo "DOWN" ;;
        *)           echo "HTTP ${code}" ;;
    esac
}

line() { printf '%*s\n' "$1" '' | tr ' ' '-'; }

# ---------------------------------------------------------------------------
echo
echo "SOC LAB -- ${DOMAIN_REALM} (NetBIOS ${DOMAIN_NETBIOS})"
echo
echo "VIRTUAL MACHINES  (log in to any VM with: multipass shell <name>)"
line 118
printf '%-13s %-8s %-16s %s\n' "NAME" "STATE" "IP" "ROLE"
line 118
declare -A printed
for key in "${ORDER[@]}"; do
    if [ "${key}" = "client" ]; then
        for name in $(printf '%s\n' "${!IP[@]}" | grep -E '^client[0-9]+$' | sort); do
            printf '%-13s %-8s %-16s %s\n' "${name}" "${STATE[${name}]}" "$(ip_of "${name}")" \
                "Domain-joined workstation (sssd); Wazuh + Velociraptor agents"
            printed["${name}"]=1
        done
        continue
    fi
    exists "${key}" || continue
    printf '%-13s %-8s %-16s %s\n' "${key}" "${STATE[${key}]}" "$(ip_of "${key}")" "${ROLE[${key}]}"
    printed["${key}"]=1
done
for name in $(printf '%s\n' "${!IP[@]}" | sort); do
    [ -n "${printed[${name}]:-}" ] && continue
    printf '%-13s %-8s %-16s %s\n' "${name}" "${STATE[${name}]}" "$(ip_of "${name}")" \
        "(unknown -- not part of this lab's definition)"
done
line 118

# ---------------------------------------------------------------------------
echo
echo "DOMAIN ACCOUNTS  (${DOMAIN_NETBIOS}\\<user> or <user>@${realm_lc}; domain logons work on fs01 and the clients)"
line 118
printf '%-16s %-20s %s\n' "ACCOUNT" "PASSWORD" "NOTES"
line 118
printf '%-16s %-20s %s\n' "Administrator" "${ADMIN_PASS}" "Domain Admin"
printf '%-16s %-20s %s\n' "alice.user"    "${SEED_PASS}" "Plain user"
printf '%-16s %-20s %s\n' "bob.user"      "${SEED_PASS}" "Plain user"
printf '%-16s %-20s %s\n' "jane.finance"  "${SEED_PASS}" "Finance group: can read \\\\fs01\\finance"
printf '%-16s %-20s %s\n' "mike.helpdesk" "${SEED_PASS}" "HelpDesk + IT-Admins"
printf '%-16s %-20s %s\n' "svc_sql"       "${SVC_SQL_PASS}" "SPN MSSQLSvc/fs01.${realm_lc}:1433, RC4 only -- kerberoastable"
printf '%-16s %-20s %s\n' "svc_legacy"    "${SVC_LEGACY_PASS}" "No Kerberos pre-auth -- AS-REP roastable"
line 118

# ---------------------------------------------------------------------------
# Generated staff (seed/labdata.py): the roster the deploy created.
ROSTER="${SCRIPT_DIR}/.rendered-cloud-init/lab-roster.json"
if [ -s "${ROSTER}" ] && command -v jq > /dev/null; then
    echo
    echo "GENERATED STAFF  ($(jq -r .company "${ROSTER}"), $(jq '.users | length' "${ROSTER}") people; same password everywhere they have an account: AD, Gitea for Engineering/IT, Juice Shop)"
    line 118
    printf '%-22s %-12s %-26s %-20s %-9s %s\n' "ACCOUNT" "DEPARTMENT" "TITLE" "PASSWORD" "DESK" "NOTES"
    line 118
    jq -r '.users | sort_by(.dept, .sam)[] |
        [.sam, .dept, .title, .password, (.client // "-"),
         ([(if .enabled then empty else "DISABLED (left \(.left))" end), (.weak // empty),
           (if .vpn then "VPN" else empty end)] | join(", "))] | @tsv' "${ROSTER}" |
        while IFS=$'\t' read -r sam dept title pass desk notes; do
            printf '%-22s %-12s %-26s %-20s %-9s %s\n' "${sam}" "${dept}" "${title}" "${pass}" "${desk}" "${notes}"
        done
    line 118
    if jq -e .services "${ROSTER}" > /dev/null; then
        echo "Legacy services on sensor:  FTP $(jq -r '.services.ftp_user + " / " + .services.ftp_pass' "${ROSTER}")" \
             "   telnet $(jq -r '.services.telnet_user + " / " + .services.telnet_pass' "${ROSTER}")"
    fi
    echo "Simulated activity never stops; what it did: /var/log/lab-activity.log on each client,"
    echo "/var/log/lab/ansible.log (ansible-ctrl), /var/log/lab-semaphore.log (mgmt), /var/log/lab-backup.log (fs01)."
fi

# ---------------------------------------------------------------------------
wazuh_admin="?" wazuh_api="?"
if exists siem; then
    creds=$(multipass exec siem -- sudo cat /root/wazuh-credentials.txt 2>&1 || true)
    wazuh_admin=$(printf '%s\n' "${creds}" | awk "/indexer_username: 'admin'/{getline; print}" \
        | sed -E "s/.*indexer_password: '(.*)'.*/\1/")
    wazuh_api=$(printf '%s\n' "${creds}" | awk "/api_username: 'wazuh-wui'/{getline; print}" \
        | sed -E "s/.*api_password: '(.*)'.*/\1/")
fi

echo
echo "WEB APPS"
line 118
printf '%-30s %-6s %-40s %s\n' "APP" "STATUS" "URL" "LOGIN"
line 118
app() {   # app <vm> <name> <url> <login>
    exists "$1" || return 0
    printf '%-30s %-6s %-40s %s\n' "$2" "$(check "$3")" "$3" "$4"
}
app siem         "Wazuh dashboard"      "https://$(ip_of siem)/"               "admin / ${wazuh_admin}"
app siem         "Wazuh API"            "https://$(ip_of siem):55000/"         "wazuh-wui / ${wazuh_api}"
app vulntarget   "DVWA"                 "http://$(ip_of vulntarget)/"          "admin / password  (database created at deploy)"
app vulntarget   "OWASP Juice Shop"     "http://$(ip_of vulntarget):3000/"     "register your own account"
app lam          "LDAP Account Manager" "http://$(ip_of lam)/lam/"             "LAM config password: lam; then Administrator / ${ADMIN_PASS}"
app caldera      "MITRE Caldera"        "http://$(ip_of caldera):8888/"        "red / admin  (also blue / admin, admin / admin)"
app velociraptor "Velociraptor"         "https://$(ip_of velociraptor):8889/"  "admin / ${ADMIN_PASS}"
app mgmt         "Semaphore UI"         "http://$(ip_of mgmt):3000/"           "admin / ${ADMIN_PASS}"
app mgmt         "Uptime Kuma"          "http://$(ip_of mgmt):3001/"           "create the admin account on first visit"
app gitea        "Gitea"                "http://$(ip_of gitea):3000/"          "labadmin / ${ADMIN_PASS}  (developer: dev / ${SEED_PASS})"
app gitea        "Gitea container registry" "http://$(ip_of gitea):3000/v2/"   "same as Gitea (docker login $(ip_of gitea):3000)"
app prod         "Demo app (deployed by CI/CD)" "http://$(ip_of prod):8080/"   "no login"
app sensor       "IT status page + downloads" "http://$(ip_of sensor)/"         "no login (FTP and telnet on the same VM: see above)"
app honeypot     "Honeypot web login (fake)" "http://$(ip_of honeypot)/"       "any -- every attempt is logged to Wazuh"
line 118
echo "Self-signed certificates (Wazuh, Velociraptor): accept the browser warning."
echo
