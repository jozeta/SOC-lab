# Changelog

All notable changes to SOC-lab are listed here, newest first.

The project has no tagged releases yet, so changes are grouped by date. Entries before November 2026 were reconstructed from the commit history.

## Unreleased

- Gitignore `.rendered-cloud-init/`, which holds rendered templates containing the lab passwords
- Teardown purges only the lab's own VMs (`multipass delete --purge <name>`) instead of a global
  `multipass purge`, and no longer lists the non-existent `misp` VM
- README links the security policy
- Velociraptor: pin `v0.77.3` and handle the gzipped release binaries (the old "latest" asset
  lookup no longer matched anything, so fresh deploys failed); bind the GUI to all interfaces so
  it is reachable at `https://<ip>:8889` (it was bound to `127.0.0.1` only); create the admin
  account with the verified `user add --role` syntax; cloud-init waits until the GUI answers
- Caldera: install a pinned, checksum-verified Node 22 from nodejs.org (Ubuntu's Node 18 is too
  old for the Vite UI build, so the service crash-looped and port 8888 never came up); build the
  UI during provisioning and wait for port 8888; document the real login (`red` / `admin`)
- dc01: set the domain's maximum password age to "never" -- Samba's 42-day default expired
  Administrator and every seeded account, which made later domain joins fail
- fs01: wait for winbind to resolve `LAB\Finance` before setting the finance share's group, and
  fail cloud-init with diagnostics instead of silently skipping it

## 2026-11-02

- Add security policy

## 2026-11-01

- Ignore macOS, Claude Code and secret files; untrack .DS_Store
- Add CLAUDE.md with project guidance for Claude Code

## 2026-08-05

- added ignore file
