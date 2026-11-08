#!/usr/bin/env bash
#
# lab-doctor.sh -- health check for a running lab. Changes nothing.
#
#   VMs running; disk space and failed systemd units on each; DNS drop-ins
#   still pointing at dc01's current IP (it can change after a host reboot);
#   Wazuh agents connected; Velociraptor clients seen recently; the
#   simulated-activity services running.
#
# (sssd's socket units always show as failed when sssd runs as one daemon, as
# on the clients: they are ignored.)
#
# Prints OK / WARN / FAIL per check, with a hint for each problem, and exits
# 1 if anything FAILed. Run with the same bash 4+ as the deploy script:
#   /opt/homebrew/bin/bash ./lab-doctor.sh
#
set -euo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "ERROR: needs bash 4+:  /opt/homebrew/bin/bash ./lab-doctor.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/labs.sh
source "${SCRIPT_DIR}/lib/labs.sh"

DISK_WARN=85                 # % used on / that counts as a warning
VELO_STALE=300               # seconds since a Velociraptor client was last seen

# VMs that run a Wazuh agent / a Velociraptor client / activity services.
WAZUH_AGENTS="dc01 fs01 client sensor vulntarget gitea prod honeypot mgmt lam caldera mail iris"
VELO_CLIENTS="dc01 fs01 client"
declare -A LOOPS=(
    [client]="lab-activity"
    [ansible-ctrl]="lab-ansible"
    [mgmt]="lab-semaphore"
    [velociraptor]="lab-velociraptor"
    [fs01]="lab-backup.timer"
    [mail]="lab-mailflow"
)

declare -A IP STATE
while IFS=, read -r name state ipv4 _rest; do
    [ "${name}" = "Name" ] && continue
    IP["${name}"]="${ipv4}"
    STATE["${name}"]="${state}"
done < <(multipass list --format csv)
# Only the lab's own VMs that exist right now (not other Multipass instances on this Mac).
VMS=$(comm -12 <(printf '%s\n' "${!IP[@]}" | LC_ALL=C sort) <(all_lab_vms | LC_ALL=C sort))

FAILS=0 WARNS=0
ok()   { printf '  \e[32mOK  \e[0m  %-14s %s\n' "$1" "$2"; }
warn() { printf '  \e[33mWARN\e[0m  %-14s %s\n' "$1" "$2"; WARNS=$((WARNS + 1)); }
fail() { printf '  \e[31mFAIL\e[0m  %-14s %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
if [ ! -t 1 ]; then          # no colours in a log file
    ok()   { printf '  OK    %-14s %s\n' "$1" "$2"; }
    warn() { printf '  WARN  %-14s %s\n' "$1" "$2"; WARNS=$((WARNS + 1)); }
    fail() { printf '  FAIL  %-14s %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
fi
kind() { [[ "$1" == client[0-9]* ]] && echo client || echo "$1"; }   # client01 -> client
in_list() { [[ " $2 " == *" $(kind "$1") "* ]]; }
vm_exec() { multipass exec "$1" -- sh -c "$2" 2>&1 || true; }           # never in parallel

echo
echo "VMs and their health"
for vm in ${VMS}; do
    if [ "${STATE[${vm}]}" != "Running" ]; then
        fail "${vm}" "state ${STATE[${vm}]} -- start it: multipass start ${vm}"
        continue
    fi
    loops="${LOOPS[$(kind "${vm}")]:-}"
    out=$(vm_exec "${vm}" "
        echo disk \$(df --output=pcent / | tail -n 1 | tr -dc 0-9)
        echo failed \$(systemctl --failed --no-legend --plain | awk '{print \$1}' | grep -v '^sssd-.*\.socket\$' | tr '\n' ' ')
        [ -f /etc/systemd/resolved.conf.d/lab-dns.conf ] && echo dns \$(sed -n 's/^DNS=//p' /etc/systemd/resolved.conf.d/lab-dns.conf)
        for u in ${loops}; do echo loop \$u \$(systemctl is-active \$u); done")
    disk=$(sed -n 's/^disk //p' <<< "${out}")
    failed=$(sed -n 's/^failed //p' <<< "${out}" | xargs)
    dns=$(sed -n 's/^dns //p' <<< "${out}" | xargs)
    msg="disk ${disk:-?}%"
    problem=""
    if [ -n "${disk}" ] && [ "${disk}" -ge "${DISK_WARN}" ]; then problem="disk ${disk}% full"; fi
    if [ -n "${failed}" ]; then problem="${problem:+${problem}; }failed units: ${failed}"; fi
    if [ -n "${dns}" ] && [ -n "${IP[dc01]:-}" ] && [ "${dns}" != "${IP[dc01]}" ]; then
        fail "${vm}" "DNS points at ${dns}, but dc01 is now ${IP[dc01]} -- edit /etc/systemd/resolved.conf.d/lab-dns.conf, then: sudo systemctl restart systemd-resolved"
    fi
    while read -r _ unit st; do
        [ -n "${unit}" ] || continue
        if [ "${st}" != "active" ]; then
            fail "${vm}" "${unit} is ${st} -- sudo systemctl restart ${unit}; journal: journalctl -u ${unit}"
        fi
    done < <(grep '^loop ' <<< "${out}" || true)
    if [ -n "${problem}" ]; then warn "${vm}" "${msg}; ${problem}"; else ok "${vm}" "${msg}${loops:+, ${loops} running}"; fi
done

if [ -n "${IP[siem]:-}" ] && [ "${STATE[siem]}" = "Running" ]; then
    echo
    echo "Wazuh agents (from siem)"
    agents=$(vm_exec siem "sudo /var/ossec/bin/agent_control -l")
    for vm in ${VMS}; do
        in_list "${vm}" "${WAZUH_AGENTS}" || continue
        line=$(grep -E "Name: ${vm}," <<< "${agents}" | tail -n 1 || true)
        case "${line}" in
            *Active*)       ok "${vm}" "agent connected" ;;
            "")             fail "${vm}" "no agent registered -- check: multipass exec ${vm} -- sudo systemctl status wazuh-agent" ;;
            *)              fail "${vm}" "agent $(sed 's/.*, //' <<< "${line}") -- multipass exec ${vm} -- sudo systemctl restart wazuh-agent" ;;
        esac
    done
fi

if [ -n "${IP[velociraptor]:-}" ] && [ "${STATE[velociraptor]}" = "Running" ]; then
    echo
    echo "Velociraptor clients (last seen)"
    seen=$(vm_exec velociraptor "sudo velociraptor --api_config /etc/velociraptor/api.config.yaml query --format jsonl 'SELECT os_info.hostname AS host, int(int=now() - last_seen_at / 1000000) AS ago FROM clients()' 2>/dev/null")
    for vm in ${VMS}; do
        in_list "${vm}" "${VELO_CLIENTS}" || continue
        ago=$(grep "\"host\":\"${vm}\"" <<< "${seen}" | sed 's/.*"ago":\(-\{0,1\}[0-9]*\).*/\1/' | sort -n | head -n 1)
        if [[ "${ago}" == -* ]]; then ago=0; fi      # seen "in the future": clock rounding
        if [ -z "${ago}" ]; then
            fail "${vm}" "not enrolled -- the deploy installs the agent in stage 5"
        elif [ "${ago}" -gt "${VELO_STALE}" ]; then
            fail "${vm}" "last seen ${ago}s ago -- multipass exec ${vm} -- sudo systemctl restart velociraptor_client"
        else
            ok "${vm}" "seen ${ago}s ago"
        fi
    done
fi

echo
if [ "${FAILS}" -gt 0 ]; then
    echo "${FAILS} problem(s), ${WARNS} warning(s)."
    exit 1
fi
echo "All good (${WARNS} warning(s))."
