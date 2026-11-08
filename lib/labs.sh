# shellcheck shell=bash
#
# lib/labs.sh -- the lab definitions: which VMs each lab builds at each size, what they are for, and
# how big they are. Sourced by deploy-soc-lab.sh (and the menu); needs bash 4+ (associative arrays).
# Sourcing only defines variables and functions.

# name -> "cpus mem disk template". The privesc boxes (privesc01..04) share the "privesc" entry.
declare -A VM_SPEC=(
    [ansible-ctrl]="2 2G 10G ansible-ctrl.yaml.tmpl"
    [dc01]="2 4G 20G dc01.yaml.tmpl"
    [fs01]="2 2G 15G fs01.yaml.tmpl"
    [siem]="4 6G 40G siem.yaml.tmpl"
    [sensor]="2 3G 15G sensor.yaml.tmpl"
    [redteam]="2 3G 20G redteam.yaml.tmpl"
    [vulntarget]="2 3G 15G vulntarget.yaml.tmpl"
    [lam]="1 1G 10G lam.yaml.tmpl"
    [caldera]="2 4G 20G caldera.yaml.tmpl"
    [velociraptor]="2 3G 20G velociraptor.yaml.tmpl"
    [mgmt]="2 2G 15G mgmt.yaml.tmpl"
    [gitea]="2 4G 30G gitea.yaml.tmpl"
    [prod]="2 2G 15G prod.yaml.tmpl"
    [honeypot]="1 1G 10G honeypot.yaml.tmpl"
    [mail]="2 2G 15G mail.yaml.tmpl"
    [iris]="2 4G 30G iris.yaml.tmpl"
    [webtarget2]="2 3G 20G webtarget2.yaml.tmpl"
    [privesc]="1 1G 10G privesc.yaml.tmpl"
    [arkime]="4 6G 60G arkime.yaml.tmpl"
    [dast]="2 2G 15G dast.yaml.tmpl"
)

# Clients: cpus mem disk template -- applied to every client of that kind.
CLIENT_SPEC="2 2G 15G client.yaml.tmpl"          # domain-joined workstations (client01..)
DEVCLIENT_SPEC="2 2G 15G devclient.yaml.tmpl"    # developer workstations, no domain (devclient01..)

# Deploy order of every VM any lab can create: the deploy script's stage order, then the newer VMs.
LAB_DEPLOY_ORDER=(ansible-ctrl siem dc01
    fs01 sensor redteam vulntarget lam caldera velociraptor mgmt gitea prod honeypot mail iris
    webtarget2 privesc01 privesc02 privesc03 privesc04 arkime dast)

LAB_NAMES=(soc forensics pentest devsecops)
declare -A LAB_TITLE=(
    [soc]="SOC / blue team"
    [forensics]="Network forensics"
    [pentest]="Pentest"
    [devsecops]="DevSecOps CI/CD"
)
declare -A LAB_PURPOSE=(
    [soc]="Build and tune detections, triage alerts and run an investigation from alert to case."
    [forensics]="Find attacks and data theft in packet captures and network logs.
Live traffic and public case PCAPs, analysed in Arkime, Zeek and tshark."
    [pentest]="Practise the whole attack chain against a weak company network: recon, web exploitation,
AD attacks, lateral movement and Linux privilege escalation. No SIEM: attacks aren't detected."
    [devsecops]="See how code, dependencies, containers and the pipeline itself are attacked and defended.
Gitea CI/CD with security scans, a vulnerable demo app and pipeline, and DAST."
)
declare -A LAB_CLIENT_KIND=([soc]=domain [forensics]=domain [pentest]=domain [devsecops]=developer)

# "<lab>:<size>" -> the VMs that size adds to the smaller one (clients are separate).
declare -A LAB_ADDS=(
    [soc:small]="dc01 siem sensor redteam"
    [soc:medium]="fs01 velociraptor iris honeypot"
    [soc:large]="mail caldera lam ansible-ctrl"
    [forensics:small]="sensor arkime dc01 redteam vulntarget"
    [forensics:medium]="fs01 mail honeypot"
    [forensics:large]="siem"
    [pentest:small]="redteam vulntarget dc01 privesc01"
    [pentest:medium]="fs01 webtarget2 privesc02 privesc03"
    [pentest:large]="mail privesc04"
    [devsecops:small]="gitea prod"
    [devsecops:medium]="dast"
    [devsecops:large]="siem"
)
declare -A LAB_CLIENTS=([small]=1 [medium]=3 [large]=6)

# "<lab>:<size>" -> minutes measured on a real deploy; overrides the rough estimate when set.
declare -A LAB_MEASURED_MINUTES=()

declare -A VM_ROLE=(
    [ansible-ctrl]="Ansible control node (inventory: ~/soc-lab-ansible)"
    [dc01]="Samba4 AD domain controller: DNS, Kerberos, LDAP"
    [fs01]="File server: Samba shares public + finance (domain member)"
    [siem]="Wazuh SIEM: manager, indexer, dashboard"
    [sensor]="Network sensor: Suricata + Zeek; web proxy (Squid :3128); legacy status page, FTP, telnet"
    [redteam]="Attacker box: nmap, impacket, bloodhound, netexec, hashcat, Sliver C2"
    [vulntarget]="Vulnerable web apps: DVWA + Juice Shop"
    [lam]="LDAP Account Manager (web UI for AD)"
    [caldera]="MITRE Caldera adversary emulation"
    [velociraptor]="Velociraptor DFIR server (agents on dc01, fs01, clients)"
    [mgmt]="Semaphore (Ansible web UI) + Uptime Kuma (status)"
    [gitea]="CI/CD: Gitea Git server, Actions runner, container registry"
    [prod]="CI/CD deploy target: runs the app container the pipeline ships"
    [honeypot]="Deception: OpenCanary (fake FTP 21, HTTP 80, SSH 2222, Telnet, MySQL, Redis, VNC)"
    [mail]="Company mail: Postfix (25/587), Dovecot IMAP (143, AD logins), Roundcube webmail"
    [iris]="DFIR-IRIS case management; Wazuh alerts of level 10+ arrive as IRIS alerts"
    [webtarget2]="More vulnerable web apps (WebGoat and others)"
    [privesc]="Linux privilege-escalation practice box: one misconfiguration, a flag file for root"
    [arkime]="Arkime full packet capture and viewer, analyst CLI tools (tshark, Zeek, ngrep, tcpflow), PCAP library"
    [dast]="OWASP ZAP dynamic scanner for the deployed app"
)

lab_names() { printf '%s\n' "${LAB_NAMES[@]}"; }
lab_title() { printf '%s\n' "${LAB_TITLE[$1]}"; }
lab_purpose() { printf '%s\n' "${LAB_PURPOSE[$1]}"; }
lab_client_kind() { printf '%s\n' "${LAB_CLIENT_KIND[$1]}"; }
lab_clients() { printf '%s\n' "${LAB_CLIENTS[$2]}"; }

# The VM_SPEC / VM_ROLE key for a VM name (privesc01..04 share "privesc").
vm_spec_key() {
    case "$1" in
        privesc[0-9]*) echo privesc ;;
        *) echo "$1" ;;
    esac
}

# lab_vms <lab> <size>: the lab's VMs for that size (cumulative), in deploy order, without clients.
lab_vms() {
    local lab="$1" size="$2" s name wanted=" "
    for s in small medium large; do
        wanted+="${LAB_ADDS[${lab}:${s}]} "
        [[ "${s}" == "${size}" ]] && break
    done
    for name in "${LAB_DEPLOY_ORDER[@]}"; do
        [[ "${wanted}" == *" ${name} "* ]] && echo "${name}"
    done
    return 0
}

# lab_totals <lab> <size>: "<vcpus> <ram_gb> <disk_gb>", clients included.
lab_totals() {
    local lab="$1" size="$2" name cpus mem disk spec n kind
    local tc=0 tm=0 td=0
    for name in $(lab_vms "${lab}" "${size}"); do
        read -r cpus mem disk _ <<< "${VM_SPEC[$(vm_spec_key "${name}")]}"
        tc=$((tc + cpus)); tm=$((tm + ${mem%G})); td=$((td + ${disk%G}))
    done
    kind="$(lab_client_kind "${lab}")"
    if [[ "${kind}" == developer ]]; then spec="${DEVCLIENT_SPEC}"; else spec="${CLIENT_SPEC}"; fi
    read -r cpus mem disk _ <<< "${spec}"
    n="${LAB_CLIENTS[${size}]}"
    echo "$((tc + n * cpus)) $((tm + n * ${mem%G})) $((td + n * ${disk%G}))"
}

# lab_estimate_minutes <lab> <size>: measured minutes if known, else a rough guess:
# 5 + 3 per VM and client, +25 for siem, +15 for caldera, +15 for arkime.
lab_estimate_minutes() {
    local key="$1:$2" name minutes
    if [[ -n "${LAB_MEASURED_MINUTES[${key}]:-}" ]]; then
        echo "${LAB_MEASURED_MINUTES[${key}]}"
        return 0
    fi
    minutes=$((5 + 3 * LAB_CLIENTS[$2]))
    for name in $(lab_vms "$1" "$2"); do
        minutes=$((minutes + 3))
        case "${name}" in
            siem) minutes=$((minutes + 25)) ;;
            caldera|arkime) minutes=$((minutes + 15)) ;;
        esac
    done
    echo "${minutes}"
}

# vm_role <vm>: one-line role. Clients get their own text.
vm_role() {
    case "$1" in
        devclient[0-9]*) echo "Developer workstation (git, editor, CLI tools); pushes to Gitea" ;;
        client[0-9]*) echo "Domain-joined workstation (sssd); Wazuh + Velociraptor agents" ;;
        *) printf '%s\n' "${VM_ROLE[$(vm_spec_key "$1")]:-}" ;;
    esac
}

# all_lab_vms: every VM name any lab can create, plus client01..20 and devclient01..20.
all_lab_vms() {
    local i
    printf '%s\n' "${LAB_DEPLOY_ORDER[@]}"
    for i in $(seq -w 1 20); do echo "client${i}"; done
    for i in $(seq -w 1 20); do echo "devclient${i}"; done
}

# lab_client_names <lab> <size>: the client VM names, client01.. (domain) or devclient01.. (developer).
lab_client_names() {
    local lab="$1" size="$2" prefix=client i n
    [[ "$(lab_client_kind "${lab}")" == developer ]] && prefix=devclient
    n="$(lab_clients "${lab}" "${size}")"
    for ((i = 1; i <= n; i++)); do printf '%s%02d\n' "${prefix}" "${i}"; done
}

# vm_spec <vm>: "cpus mem disk template" for a server or a client.
vm_spec() {
    case "$1" in
        devclient[0-9]*) echo "${DEVCLIENT_SPEC}" ;;
        client[0-9]*) echo "${CLIENT_SPEC}" ;;
        *) echo "${VM_SPEC[$(vm_spec_key "$1")]}" ;;
    esac
}

# lab_deploy_plan <lab> <size>: every VM (clients included) in the order the deploy launches them:
# the servers up to fs01, then the clients, then the rest.
lab_deploy_plan() {
    local name clients_done=0
    for name in $(lab_vms "$1" "$2"); do
        if ((!clients_done)) && [[ "${name}" != ansible-ctrl && "${name}" != siem && "${name}" != dc01 && "${name}" != fs01 ]]; then
            lab_client_names "$1" "$2"
            clients_done=1
        fi
        echo "${name}"
    done
    if ((!clients_done)); then lab_client_names "$1" "$2"; fi
    return 0
}

# running_lab_vms: the lab VM names (all_lab_vms) that Multipass lists right now. One `multipass list`.
running_lab_vms() {
    local listed name
    listed=" $(multipass list --format csv | awk -F, '$1 != "Name" && $1 != "" {printf "%s ", $1}') "
    for name in $(all_lab_vms); do
        [[ "${listed}" == *" ${name} "* ]] && echo "${name}"
    done
    return 0
}
