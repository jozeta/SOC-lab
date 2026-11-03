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
    echo "ERROR: multipass not found. Install it first:"
    echo "    brew install --cask multipass"
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
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

render_template() {
    local template="$1" output="$2"
    shift 2
    cp "${template}" "${output}"
    for pair in "$@"; do
        local key="${pair%%=*}"
        local value="${pair#*=}"
        sed -i.bak "s|__${key}__|${value}|g" "${output}"
        rm -f "${output}.bak"
    done
}

launch_vm() {
    local name="$1" cpus="$2" mem="$3" disk="$4" cloud_init="$5"
    if multipass info "${name}" &>/dev/null; then
        echo "  [skip]   ${name} already exists"
        return
    fi
    echo "  [launch] ${name} (${cpus} vCPU / ${mem} RAM / ${disk} disk)"
    multipass launch "${IMAGE}" --name "${name}" --cpus "${cpus}" \
        --memory "${mem}" --disk "${disk}" --cloud-init "${cloud_init}"
}

wait_ready() {
    local name="$1"
    echo "  [wait]   ${name} finishing cloud-init (siem/caldera can take a long while)..."
    local attempt status
    for attempt in $(seq 1 180); do
        status=$(multipass exec "${name}" -- cloud-init status 2>/dev/null | awk '{print $2}') || true
        if [ "${status}" = "done" ]; then
            return 0
        elif [ "${status}" = "error" ]; then
            echo "  [ERROR]  ${name} cloud-init reported an error."
            echo "           Check: multipass exec ${name} -- cat /var/log/provision.log"
            return 1
        fi
        sleep 10
    done
    echo "  [WARN]   ${name} still not 'done' after 30 minutes -- check it manually:"
    echo "           multipass exec ${name} -- cloud-init status"
    echo "           multipass exec ${name} -- cat /var/log/provision.log"
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
    ANSIBLE_PUBKEY=$(multipass exec ansible-ctrl -- cat /home/ubuntu/.ssh/id_ed25519.pub)
else
    echo "  ansible-ctrl disabled -- other VMs will have no injected management key."
fi

echo "== Stage 2: Active Directory domain controller =="
DC_IP=""
if is_enabled dc01; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[dc01]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/dc01.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DOMAIN_NETBIOS=${DOMAIN_NETBIOS}" \
        "ADMIN_PASS=${ADMIN_PASS}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm dc01 "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/dc01.yaml"
    wait_ready dc01
    DC_IP=$(get_ip dc01)
    echo "  dc01 IP: ${DC_IP}"
else
    echo "  dc01 disabled -- domain-joined VMs below will fail to join, and lam"
    echo "  will have nothing to point at."
fi

echo "== Stage 3: Wazuh SIEM =="
SIEM_IP=""
if is_enabled siem; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[siem]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/siem.yaml" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm siem "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/siem.yaml"
    wait_ready siem
    SIEM_IP=$(get_ip siem)
    echo "  siem IP: ${SIEM_IP}"
else
    echo "  siem disabled -- agents on other VMs will have no manager to report to."
fi

echo "== Stage 4: clients, file server, sensor, red team, vuln target, and add-on tools =="

if is_enabled fs01; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[fs01]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/fs01.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DOMAIN_NETBIOS=${DOMAIN_NETBIOS}" \
        "DC_IP=${DC_IP}" "ADMIN_PASS=${ADMIN_PASS}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm fs01 "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/fs01.yaml" &
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
        launch_vm "${name}" "${c_cpus}" "${c_mem}" "${c_disk}" "${RENDERED_DIR}/${name}.yaml" &
    done
fi

if is_enabled sensor; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[sensor]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/sensor.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" "WAZUH_MANAGER_IP=${SIEM_IP}" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm sensor "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/sensor.yaml" &
fi

if is_enabled redteam; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[redteam]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/redteam.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm redteam "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/redteam.yaml" &
fi

if is_enabled vulntarget; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[vulntarget]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/vulntarget.yaml" \
        "WAZUH_MANAGER_IP=${SIEM_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm vulntarget "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/vulntarget.yaml" &
fi

if is_enabled lam; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[lam]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/lam.yaml" \
        "DOMAIN_REALM=${DOMAIN_REALM}" "DC_IP=${DC_IP}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm lam "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/lam.yaml" &
fi

if is_enabled caldera; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[caldera]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/caldera.yaml" \
        "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm caldera "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/caldera.yaml" &
fi

if is_enabled velociraptor; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[velociraptor]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/velociraptor.yaml" \
        "ADMIN_PASS=${ADMIN_PASS}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm velociraptor "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/velociraptor.yaml" &
fi

if is_enabled mgmt; then
    read -r cpus mem disk tmpl <<< "${VM_SPEC[mgmt]}"
    render_template "${TEMPLATE_DIR}/${tmpl}" "${RENDERED_DIR}/mgmt.yaml" \
        "ADMIN_PASS=${ADMIN_PASS}" "ANSIBLE_PUBKEY=${ANSIBLE_PUBKEY}"
    launch_vm mgmt "${cpus}" "${mem}" "${disk}" "${RENDERED_DIR}/mgmt.yaml" &
fi

wait

for name in fs01 "${CLIENT_NAMES[@]}" sensor redteam vulntarget lam caldera velociraptor mgmt; do
    if is_enabled "${name}" || [[ " ${CLIENT_NAMES[*]} " == *" ${name} "* ]]; then
        wait_ready "${name}"
    fi
done

if is_enabled ansible-ctrl; then
    echo "== Stage 5: generating Ansible inventory =="
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
multipass list
echo
echo "Domain:            ${DOMAIN_REALM} (NetBIOS: ${DOMAIN_NETBIOS})"
echo "Administrator pass: ${ADMIN_PASS}   <-- change this in the script for real use"
is_enabled siem && echo "Wazuh dashboard:    https://${SIEM_IP}/  (creds: multipass exec siem -- sudo cat /root/wazuh-credentials.txt)"
is_enabled ansible-ctrl && echo "Ansible inventory:  multipass shell ansible-ctrl, then: cd soc-lab-ansible && ansible all -m ping"
is_enabled vulntarget && echo "DVWA:               http://$(get_ip vulntarget)/  (login: admin / password, then set difficulty)"
is_enabled vulntarget && echo "Juice Shop:         http://$(get_ip vulntarget):3000/  (no login needed to start)"
is_enabled lam && echo "LAM:                http://$(get_ip lam)/lam  (complete the server-profile wizard first -- see README)"
is_enabled caldera && echo "Caldera:            http://$(get_ip caldera):8888  (login: red / admin -- Caldera's --insecure defaults)"
is_enabled velociraptor && echo "Velociraptor:       https://$(get_ip velociraptor):8889  (login: admin / the lab Administrator password above)"
is_enabled mgmt && echo "Uptime Kuma:        http://$(get_ip mgmt):3001  (set up your own admin account on first visit)"
is_enabled mgmt && echo "Semaphore UI:       http://$(get_ip mgmt):3000  (login: admin / the lab Administrator password above)"
echo "===================================================="
echo "See README.md for lab accounts, attack scenarios, and teardown-soc-lab.sh."
