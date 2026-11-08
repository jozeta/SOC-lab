# shellcheck shell=bash
# shellcheck disable=SC2034  # DRY_RUN is read by deploy-soc-lab.sh
#
# lib/menu.sh -- the lab menu: flags, numbered choices, the summary and the confirmation.
# Needs lib/labs.sh sourced first (bash 4+). Plain printf is fine here: it runs before the live
# progress panel exists. Sourcing only defines variables and functions.

LAB="${LAB:-}"
SIZE="${SIZE:-}"
ASSUME_YES=0
LIST_ONLY=0
DRY_RUN=0
LAB_SIZES=(small medium large)

# Extra RAM (GB) to leave for macOS and the Multipass host on top of what the lab allocates.
LAB_RAM_HEADROOM_GB=8

lab_usage() {
    cat <<'USAGE'
Usage: deploy-soc-lab.sh [--lab LAB] [--size SIZE] [--yes] [--list] [--dry-run] [--help]

  --lab LAB     soc | forensics | pentest | devsecops
  --size SIZE   small | medium | large
  --yes         skip the confirmation
  --list        print every lab, its purpose and the totals for each size, then exit
  --dry-run     show what would be deployed without launching anything
  --help        this text

Without --lab and --size the script asks in a numbered menu (needs a terminal).
USAGE
}

# parse_lab_args "$@": sets LAB, SIZE, ASSUME_YES, LIST_ONLY, DRY_RUN. Bad input exits 2.
parse_lab_args() {
    local value
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --lab|--size)
                if [[ $# -lt 2 || -z "$2" ]]; then
                    printf 'error: %s needs a value\n' "$1" >&2
                    exit 2
                fi
                value="$2"
                if [[ "$1" == "--lab" ]]; then
                    if ! lab_valid "${value}"; then
                        printf 'error: unknown lab "%s"\nvalid labs: %s\n' "${value}" "${LAB_NAMES[*]}" >&2
                        exit 2
                    fi
                    LAB="${value}"
                else
                    if ! size_valid "${value}"; then
                        printf 'error: unknown size "%s"\nvalid sizes: %s\n' "${value}" "${LAB_SIZES[*]}" >&2
                        exit 2
                    fi
                    SIZE="${value}"
                fi
                shift 2
                ;;
            --yes|-y) ASSUME_YES=1; shift ;;
            --list) LIST_ONLY=1; shift ;;
            --dry-run) DRY_RUN=1; shift ;;
            --help|-h) lab_usage; exit 0 ;;
            *)
                printf 'error: unknown option "%s"\n\n' "$1" >&2
                lab_usage >&2
                exit 2
                ;;
        esac
    done
    return 0
}

lab_valid() {
    local n
    for n in "${LAB_NAMES[@]}"; do [[ "$1" == "${n}" ]] && return 0; done
    return 1
}

size_valid() {
    local n
    for n in "${LAB_SIZES[@]}"; do [[ "$1" == "${n}" ]] && return 0; done
    return 1
}

# lab_vm_count <lab> <size>: servers plus clients.
lab_vm_count() {
    local servers
    servers="$(lab_vms "$1" "$2" | wc -l)"
    echo $((servers + LAB_CLIENTS[$2]))
}

# lab_ram_gb <lab> <size>
lab_ram_gb() {
    local c r d
    read -r c r d <<< "$(lab_totals "$1" "$2")"
    echo "${r}"
}

# host_ram_gb: the Mac's RAM in GB (LAB_HOST_RAM_GB overrides); empty when unknown.
host_ram_gb() {
    local bytes
    if [[ -n "${LAB_HOST_RAM_GB:-}" ]]; then
        echo "${LAB_HOST_RAM_GB}"
        return 0
    fi
    bytes="$(sysctl -n hw.memsize 2>/dev/null || true)"
    if [[ "${bytes}" =~ ^[0-9]+$ ]]; then echo $((bytes / 1073741824)); fi
    return 0
}

print_lab_list() {
    local lab size c r d
    printf 'Labs (one at a time; sizes build on each other):\n'
    for lab in "${LAB_NAMES[@]}"; do
        printf '\n%s  [%s]\n' "$(lab_title "${lab}")" "${lab}"
        lab_purpose "${lab}" | sed 's/^/  /'
        for size in "${LAB_SIZES[@]}"; do
            read -r c r d <<< "$(lab_totals "${lab}" "${size}")"
            printf '  %-7s %2d VMs (%d cl.), %2d vCPU, %2d GB RAM, %3d GB disk, ~%d min\n' \
                "${size}" "$(lab_vm_count "${lab}" "${size}")" "${LAB_CLIENTS[${size}]}" \
                "${c}" "${r}" "${d}" "$(lab_estimate_minutes "${lab}" "${size}")"
        done
    done
}

# print_lab_summary <lab> <size>
print_lab_summary() {
    local lab="$1" size="$2" name n i c r d host need
    local kind prefix
    kind="$(lab_client_kind "${lab}")"
    if [[ "${kind}" == developer ]]; then prefix=devclient; else prefix=client; fi
    read -r c r d <<< "$(lab_totals "${lab}" "${size}")"
    n="${LAB_CLIENTS[${size}]}"
    printf '\n%s, %s\n' "$(lab_title "${lab}")" "${size}"
    printf '%s\n\n' "------------------------------------------------------------"
    for name in $(lab_vms "${lab}" "${size}"); do
        printf '  %-13s %s\n' "${name}" "$(vm_role "${name}")"
    done
    for ((i = 1; i <= n; i++)); do
        name="$(printf '%s%02d' "${prefix}" "${i}")"
        printf '  %-13s %s\n' "${name}" "$(vm_role "${name}")"
    done
    printf '\nClients: %d (%s)\n' "${n}" "${kind} workstations"
    printf 'Total:   %d VMs, %d vCPU, %d GB RAM, %d GB disk\n' "$(lab_vm_count "${lab}" "${size}")" "${c}" "${r}" "${d}"
    printf 'Time:    ≈%d min\n' "$(lab_estimate_minutes "${lab}" "${size}")"
    host="$(host_ram_gb)"
    need=$((r + LAB_RAM_HEADROOM_GB))
    if [[ -n "${host}" ]] && ((need > host)); then
        printf 'WARNING: this lab needs %d GB RAM (+%d GB headroom for macOS) but this Mac has %d GB.\n' \
            "${r}" "${LAB_RAM_HEADROOM_GB}" "${host}"
    fi
    return 0
}

# ask <prompt>: reads one answer into REPLY (trimmed, lower case). End of input quits (exit 0).
ask() {
    local ans
    printf '%s' "$1"
    if ! IFS= read -r ans; then
        printf '\nNo more input; nothing deployed.\n'
        exit 0
    fi
    ans="${ans#"${ans%%[![:space:]]*}"}"
    ans="${ans%"${ans##*[![:space:]]}"}"
    REPLY="${ans,,}"
    if [[ "${REPLY}" == q || "${REPLY}" == quit ]]; then
        printf 'Nothing deployed.\n'
        exit 0
    fi
    return 0
}

# menu_pick_lab: sets LAB from the numbered list.
menu_pick_lab() {
    local i lab c r_small r_large d
    while true; do
        printf '\nChoose a lab:\n'
        i=0
        for lab in "${LAB_NAMES[@]}"; do
            i=$((i + 1))
            r_small="$(lab_ram_gb "${lab}" small)"
            r_large="$(lab_ram_gb "${lab}" large)"
            printf '\n  %d) %s   (%d-%d GB RAM)\n' "${i}" "$(lab_title "${lab}")" "${r_small}" "${r_large}"
            lab_purpose "${lab}" | sed 's/^/       /'
        done
        printf '\n  q) quit\n'
        ask 'Lab [1-4]: '
        if [[ "${REPLY}" =~ ^[0-9]+$ ]] && ((REPLY >= 1 && REPLY <= ${#LAB_NAMES[@]})); then
            LAB="${LAB_NAMES[$((REPLY - 1))]}"
            return 0
        fi
        if lab_valid "${REPLY}"; then LAB="${REPLY}"; return 0; fi
        printf 'Please type a number from 1 to %d, or q.\n' "${#LAB_NAMES[@]}"
    done
}

# menu_pick_size: sets SIZE; returns 1 when the user typed b (back).
menu_pick_size() {
    local i size c r d
    while true; do
        printf '\nChoose a size for %s:\n\n' "$(lab_title "${LAB}")"
        i=0
        for size in "${LAB_SIZES[@]}"; do
            i=$((i + 1))
            read -r c r d <<< "$(lab_totals "${LAB}" "${size}")"
            printf '  %d) %-6s %2d VMs (%d cl.), %2d GB RAM, ~%d min\n' "${i}" "${size}" \
                "$(lab_vm_count "${LAB}" "${size}")" "${LAB_CLIENTS[${size}]}" "${r}" \
                "$(lab_estimate_minutes "${LAB}" "${size}")"
        done
        printf '\n  b) back   q) quit\n'
        ask 'Size [1-3]: '
        if [[ "${REPLY}" == b || "${REPLY}" == back ]]; then return 1; fi
        if [[ "${REPLY}" =~ ^[0-9]+$ ]] && ((REPLY >= 1 && REPLY <= ${#LAB_SIZES[@]})); then
            SIZE="${LAB_SIZES[$((REPLY - 1))]}"
            return 0
        fi
        if size_valid "${REPLY}"; then SIZE="${REPLY}"; return 0; fi
        printf 'Please type a number from 1 to %d, b or q.\n' "${#LAB_SIZES[@]}"
    done
}

# choose_lab: fills LAB / SIZE through the menu when they are not set. --list prints and exits.
choose_lab() {
    local lab_from_flag=0
    if [[ "${LIST_ONLY}" == 1 ]]; then
        print_lab_list
        exit 0
    fi
    [[ -n "${LAB}" && -n "${SIZE}" ]] && return 0
    if [[ ! -t 0 && "${LAB_MENU_TTY:-}" != 1 ]]; then
        printf 'error: no terminal to ask on. Pass both --lab (%s) and --size (%s).\n' \
            "${LAB_NAMES[*]}" "${LAB_SIZES[*]}" >&2
        exit 2
    fi
    [[ -n "${LAB}" ]] && lab_from_flag=1
    while [[ -z "${LAB}" || -z "${SIZE}" ]]; do
        [[ -z "${LAB}" ]] && menu_pick_lab
        if [[ -z "${SIZE}" ]] && ! menu_pick_size; then
            # b: back to the lab list (or, when --lab was given, ask the size again)
            if ((lab_from_flag)); then :; else LAB=""; fi
        fi
    done
    return 0
}

# confirm_lab: 0 on y/yes. Enter means No; b goes back to the size choice; q exits 0.
confirm_lab() {
    [[ "${ASSUME_YES}" == 1 ]] && return 0
    while true; do
        ask 'Deploy this lab? [y/N, b = back, q = quit]: '
        case "${REPLY}" in
            y|yes) return 0 ;;
            ""|n|no) printf 'Not deploying.\n'; return 1 ;;
            b|back)
                SIZE=""
                choose_lab
                print_lab_summary "${LAB}" "${SIZE}"
                ;;
            *) printf 'Please type y, n, b or q.\n' ;;
        esac
    done
}
