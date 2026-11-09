#!/usr/bin/env bash
#
# teardown-soc-lab.sh -- deletes and purges every VM this lab creates
# (frees the disk space). Each VM is purged individually with
# `multipass delete --purge <name>`, so other deleted-but-recoverable
# Multipass instances on this Mac that aren't part of the lab are left alone.
#
set -euo pipefail

# The lab library needs bash 4+ (macOS ships 3.2): use Homebrew's if this is not it.
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [ -x "${b}" ] && exec "${b}" "$0" "$@"
    done
    echo "ERROR: needs bash 4+:  /opt/homebrew/bin/bash ./teardown-soc-lab.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/labs.sh
source "${SCRIPT_DIR}/lib/labs.sh"

usage() {
    cat <<'USAGE'
Usage: ./teardown-soc-lab.sh [--yes]

Deletes and purges every VM of the running lab (any lab, any size), lists them first and asks
[y/N] (default No). Other Multipass instances on this Mac are left alone.

  -y, --yes    skip the question and delete
  -h, --help   show this help and exit
USAGE
}

ASSUME_YES=0
for arg in "$@"; do
    case "${arg}" in
        -h|--help) usage; exit 0 ;;
        -y|--yes) ASSUME_YES=1 ;;
        *) echo "ERROR: unknown argument: ${arg}" >&2; usage >&2; exit 2 ;;
    esac
done

# Every VM any lab can create (servers, client01..20, devclient01..20) that Multipass lists:
# whichever lab and size was deployed, and nothing else. A failing `multipass list` must not look
# like "no VMs": stop before changing anything.
if ! VM_LIST="$(running_lab_vms)"; then
    echo "ERROR: can't reach Multipass (\`multipass list\` failed); nothing changed." >&2
    exit 1
fi

if [ -n "${VM_LIST}" ]; then
    echo "These VMs will be deleted and purged:"
    while IFS= read -r name; do printf '  %s\n' "${name}"; done <<< "${VM_LIST}"
    if [ "${ASSUME_YES}" != 1 ]; then
        if [[ ! -t 0 && "${LAB_MENU_TTY:-}" != 1 ]]; then
            echo "ERROR: no terminal to ask on; nothing deleted. Re-run with --yes to delete without asking." >&2
            exit 2
        fi
        printf 'Delete them? [y/N]: '
        reply=""
        read -r reply || true
        case "${reply}" in
            y|Y|yes|YES) ;;
            *) echo; echo "Not deleting."; exit 0 ;;
        esac
    fi
    while IFS= read -r name; do
        echo "Deleting and purging ${name}..."
        multipass delete --purge "${name}"
    done <<< "${VM_LIST}"
else
    echo "No lab VMs found."
fi

rm -f "${SCRIPT_DIR}/.lab-state"

# The staff roster belongs to the lab that was just deleted: remove it (and
# the seed config and IRIS API key, which are secrets) so the next deploy
# hires new people and gets a new key.
RENDERED_DIR="${SCRIPT_DIR}/.rendered-cloud-init"
rm -f "${RENDERED_DIR}/lab-roster.json" "${RENDERED_DIR}/labdata-config.json" \
      "${RENDERED_DIR}/iris-api-key" "${RENDERED_DIR}/iris-cert.pem"

echo "Done. Rendered cloud-init files are left in .rendered-cloud-init/ for inspection"
echo "(gitignored -- they contain the lab passwords; delete the folder if you don't need it)."
