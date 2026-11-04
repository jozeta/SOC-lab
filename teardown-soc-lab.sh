#!/usr/bin/env bash
#
# teardown-soc-lab.sh -- deletes and purges every VM this lab creates
# (frees the disk space). Each VM is purged individually with
# `multipass delete --purge <name>`, so other deleted-but-recoverable
# Multipass instances on this Mac that aren't part of the lab are left alone.
#
set -euo pipefail

VMS=(ansible-ctrl dc01 fs01 siem sensor redteam vulntarget lam caldera velociraptor mgmt gitea prod honeypot
     mail iris)

# Clients are numbered client01, client02, ... and the count is whatever
# NUM_CLIENTS was set to at deploy time -- check up to 20 rather than
# hardcoding a count, and just skip whichever don't exist.
for i in $(seq -w 1 20); do
    VMS+=("client${i}")
done

for name in "${VMS[@]}"; do
    if multipass info "${name}" &>/dev/null; then
        echo "Deleting and purging ${name}..."
        multipass delete --purge "${name}"
    fi
done

# The staff roster belongs to the lab that was just deleted: remove it (and
# the seed config and IRIS API key, which are secrets) so the next deploy
# hires new people and gets a new key.
RENDERED_DIR="$(cd "$(dirname "$0")" && pwd)/.rendered-cloud-init"
rm -f "${RENDERED_DIR}/lab-roster.json" "${RENDERED_DIR}/labdata-config.json" \
      "${RENDERED_DIR}/iris-api-key" "${RENDERED_DIR}/iris-cert.pem"

echo "Done. Rendered cloud-init files are left in .rendered-cloud-init/ for inspection"
echo "(gitignored -- they contain the lab passwords; delete the folder if you don't need it)."
