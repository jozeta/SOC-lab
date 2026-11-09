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

# Roles come from lib/labs.sh (vm_role). Anything running that is not a lab VM is still
# printed (as "unknown") so nothing is ever left out.
# shellcheck source=lib/labs.sh
source "${SCRIPT_DIR}/lib/labs.sh"
ORDER=()
for name in "${LAB_DEPLOY_ORDER[@]}"; do
    ORDER+=("${name}")
    if [ "${name}" = fs01 ]; then ORDER+=(client devclient); fi
done
LAB_STATE="${SCRIPT_DIR}/.lab-state"
lab_line=""
if [ -f "${LAB_STATE}" ]; then
    state_lab=$(sed -n 's/^lab=//p' "${LAB_STATE}" | head -n 1)
    state_size=$(sed -n 's/^size=//p' "${LAB_STATE}" | head -n 1)
    if [ -n "${LAB_TITLE[${state_lab}]:-}" ]; then lab_line="Lab: $(lab_title "${state_lab}") (${state_size})"; fi
fi

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
if [ -n "${lab_line}" ]; then echo "${lab_line}"; fi
echo
echo "VIRTUAL MACHINES  (log in to any VM with: multipass shell <name>)"
line 118
printf '%-13s %-8s %-16s %s\n' "NAME" "STATE" "IP" "ROLE"
line 118
declare -A printed
for key in "${ORDER[@]}"; do
    if [ "${key}" = "client" ] || [ "${key}" = "devclient" ]; then
        for name in $(printf '%s\n' "${!IP[@]}" | grep -E "^${key}[0-9]+\$" | sort || true); do
            printf '%-13s %-8s %-16s %s\n' "${name}" "${STATE[${name}]}" "$(ip_of "${name}")" "$(vm_role "${name}")"
            printed["${name}"]=1
        done
        continue
    fi
    exists "${key}" || continue
    role="$(vm_role "${key}")"
    if [ "${key}" = dc01 ]; then role+=" (${DOMAIN_REALM})"; fi
    printf '%-13s %-8s %-16s %s\n' "${key}" "${STATE[${key}]}" "$(ip_of "${key}")" "${role}"
    printed["${key}"]=1
done
for name in $(printf '%s\n' "${!IP[@]}" | sort); do
    [ -n "${printed[${name}]:-}" ] && continue
    printf '%-13s %-8s %-16s %s\n' "${name}" "${STATE[${name}]}" "$(ip_of "${name}")" \
        "(unknown -- not part of this lab's definition)"
done
line 118

# ---------------------------------------------------------------------------
if exists dc01; then
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
fi

# ---------------------------------------------------------------------------
# Generated staff (seed/labdata.py): the roster the deploy created.
ROSTER="${SCRIPT_DIR}/.rendered-cloud-init/lab-roster.json"
if [ -s "${ROSTER}" ] && command -v jq > /dev/null && jq -e '.users' "${ROSTER}" > /dev/null; then   # none in DevSecOps
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
    echo "/var/log/lab/ansible.log (ansible-ctrl), /var/log/lab-semaphore.log (mgmt), /var/log/lab-backup.log (fs01),"
    echo "/var/log/lab-velociraptor.log (hunts the simulated analyst started), /var/log/lab-mailflow.log (mail)."
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
kuma_login="create the admin account on first visit"   # until the deploy's test-data stage has run
if [ -s "${ROSTER}" ] && command -v jq > /dev/null && jq -e .services.kuma_pass "${ROSTER}" > /dev/null; then
    kuma_login=$(jq -r '.services.kuma_user + " / " + .services.kuma_pass' "${ROSTER}")
fi
app() {   # app <vm> <name> <url> <login>
    exists "$1" || return 0
    printf '%-30s %-6s %-40s %s\n' "$2" "$(check "$3")" "$3" "$4"
}
app siem         "Wazuh dashboard"      "https://$(ip_of siem)/"               "admin / ${wazuh_admin}"
app siem         "Wazuh API"            "https://$(ip_of siem):55000/"         "wazuh-wui / ${wazuh_api}"
app vulntarget   "DVWA"                 "http://$(ip_of vulntarget)/"          "admin / password  (database created at deploy)"
app vulntarget   "OWASP Juice Shop"     "http://$(ip_of vulntarget):3000/"     "register your own account"
app webtarget2   "OWASP WebGoat"        "http://$(ip_of webtarget2):8080/WebGoat/" "register your own account"
app webtarget2   "WebWolf (WebGoat helper)" "http://$(ip_of webtarget2):9090/WebWolf/" "your WebGoat account"
app webtarget2   "OWASP VulnerableApp"  "http://$(ip_of webtarget2):9091/VulnerableApp/" "no login"
app lam          "LDAP Account Manager" "http://$(ip_of lam)/lam/"             "Administrator / ${ADMIN_PASS} or any IT staff account (config password: lam)"
app caldera      "MITRE Caldera"        "http://$(ip_of caldera):8888/"        "red / admin  (also blue / admin, admin / admin)"
app velociraptor "Velociraptor"         "https://$(ip_of velociraptor):8889/"  "admin / ${ADMIN_PASS}"
app mgmt         "Semaphore UI"         "http://$(ip_of mgmt):3000/"           "admin / ${ADMIN_PASS}"
app mgmt         "Uptime Kuma"          "http://$(ip_of mgmt):3001/"           "${kuma_login}"
gitea_devs="dev"
if exists devclient01; then gitea_devs="dev, dev01..dev$(printf '%s\n' "${!IP[@]}" | sed -n 's/^devclient//p' | sort | tail -n 1)"; fi
app gitea        "Gitea"                "http://$(ip_of gitea):3000/"          "labadmin / ${ADMIN_PASS}  (developers: ${gitea_devs} / ${SEED_PASS})"
app gitea        "Gitea container registry" "http://$(ip_of gitea):3000/v2/"   "same as Gitea (docker login $(ip_of gitea):3000)"
app prod         "Demo app (deployed by CI/CD)" "http://$(ip_of prod):8080/"   "no login"
app sensor       "IT status page + downloads" "http://$(ip_of sensor)/"         "no login (FTP and telnet on the same VM: see above)"
app mail         "Webmail (Roundcube)"  "http://$(ip_of mail)/roundcube/"     "any staff account (sAMAccountName + AD password); IMAP/SMTP: $(ip_of mail)"
app iris         "DFIR-IRIS"            "https://$(ip_of iris)/"               "administrator / ${ADMIN_PASS}"
app honeypot     "Honeypot web login (fake)" "http://$(ip_of honeypot)/"       "any -- every attempt is logged to Wazuh"
app arkime       "Arkime (packet capture)" "https://$(ip_of arkime):8005/"     "admin / ${ADMIN_PASS}"
line 118
echo "Self-signed certificates (Wazuh, Velociraptor, IRIS, Arkime): accept the browser warning."
if exists devclient01; then
    echo "DevSecOps: multipass shell devclient01, then cd demo-app (Gitea user dev01; devclientNN is devNN). Every push to"
    echo "main runs the pipeline (Actions on gitea); prod only runs images signed with the lab's cosign key. Exercises: README."
fi
if exists dast; then
    echo "DAST: OWASP ZAP baseline scan of prod every 2 hours -- multipass exec dast -- ls /var/lib/zap/reports/latest/"
    echo "(report.html, report.json); one summary line per scan in /var/log/lab-zap.log."
fi
if exists arkime; then
    echo "Case PCAPs: /srv/pcaps/cases on arkime (zip passwords in INDEX.txt). Members route through sensor, which"
    echo "copies their traffic to Arkime; redteam's attack scenario runs every 30 min (/var/log/lab-attack.log)."
fi
echo
