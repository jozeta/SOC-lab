# shellcheck shell=bash
#
# lib/gateway.sh -- the forensics lab's network tap: which VMs route through sensor, and the netplan
# drop-in that sends them there. Sourced by deploy-soc-lab.sh after lib/labs.sh; needs bash 4+.
# Sourcing only defines functions. Everything here is pure (no multipass calls), so tests can call it.
#
# Multipass NAT gives each VM only its own traffic. In the forensics lab every member's default route
# and its routes to the other members go through sensor, which forwards (and NATs to the internet) and
# copies every forwarded packet to arkime. The deploy applies this after every VM is ready
# (route_through_sensor): members such as dc01 launch before sensor exists.

# shellcheck disable=SC2034  # used by deploy-soc-lab.sh
GATEWAY_DROPIN=/etc/netplan/60-lab-gateway.yaml

# gateway_members <lab> <size>: every VM of the lab that routes through sensor: all but sensor and arkime.
gateway_members() {
    local name
    for name in $(lab_deploy_plan "$1" "$2"); do
        [[ "${name}" == sensor || "${name}" == arkime ]] || echo "${name}"
    done
    return 0
}

gateway_is_ipv4() {
    [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    local i
    for i in 1 2 3 4; do ((10#${BASH_REMATCH[i]} <= 255)) || return 1; done
}

# gateway_netplan <netplan id> <sensor ip> [peer ip]...: the drop-in for one member. Netplan merges it
# into the cloud-init config by the ethernets id (Multipass names it "default"). The routes carry a
# lower metric than DHCP's default route (100), so they win and survive reboots and DHCP renewals.
# Prints nothing and returns 1 on an invalid id or address.
gateway_netplan() {
    local id="${1:-}" via="${2:-}" peer
    shift 2 || return 1
    [[ "${id}" =~ ^[A-Za-z0-9_.-]+$ ]] || return 1
    gateway_is_ipv4 "${via}" || return 1
    for peer in "$@"; do gateway_is_ipv4 "${peer}" || return 1; done
    printf '%s\n' "# Written by deploy-soc-lab.sh (forensics lab): route through sensor, the lab's network tap." \
        "network:" "  version: 2" "  ethernets:" "    ${id}:" "      routes:" \
        "        - to: default" "          via: ${via}" "          metric: 10"
    for peer in "$@"; do
        printf '%s\n' "        - to: ${peer}/32" "          via: ${via}" "          metric: 10"
    done
}
