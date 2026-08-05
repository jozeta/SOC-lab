#!/usr/bin/env bash
#
# teardown-soc-lab.sh -- deletes every VM this lab creates and purges them
# from Multipass (frees the disk space; skip --purge if you want to keep
# them recoverable for a short while instead).
#
set -euo pipefail

VMS=(ansible-ctrl dc01 fs01 siem sensor redteam vulntarget lam caldera velociraptor misp mgmt)

# Clients are numbered client01, client02, ... and the count is whatever
# NUM_CLIENTS was set to at deploy time -- check up to 20 rather than
# hardcoding a count, and just skip whichever don't exist.
for i in $(seq -w 1 20); do
    VMS+=("client${i}")
done

for name in "${VMS[@]}"; do
    if multipass info "${name}" &>/dev/null; then
        echo "Deleting ${name}..."
        multipass delete "${name}"
    fi
done

echo "Purging deleted VMs..."
multipass purge

echo "Done. Rendered cloud-init files are left in .rendered-cloud-init/ for inspection."
