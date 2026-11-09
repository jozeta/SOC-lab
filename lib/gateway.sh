# shellcheck shell=bash
#
# lib/gateway.sh -- the forensics lab's network tap: which VMs route through sensor, and the netplan
# drop-in that sends them there. Sourced by deploy-soc-lab.sh after lib/labs.sh; needs bash 4+.
# Sourcing only defines functions. gateway_* are pure; route_through_sensor (deploy stage 4b) calls
# multipass and the deploy's helpers (say, progress_task_*, get_ip, is_enabled, run_with_timeout).
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

route_through_sensor() {
    # Forensics lab: sensor becomes the gateway and network tap once every VM is ready (dc01 and
    # the other early VMs launch before sensor exists, so this can't happen at boot). Serially, one
    # `multipass exec` at a time: tell sensor where arkime is, then give each member a persistent
    # netplan drop-in (lib/gateway.sh) that routes its default route and the other members through
    # sensor, and check it still resolves through dc01 and reaches the Ubuntu mirror. Last, the
    # attack scenario on redteam gets its targets.
    local sensor_ip arkime_ip="" name other id out conf members=() peers=()
    local -A ips=()
    sensor_ip=$(get_ip sensor)
    if is_enabled arkime; then arkime_ip=$(get_ip arkime); fi
    progress_task_start "Network tap: sensor copies forwarded traffic to arkime"
    out=$(multipass exec sensor -- sudo sh -c \
        "printf 'MIRROR_IP=%s\n' '${arkime_ip}' > /etc/lab-gateway.conf && systemctl restart lab-gateway && systemctl is-active lab-gateway" 2>&1) || true
    # Exactly "active" on a line of its own ("inactive" and "activating" are failures): without a
    # working gateway, routing the members through sensor would cut them off (DNS and internet too).
    if ! grep -qx active <<< "${out}"; then
        say "  [WARN]   sensor's lab-gateway service isn't active, so no VM was routed through sensor"
        say "           (they keep their direct routes). Check: multipass exec sensor -- journalctl -u lab-gateway"
        progress_task_done
        return 0
    fi
    say "  [tap]    sensor forwards the lab's traffic and copies it to arkime (${arkime_ip:-none})"
    progress_task_done
    mapfile -t members < <(gateway_members "${LAB}" "${SIZE}")
    for name in "${members[@]}"; do ips[${name}]=$(get_ip "${name}"); done
    for name in "${members[@]}"; do
        progress_task_start "Routing ${name} through sensor"
        peers=()
        for other in "${members[@]}"; do
            if [ "${other}" != "${name}" ]; then peers+=("${ips[${other}]}"); fi
        done
        id=$(run_with_timeout 60 multipass exec "${name}" -- sudo netplan get ethernets 2>&1 \
            | awk -F: '/^[A-Za-z0-9_.-]+:/ {print $1; exit}') || true
        conf="${RENDERED_DIR}/gateway-${name}.yaml"
        if ! gateway_netplan "${id:-default}" "${sensor_ip}" "${peers[@]}" > "${conf}"; then
            say "  [WARN]   ${name}: no route through sensor (bad netplan id '${id}' or address)"
            progress_task_done
            continue
        fi
        # The drop-in travels base64-encoded in the command: run_with_timeout's background job has no stdin.
        out=$(run_with_timeout 180 multipass exec "${name}" -- sudo sh -c "
            printf '%s' '$(base64 < "${conf}" | tr -d '\n')' | base64 -d > ${GATEWAY_DROPIN}
            chmod 600 ${GATEWAY_DROPIN} && netplan apply && sleep 5
            echo route \$(ip -4 route get 1.1.1.1 | sed -n 's/.* via \\([0-9.]*\\).*/\\1/p')
            echo dns \$(dig +short +time=3 +tries=2 @${DC_IP:-127.0.0.1} dc01.${DOMAIN_REALM,,} | head -n 1)
            echo mirror \$(curl -s -m 20 -o /dev/null -w '%{http_code}' http://ports.ubuntu.com/ubuntu-ports/dists/noble/Release)" \
            2>&1) || true
        if [[ "${out}" == *"route ${sensor_ip}"* && "${out}" == *"mirror 200"* \
              && ( -z "${DC_IP}" || "${out}" =~ dns\ [0-9] ) ]]; then
            say "  [route]  ${name}: default route via sensor (${sensor_ip}); DNS via dc01 and the Ubuntu mirror work"
        else
            # Don't leave a member cut off: drop the drop-in, back to its DHCP routes.
            run_with_timeout 120 multipass exec "${name}" -- sudo sh -c \
                "rm -f ${GATEWAY_DROPIN} && netplan apply" > "${RENDERED_DIR}/gateway-${name}.undo.log" 2>&1 || true
            say "  [WARN]   ${name}: check after routing through sensor failed ($(tr '\n' ' ' <<< "${out}")); route removed, it keeps its direct routes"
        fi
        progress_task_done
    done
    progress_task_start "Attack scenario targets on redteam"
    if [ "$(lab_attack_scenario "${LAB}")" = 1 ] && is_enabled redteam; then
        conf="${RENDERED_DIR}/lab-attack-config"
        (umask 077 && {
            printf 'realm %s\nnetbios %s\nuser alice.user\nfinance_user jane.finance\npassword %s\n' \
                "${DOMAIN_REALM}" "${DOMAIN_NETBIOS}" "${SEED_PASS}"
            for name in "${members[@]}"; do
                if [ "${name}" != redteam ] && [ -n "${ips[${name}]}" ]; then
                    printf 'target %s %s\n' "${name}" "${ips[${name}]}"
                fi
            done
        } > "${conf}")
        # base64 in the command, like the drop-ins: run_with_timeout's background job has no stdin.
        if run_with_timeout 60 multipass exec redteam -- sudo sh -c \
            "install -d -m 700 /etc/lab-attack && printf '%s' '$(base64 < "${conf}" | tr -d '\n')' | base64 -d > /etc/lab-attack/config && chmod 600 /etc/lab-attack/config" \
            > "${RENDERED_DIR}/lab-attack-config.log" 2>&1; then
            say "  [attack] redteam runs the attack scenario every 30 minutes (log: /var/log/lab-attack.log)"
        else
            say "  [WARN]   redteam: attack scenario targets not written -- see ${RENDERED_DIR}/lab-attack-config.log"
        fi
        rm -f "${conf}"
    fi
    progress_task_done
}
