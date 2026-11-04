#!/usr/bin/env bash
#
# deploy-soc-lab.sh
#
# Builds a multi-VM SOC lab on macOS using Multipass + cloud-init:
#   - Samba4 Active Directory domain controller
#   - Domain-joined file server (Samba shares) and N clients (NUM_CLIENTS below)
#   - Wazuh SIEM (manager + indexer + dashboard)
#   - Suricata + Zeek network sensor
#   - Ansible control node (config management) with a generated inventory
#   - A red team box and a vulnerable-app target (DVWA + Juice Shop)
#   - LDAP Account Manager (web UI for the AD DC)
#   - MITRE Caldera (adversary emulation)
#   - Velociraptor (endpoint DFIR/hunting server)
#   - mgmt: Semaphore UI (Ansible web UI) + Uptime Kuma (status dashboard)
#
# MISP is intentionally NOT included -- see README "Ideas for extending
# this further" for why and how to add it yourself if you want it.
#
# Requires: Multipass installed, bash 4+ (macOS ships bash 3.2 -- see check
# below), and outbound internet access from the VMs (default Multipass NAT).
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "ERROR: this script needs bash 4+. macOS ships bash 3.2 by default."
    echo "Install a newer bash and re-run, e.g.:"
    echo "    brew install bash"
    echo "    /opt/homebrew/bin/bash ./deploy-soc-lab.sh"
    exit 1
fi

if ! command -v multipass &>/dev/null; then
    echo "ERROR: multipass not found. Install Multipass 1.16.x first (see README)."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="${SCRIPT_DIR}/cloud-init-templates"
RENDERED_DIR="${SCRIPT_DIR}/.rendered-cloud-init"
mkdir -p "${RENDERED_DIR}"

# ---------------------------------------------------------------------------
# Lab configuration -- edit these to taste
# ---------------------------------------------------------------------------
IMAGE="24.04"                    # Ubuntu image Multipass will launch
DOMAIN_REALM="LAB.LOCAL"         # AD realm (uppercase by convention)
DOMAIN_NETBIOS="LAB"             # AD NetBIOS / workgroup name
ADMIN_PASS="ChangeMe!2024#Lab"   # CHANGE THIS. Domain Administrator password.
                                 # Must not contain " $ ` \ or a newline (see check below).

# Seeded lab accounts (intentionally weak; see README "Default lab accounts").
SEED_PASS='Passw0rd!23'          # alice.user, bob.user, jane.finance, mike.helpdesk
SVC_SQL_PASS='SqlServ1ce#2024'   # svc_sql: kerberoastable (SPN, RC4 only)
SVC_LEGACY_PASS='Backup2024!'    # svc_legacy: AS-REP roastable (no pre-auth)

# How long one VM may take to finish provisioning (cloud-init) before the
# deploy treats it as failed. siem and caldera are the slow ones.
READY_TIMEOUT=2700               # seconds (45 minutes)

# Stage-4 VMs are launched one at a time: never run several `multipass`
# commands at once. The daemon (1.16.4 and 1.17.0 on macOS 27) corrupts its
# heap and crashes under concurrent launches, then hangs on the orphaned VMs.
# Each launch waits only this long -- enough to boot and start cloud-init --
# and the VM keeps provisioning while the next one launches. Don't set it
# too low: a launch that times out before the VM has finished booting
# leaves the VM permanently in "Unknown" state.
STAGE4_LAUNCH_TIMEOUT=120        # seconds

# How many domain-joined "workstation" clients to create: client01, client02,
# ... up through this count. Set to 0 to skip clients entirely. This is the
# only thing you need to touch to add more -- no other edits required.
NUM_CLIENTS=2
CLIENT_SPEC="2 2G 15G client.yaml.tmpl"   # cpus mem disk template -- applied to every client

# Toggle other VMs on/off here if your Mac doesn't have room for all of them.
# A reasonable "core" set to start with: ansible-ctrl, dc01, fs01, siem
# (plus NUM_CLIENTS above). Everything else is additive.
declare -A VM_ENABLED=(
    [ansible-ctrl]=true
    [dc01]=true
    [fs01]=true
    [siem]=true
    [sensor]=true
    [redteam]=true
    [vulntarget]=true
    [lam]=true
    [caldera]=true
    [velociraptor]=true
    [mgmt]=true
    [gitea]=true      # CI/CD: Git server, Actions runner, container registry
    [prod]=true       # CI/CD: deploy target for the pipeline
    [honeypot]=true   # deception: OpenCanary fake services
)

# name -> "cpus mem disk template"
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
)

# The templates embed ADMIN_PASS as ADMIN_PASS="..." inside bash scripts, so
# these characters would break provisioning (or run as shell code there).
for pw_var in ADMIN_PASS SEED_PASS SVC_SQL_PASS SVC_LEGACY_PASS; do
    pw="${!pw_var}"
    if [[ "${pw}" == *[\"\$\`\\]* || "${pw}" == *$'\n'* ]]; then
        echo "ERROR: ${pw_var} must not contain \" \$ \` \\ or a newline."
        exit 1
    fi
done

# Settings lab-info.sh needs to describe this lab (owner-only: has passwords).
(umask 077 && {
    printf 'DOMAIN_REALM=%q\nDOMAIN_NETBIOS=%q\nADMIN_PASS=%q\n' "${DOMAIN_REALM}" "${DOMAIN_NETBIOS}" "${ADMIN_PASS}"
    printf 'SEED_PASS=%q\nSVC_SQL_PASS=%q\nSVC_LEGACY_PASS=%q\n' "${SEED_PASS}" "${SVC_SQL_PASS}" "${SVC_LEGACY_PASS}"
} > "${RENDERED_DIR}/lab.env")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

render_template() {
    # Plain string replacement with quoted operands, so characters such as
    # & | / \ in values are inserted literally. (The previous sed-based
    # version turned `&` into the placeholder text and choked on `|`.)
    local template="$1" output="$2"
    shift 2
    local content pair key value
    content="$(<"${template}")"
    for pair in "$@"; do
        key="${pair%%=*}"
        value="${pair#*=}"
        content="${content//"__${key}__"/"${value}"}"
    done
    # Rendered files contain the lab passwords: keep them owner-only.
    (umask 077 && printf '%s\n' "${content}" > "${output}")
}

run_with_timeout() {
    # Run a command, killing it after $1 seconds. Guards the deploy against
    # `multipass exec` calls that never return (seen on Multipass 1.17).
    local secs="$1" pid watchdog rc=0
    shift
    "$@" &
    pid=$!
    (sleep "${secs}"; kill "${pid}" 2>/dev/null) >/dev/null 2>&1 &
    watchdog=$!
    wait "${pid}" || rc=$?
    kill "${watchdog}" 2>/dev/null || true
    wait "${watchdog}" 2>/dev/null || true
    return "${rc}"
}

launch_vm() {
    # Optional 6th argument: how long `multipass launch` itself may wait.
    # Defaults to READY_TIMEOUT, i.e. wait for provisioning to finish.
    local name="$1" cpus="$2" mem="$3" disk="$4" cloud_init="$5"
    local launch_timeout="${6:-${READY_TIMEOUT}}"
    local log="${RENDERED_DIR}/${name}.launch.log"
    if multipass info "${name}" &>/dev/null; then
        echo "  [skip]   ${name} already exists"
        return 0
    fi
    echo "  [launch] ${name} (${cpus} vCPU / ${mem} RAM / ${disk} disk)"
    # `multipass launch` waits for cloud-init and by default gives up after
    # 5 minutes -- far less than siem or caldera need -- so pass our own limit.
    if multipass launch "${IMAGE}" --name "${name}" --cpus "${cpus}" \
        --memory "${mem}" --disk "${disk}" --cloud-init "${cloud_init}" \
        --timeout "${launch_timeout}" > "${log}" 2>&1; then
        return 0
    fi
    # A launch that times out after boot leaves the VM running and
    # provisioning; if it is Running, let wait_ready judge it. (One that timed
    # out mid-boot is stuck in "Unknown" and counts as a failed launch.)
    local state
    state=$(multipass info "${name}" 2>&1 | awk '/^State:/{print $2}') || true
    if [ "${state}" = "Running" ]; then
        if [ "${launch_timeout}" -lt "${READY_TIMEOUT}" ]; then
            echo "  [started] ${name} is provisioning in the background"
        else
            echo "  [WARN]   ${name}: launch reported an error, but the VM exists -- checking cloud-init instead"
            tail -n 3 "${log}" | tr '\r' '\n' | grep -v '^[[:space:]]*$' | tail -n 1 | sed 's/^/           /'
        fi
        return 0
    fi
    echo "  [ERROR]  ${name} failed to launch:"
    tr '\r' '\n' < "${log}" | grep -v '^[[:space:]]*$' | tail -n 3 | sed 's/^/           /'
    return 1
}

wait_ready() {
    local name="$1" status deadline
    echo "  [wait]   ${name} finishing cloud-init (siem/caldera can take a long while)..."
    deadline=$((SECONDS + READY_TIMEOUT))
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        # stderr goes into the pipe, never to /dev/null: `multipass exec`
        # (1.16.4 and 1.17.0) hangs forever when its output goes to /dev/null.
        status=$(run_with_timeout 60 multipass exec "${name}" -- cloud-init status 2>&1 \
            | awk '/^status:/{print $2}') || true
        case "${status}" in
            done)
                return 0 ;;
            error)
                echo "  [ERROR]  ${name}: cloud-init reported an error."
                echo "           Check: multipass exec ${name} -- sudo cat /var/log/provision.log"
                return 1 ;;
        esac
        sleep 10
    done
    echo "  [ERROR]  ${name}: not ready after $((READY_TIMEOUT / 60)) minutes."
    echo "           Check: multipass exec ${name} -- cloud-init status"
    echo "                  multipass exec ${name} -- sudo cat /var/log/provision.log"
    return 1
}

reboot_if_required() {
    # The templates run `package_upgrade`; if that pulled in an update that
    # needs a reboot (kernel, libc, ...), reboot now, during the deploy,
    # instead of leaving it pending for the middle of a lab session.
    local name="$1"
    if ! run_with_timeout 60 multipass exec "${name}" -- test -f /var/run/reboot-required; then
        return 0
    fi
    echo "  [reboot] ${name}: rebooting to finish installing updates"
    if ! multipass restart "${name}" --timeout 600 > "${RENDERED_DIR}/${name}.restart.log" 2>&1; then
        echo "  [ERROR]  ${name}: restart failed:"
        tr '\r' '\n' < "${RENDERED_DIR}/${name}.restart.log" | grep -v '^[[:space:]]*$' | tail -n 2 | sed 's/^/           /'
        return 1
    fi
    wait_ready "${name}"
}

install_wazuh_rules() {
    # Push the lab's custom decoders and rules (wazuh/ in this repo) to the
    # manager, check that they load, then restart it. Safe to re-run.
    local f
    for f in soc_lab_decoders.xml soc_lab_rules.xml; do
        multipass transfer "${SCRIPT_DIR}/wazuh/${f}" "siem:/tmp/${f}"
    done
    multipass exec siem -- sudo bash -c '
        install -o wazuh -g wazuh -m 660 /tmp/soc_lab_decoders.xml /var/ossec/etc/decoders/
        install -o wazuh -g wazuh -m 660 /tmp/soc_lab_rules.xml /var/ossec/etc/rules/
        if /var/ossec/bin/wazuh-analysisd -t 2>&1 | grep -E "ERROR|CRITICAL"; then exit 1; fi
        systemctl restart wazuh-manager'
    echo "  [rules]  SOC-lab Wazuh rules installed on siem"
}

install_velociraptor_clients() {
    # Install the Velociraptor endpoint agent (the .deb the velociraptor VM
    # built with its client config embedded) on the given VMs, one at a time.
    local pkg="${RENDERED_DIR}/velociraptor-client.deb" name
    multipass transfer "velociraptor:$(multipass exec velociraptor -- sh -c 'ls /opt/velociraptor-pkg/*.deb')" "${pkg}"
    for name in "$@"; do
        multipass transfer "${pkg}" "${name}:/tmp/velociraptor-client.deb"
        multipass exec "${name}" -- sudo sh -c \
            'DEBIAN_FRONTEND=noninteractive dpkg -i /tmp/velociraptor-client.deb > /tmp/velociraptor-client.log 2>&1 && rm -f /tmp/velociraptor-client.deb'
        echo "  [velo]   ${name}: Velociraptor client installed"
    done
}

wire_cicd() {
    # Connect the pipeline on gitea to prod (deploy key, registry access),
    # push the demo app, and wait for the first run to deploy it.
    local prod_ip gitea_ip wire pubkey token
    prod_ip=$(get_ip prod)
    gitea_ip=$(get_ip gitea)
    wire=$(multipass exec gitea -- sudo /root/cicd-wire.sh "${prod_ip}")
    pubkey=$(printf '%s\n' "${wire}" | sed -n 's/^DEPLOY_PUBKEY=//p')
    token=$(printf '%s\n' "${wire}" | sed -n 's/^REGISTRY_TOKEN=//p')
    multipass exec prod -- sudo /root/prod-wire.sh "${gitea_ip}" "${pubkey}" "${token}"
    multipass exec gitea -- sudo /root/cicd-seed.sh
    echo "  [cicd]   waiting for the pipeline to deploy demo-app to prod (up to 20 minutes)..."
    for _ in $(seq 1 120); do
        if curl -fs -m 5 -o /dev/null "http://${prod_ip}:8080/health"; then
            echo "  [cicd]   demo-app is live: http://${prod_ip}:8080/"
            return 0
        fi
        sleep 10
    done
    echo "  [WARN]   demo-app is not live yet -- check the run under Actions: http://${gitea_ip}:3000/labadmin/demo-app/actions"
}

seed_vm() {
    # seed_vm <vm> <role> [arg]: run one seed/labdata.py role on a VM. The
    # config and roster hold passwords, so they are deleted again afterwards.
    local vm="$1" role="$2" arg="${3:-}" log="${RENDERED_DIR}/seed-$1.log"
    multipass transfer "${SCRIPT_DIR}/seed/labdata.py" "${RENDERED_DIR}/labdata-config.json" \
        "${RENDERED_DIR}/lab-roster.json" "${vm}:/tmp/"
    : > "${log}" && chmod 600 "${log}"   # an error message can contain a password
    if multipass exec "${vm}" -- sudo sh -c "python3 /tmp/labdata.py ${role} /tmp/labdata-config.json /tmp/lab-roster.json ${arg}; rc=\$?; rm -f /tmp/labdata.py /tmp/labdata-config.json /tmp/lab-roster.json; exit \$rc" \
        >> "${log}" 2>&1; then
        echo "  [data]   ${vm}: $(tail -n 1 "${log}")"
    else
        echo "  [WARN]   ${vm}: test data failed -- see ${log}"
    fi
}

seed_lab_data() {
    # Random, current test data on every VM where it makes sense (see
    # seed/labdata.py). The staff roster is generated once per lab and kept in
    # .rendered-cloud-init/lab-roster.json, so a re-run adds nothing twice.
    local roster="${RENDERED_DIR}/lab-roster.json" name ips ca
    if [ ! -s "${roster}" ]; then
        multipass transfer "${SCRIPT_DIR}/seed/labdata.py" dc01:/tmp/labdata.py
        (umask 077 && multipass exec dc01 -- python3 /tmp/labdata.py roster "${RANDOM}${RANDOM}" "${NUM_CLIENTS}" \
            > "${roster}")
    fi
    if ! jq -e '.services.kuma_pass' "${roster}" > /dev/null; then   # roster from an older version
        multipass transfer "${SCRIPT_DIR}/seed/labdata.py" "${roster}" dc01:/tmp/
        (umask 077 && multipass exec dc01 -- sh -c \
            'python3 /tmp/labdata.py services /tmp/lab-roster.json; rm -f /tmp/lab-roster.json' \
            > "${roster}.services" \
            && jq --slurpfile s "${roster}.services" '.services = $s[0]' "${roster}" > "${roster}.new" \
            && mv "${roster}.new" "${roster}")
        rm -f "${roster}.services"
    fi
    ips=$(for name in ansible-ctrl dc01 fs01 siem sensor redteam vulntarget lam caldera velociraptor \
                      mgmt gitea prod honeypot "${CLIENT_NAMES[@]}"; do
              is_enabled "${name}" || [[ "${name}" == client* ]] || continue
              printf '%s\n%s\n' "${name}" "$(get_ip "${name}")"
          done | jq -Rn '[inputs] | [range(0; length; 2) as $i | {(.[$i]): .[$i + 1]}] | add')
    # dc01's Samba CA, so lam can verify LDAPS instead of turning checks off.
    ca=$(multipass exec dc01 -- sudo cat /var/lib/samba/private/tls/ca.pem)
    (umask 077 && jq -n --arg realm "${DOMAIN_REALM}" --arg admin "${ADMIN_PASS}" \
        --arg seed "${SEED_PASS}" --arg sql "${SVC_SQL_PASS}" --arg legacy "${SVC_LEGACY_PASS}" \
        --argjson ips "${ips}" --arg ca "${ca}" \
        '{realm: $realm, admin_pass: $admin, seed_pass: $seed, svc_sql_pass: $sql,
          svc_legacy_pass: $legacy, ips: $ips, dc_ca_pem: $ca}' \
        > "${RENDERED_DIR}/labdata-config.json")
    seed_vm dc01 dc01                         # first: everyone else uses its accounts
    if is_enabled fs01; then seed_vm fs01 fs01; fi
    if is_enabled sensor; then seed_vm sensor sensor; fi   # before the clients: they use its services
    for name in "${CLIENT_NAMES[@]}"; do seed_vm "${name}" client "${name}"; done
    if is_enabled gitea; then seed_vm gitea gitea; fi
    if is_enabled mgmt; then seed_vm mgmt mgmt; fi
    if is_enabled vulntarget; then seed_vm vulntarget vulntarget; fi
    if is_enabled ansible-ctrl; then seed_vm ansible-ctrl ansible-ctrl; fi
    if is_enabled lam; then seed_vm lam lam; fi
    if is_enabled velociraptor; then seed_vm velociraptor velociraptor; fi   # after its agents (stage 5)
    authorize_activity_keys
}

authorize_activity_keys() {
    # The simulated admins (clients) and Semaphore (mgmt) SSH to the servers
    # as ubuntu with their own keys. Never to honeypot (nothing legitimate
    # touches it) or redteam.
    local keys="" name key
    for name in "${CLIENT_NAMES[@]}" mgmt; do
        is_enabled "${name}" || [[ "${name}" == client* ]] || continue
        key=$(multipass exec "${name}" -- sudo cat /etc/lab-activity-key/id_ed25519.pub 2>&1 || true)
        if [[ "${key}" == ssh-ed25519* ]]; then keys+="${key}"$'\n'; fi
    done
    [ -n "${keys}" ] || return 0
    for name in ansible-ctrl dc01 fs01 siem sensor vulntarget lam caldera velociraptor mgmt gitea prod \
                "${CLIENT_NAMES[@]}"; do
        is_enabled "${name}" || [[ "${name}" == client* ]] || continue
        printf '%s' "${keys}" | multipass exec "${name}" -- sudo sh -c '
            f=/home/ubuntu/.ssh/authorized_keys
            while read -r k; do grep -qxF "$k" "$f" || printf "%s\n" "$k" >> "$f"; done'
    done
    echo "  [data]   activity SSH keys authorized on the servers"
}

STAGE4_NAMES=()
LAUNCH_FAILED=()

launch_stage4() {
    # Launch one stage-4 VM (sequentially -- see STAGE4_LAUNCH_TIMEOUT) and
    # record the result, so a failed launch is reported at the end.
    local name="$1"
    STAGE4_NAMES+=("${name}")
    launch_vm "$@" "${STAGE4_LAUNCH_TIMEOUT}" && return 0
    # Seen in testing (Multipass 1.16.4, macOS 27): a guest occasionally boots
    # but never gets a DHCP lease, so the VM is stuck in "Unknown". The same
    # launch succeeds when repeated, so retry once with a fresh VM.
    if multipass info "${name}" &>/dev/null; then
        echo "  [retry]  ${name}: deleting the stuck VM and launching it once more"
        if multipass delete --purge "${name}" \
            && launch_vm "$@" "${STAGE4_LAUNCH_TIMEOUT}"; then
            return 0
        fi
    fi
    LAUNCH_FAILED+=("${name}")
}

get_ip() {
    multipass info "$1" | awk '/IPv4/{print $2; exit}'
}

is_enabled() {
    [ "${VM_ENABLED[$1]:-false}" = "true" ]
}

client_name() {
    printf "client%02d" "$1"
}

echo "== Stage 1: Ansible control node =="
ANSIBLE_PUBKEY=""
if is_enabled ansible-ctrl; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[ansible-ctrl]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/ansible-ctrl.yaml"
    launch_vm ansible-ctrl "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/ansible-ctrl.yaml"
    wait_ready ansible-ctrl
    reboot_if_required ansible-ctrl
    ANSIBLE_PUBKEY=$(multipass exec ansible-ctrl -- cat /home/ubuntu/.ssh/id_ed25519.pub)
else
    echo "  ansible-ctrl disabled -- other VMs will have no injected management key."
fi

echo "== Stage 2: Wazuh SIEM =="
SIEM_IP=""
if is_enabled siem; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[siem]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/siem.yaml" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm siem "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/siem.yaml"
    wait_ready siem
    reboot_if_required siem
    install_wazuh_rules
    SIEM_IP=$(get_ip siem)
    echo "  siem IP: ${SIEM_IP}"
else
    echo "  siem disabled -- agents on other VMs will have no manager to report to."
fi

echo "== Stage 3: Active Directory domain controller =="
DC_IP=""
if is_enabled dc01; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[dc01]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/dc01.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DOMAIN_NETBIOS=${DOMAIN_NETBIOS}" \
        "ADMIN_PASS=${ADMIN_PASS}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
        "SEED_PASS=${SEED_PASS}" "SVC_SQL_PASS=${SVC_SQL_PASS}" "SVC_LEGACY_PASS=${SVC_LEGACY_PASS}" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm dc01 "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/dc01.yaml"
    wait_ready dc01
    reboot_if_required dc01
    DC_IP=$(get_ip dc01)
    echo "  dc01 IP: ${DC_IP}"
else
    echo "  dc01 disabled -- domain-joined VMs below will fail to join, and lam"
    echo "  will have nothing to point at."
fi

echo "== Stage 4: clients, file server, sensor, red team, vuln target, and add-on tools =="

if is_enabled fs01; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[fs01]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/fs01.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DOMAIN_NETBIOS=${DOMAIN_NETBIOS}" \
        "DC_IP=${DC_IP}" "ADMIN_PASS=${ADMIN_PASS}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 fs01 "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/fs01.yaml"
fi

CLIENT_NAMES=()
if [ "${NUM_CLIENTS}" -gt 0 ]; then
    read -r c_cpus c_mem c_disk c_tmpl <<< "${CLIENT_SPEC}"
    for ((i = 1; i <= NUM_CLIENTS; i++)); do
        name=$(client_name "${i}")
        CLIENT_NAMES+=("${name}")
        render_template "${TEMPLATE_DIR}/${c_tmpl}" "${RENDERED_DIR}/${name}.yaml" \
            "HOSTNAME=${name}" "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" \
            "ADMIN_PASS=${ADMIN_PASS}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
            "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
        launch_stage4 "${name}" "${c_cpus}" "${c_mem}" "${c_disk}" "${RENDERED_DIR}/${name}.yaml"
    done
fi

if is_enabled sensor; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[sensor]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/sensor.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 sensor "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/sensor.yaml"
fi

if is_enabled redteam; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[redteam]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/redteam.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 redteam "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/redteam.yaml"
fi

if is_enabled vulntarget; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[vulntarget]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/vulntarget.yaml" \
        "WAZUH_MANAGER_IP=${SIEM_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 vulntarget "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/vulntarget.yaml"
fi

if is_enabled lam; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[lam]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/lam.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 lam "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/lam.yaml"
fi

if is_enabled caldera; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[caldera]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/caldera.yaml" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 caldera "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/caldera.yaml"
fi

if is_enabled velociraptor; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[velociraptor]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/velociraptor.yaml" \
        "ADMIN_PASS=${ADMIN_PASS}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 velociraptor "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/velociraptor.yaml"
fi

if is_enabled mgmt; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[mgmt]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/mgmt.yaml" \
        "ADMIN_PASS=${ADMIN_PASS}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 mgmt "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/mgmt.yaml"
fi

if is_enabled gitea; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[gitea]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/gitea.yaml" \
        "ADMIN_PASS=${ADMIN_PASS}" "SEED_PASS=${SEED_PASS}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 gitea "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/gitea.yaml"
fi

if is_enabled prod; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[prod]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/prod.yaml" \
        "WAZUH_MANAGER_IP=${SIEM_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 prod "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/prod.yaml"
fi

if is_enabled honeypot; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[honeypot]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/honeypot.yaml" \
        "WAZUH_MANAGER_IP=${SIEM_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_stage4 honeypot "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/honeypot.yaml"
fi

NOT_READY=()
for name in "${STAGE4_NAMES[@]}"; do
    if [[ " ${LAUNCH_FAILED[*]} " == *" ${name} "* ]]; then
        continue
    fi
    if ! wait_ready "${name}" || ! reboot_if_required "${name}"; then
        NOT_READY+=("${name}")
    fi
done

if [ "${#LAUNCH_FAILED[@]}" -gt 0 ] || [ "${#NOT_READY[@]}" -gt 0 ]; then
    echo
    echo "================ SOC Lab deploy INCOMPLETE ================"
    if [ "${#LAUNCH_FAILED[@]}" -gt 0 ]; then
        echo "Failed to launch:     ${LAUNCH_FAILED[*]}"
    fi
    if [ "${#NOT_READY[@]}" -gt 0 ]; then
        echo "Provisioning failed:  ${NOT_READY[*]}  (see /var/log/provision.log on each)"
    fi
    echo "Fix the cause, remove broken VMs with 'multipass delete --purge <name>',"
    echo "and re-run this script: VMs that already exist are skipped."
    echo "The Ansible inventory was not generated."
    exit 1
fi

if is_enabled velociraptor; then
    echo "== Stage 5: Velociraptor endpoint agents =="
    VELO_ENDPOINTS=()
    for name in dc01 fs01; do
        if is_enabled "${name}"; then VELO_ENDPOINTS+=("${name}"); fi
    done
    VELO_ENDPOINTS+=("${CLIENT_NAMES[@]}")
    install_velociraptor_clients "${VELO_ENDPOINTS[@]}"
fi

if is_enabled gitea && is_enabled prod; then
    echo "== Stage 6: CI/CD pipeline (gitea -> prod) =="
    wire_cicd
fi

if is_enabled dc01; then
    echo "== Stage 7: test data (staff, files, repos, simulated users) =="
    seed_lab_data
fi

if is_enabled ansible-ctrl; then
    echo "== Stage 8: generating Ansible inventory =="
    INV="${RENDERED_DIR}/inventory.ini"
    {
        echo "[domain_controllers]"
        if is_enabled dc01; then echo "dc01 ansible_host=${DC_IP}"; fi
        echo
        echo "[file_servers]"
        if is_enabled fs01; then echo "fs01 ansible_host=$(get_ip fs01)"; fi
        echo
        echo "[clients]"
        for name in "${CLIENT_NAMES[@]}"; do
            echo "${name} ansible_host=$(get_ip "${name}")"
        done
        echo
        echo "[siem]"
        if is_enabled siem; then echo "siem ansible_host=${SIEM_IP}"; fi
        echo
        echo "[sensors]"
        if is_enabled sensor; then echo "sensor ansible_host=$(get_ip sensor)"; fi
        echo
        echo "[redteam]"
        if is_enabled redteam; then echo "redteam ansible_host=$(get_ip redteam)"; fi
        echo
        echo "[vuln_targets]"
        if is_enabled vulntarget; then echo "vulntarget ansible_host=$(get_ip vulntarget)"; fi
        echo
        echo "[tools]"
        if is_enabled lam; then echo "lam ansible_host=$(get_ip lam)"; fi
        if is_enabled caldera; then echo "caldera ansible_host=$(get_ip caldera)"; fi
        if is_enabled velociraptor; then echo "velociraptor ansible_host=$(get_ip velociraptor)"; fi
        if is_enabled mgmt; then echo "mgmt ansible_host=$(get_ip mgmt)"; fi
        if is_enabled honeypot; then echo "honeypot ansible_host=$(get_ip honeypot)"; fi
        echo
        echo "[cicd]"
        if is_enabled gitea; then echo "gitea ansible_host=$(get_ip gitea)"; fi
        if is_enabled prod; then echo "prod ansible_host=$(get_ip prod)"; fi
        echo
        echo "[domain_joined:children]"
        echo "file_servers"
        echo "clients"
        echo
        echo "[all:vars]"
        echo "ansible_user=ubuntu"
        echo "ansible_python_interpreter=/usr/bin/python3"
    } > "${INV}"

    multipass transfer "${INV}" ansible-ctrl:/home/ubuntu/soc-lab-ansible/inventory.ini
    multipass exec ansible-ctrl -- sudo chown ubuntu:ubuntu /home/ubuntu/soc-lab-ansible/inventory.ini
fi

echo
echo "================ SOC Lab Deployed ================"
echo "Ansible inventory: multipass shell ansible-ctrl, then: cd soc-lab-ansible && ansible all -m ping"
echo "Re-print this overview any time with: /opt/homebrew/bin/bash ./lab-info.sh"
"${BASH}" "${SCRIPT_DIR}/lab-info.sh"   # same bash 4+ as this script (not macOS's 3.2)
