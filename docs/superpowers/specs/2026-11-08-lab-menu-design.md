# Lab menu: choose a lab type and size at deploy time

Status: reviewed; waiting for Johan's approval (2026-11-08)

## Why

Today `deploy-soc-lab.sh` always builds one big blue-team lab (about 36 vCPU / 50 GB RAM), and
trimming it means editing `VM_ENABLED` and `NUM_CLIENTS` by hand. Johan wants to start a deploy and
pick *what to practise* (SOC, network forensics, pentest, DevSecOps) and *how big*, with the menu
explaining each lab before anything is launched.

Success: run the script, read in the menu what each lab is for, which servers and clients it builds,
how many, how much RAM and roughly how long it takes, pick one, confirm, and get a working lab
focused on that subject.

## Goals

- An interactive numbered terminal menu when `deploy-soc-lab.sh` starts:
  1. choose a lab (with its purpose in one or two lines),
  2. choose a size (small / medium / large),
  3. a summary: every VM with its role, the count of clients, total vCPU / RAM / disk, an estimated
     deployment time, then confirm (default No) or go back.
- The same choice without the menu: `--lab soc|forensics|pentest|devsecops --size small|medium|large`,
  plus `--yes` to skip the confirmation and `--list` to print all labs, sizes and totals and exit.
- Four labs, each with its own servers and clients, focused on one area (contents below).
- Sizes: small = the lab's core servers + 1 client; medium = + the lab's optional tools + 3 clients;
  large = everything in that lab + 6 clients.
- Every lab's large size fits Johan's Mac (64 GB RAM, 18 cores): at most about 48 GB RAM allocated,
  leaving headroom for macOS. The menu checks the Mac's RAM and warns (before confirming) when the
  chosen lab needs more than the Mac can spare.
- One lab at a time. The deploy records which lab and size are running; the menu shows it, and
  deploying another lab asks you to run `teardown-soc-lab.sh` first.
- Time estimates are rough to start with (from the VM count and the known slow servers) and are
  replaced by measured times once each lab has been deployed on Johan's Mac.

## Non-goals

- Real Kali or any non-Ubuntu VM (Multipass runs Ubuntu only).
- Windows machines.
- Running several labs side by side, or adding one lab's servers to another.
- A full-screen (dialog/whiptail) menu.
- Exposing any lab outside the Multipass NAT network.

## Anti-goals (a failure even if it works)

- A menu that hides what will be deployed or how heavy it is: the summary must be accurate
  (generated from the same data the deploy uses, never hand-maintained text that can drift).
- Breaking the current deploy: `soc large` must still build a working blue-team lab, and the
  existing safety rules hold (no concurrent `multipass` commands, `say` for all output, serial
  polling, never redirecting `multipass exec` to `/dev/null`).
- "Fixing" the planted weaknesses: they are the exercises.

## Constraints

- Bash 4+ (Homebrew bash), Apple-silicon macOS, arm64 Ubuntu 24.04 images. Anything a lab
  installs must exist for arm64 (choose alternatives where an app is x86-only).
- All output through `say` / `lib/progress.sh`; the menu runs before the live panel starts.
- Templates keep passing `tests/test_templates.py` (placeholders, valid cloud-config, ShellCheck).
- `lab-info.sh`, `lab-doctor.sh`, README (architecture table, resource totals) and CLAUDE.md stay
  in sync with every new VM and app.

## The labs

RAM figures use today's VM specs (`VM_SPEC`, clients 2 GB each); new VMs have proposed specs.
Clients are domain-joined workstations with the simulated users (`seed/labdata.py`), except in
DevSecOps, where they are developer workstations (git, editor, CLI tools) that push to Gitea.

### 1. SOC / blue team — detect, triage and respond

Purpose: build and tune detections, triage alerts, run an investigation from alert to case.
The CI/CD servers move out to the DevSecOps lab (Johan's choice); `mgmt` is dropped from SOC to keep
large under 48 GB (my call).

| Size | VMs | ≈RAM |
|---|---|---|
| small | dc01, siem, sensor, redteam (to create alerts), 1 client | 18 GB |
| medium | + fs01, velociraptor, iris, honeypot; 3 clients | 32 GB |
| large | + mail, caldera, lam, ansible-ctrl; 6 clients | 47 GB |

### 2. Network forensics — capture and analyse traffic

Purpose: find attacks and data theft in packet captures and network logs.

- New `arkime` VM (≈6 GB): Arkime full packet capture and viewer (with its OpenSearch), plus the
  analyst CLI tools (tshark, Zeek, ngrep, tcpflow) and a PCAP library.
- Traffic is live (scripted attacks from `redteam`, simulated users on the clients, AD/DNS/Kerberos,
  SMB and mail traffic) **and** a set of public case PCAPs (e.g. malware-traffic-analysis.net
  exercises) downloaded into the PCAP library (Johan's choice). Live attacks run as a scheduled
  scenario so there is always something to find.
- Multipass NAT gives each VM only its own traffic, so in this lab the clients and targets use
  `sensor` as their default gateway (it forwards and NATs), and `sensor` mirrors what it forwards to
  Arkime. This gives a real network-tap view (my call; the alternative is a capture agent on every
  VM shipping PCAPs).

| Size | VMs | ≈RAM |
|---|---|---|
| small | sensor, arkime, dc01, redteam, vulntarget, 1 client | 21 GB |
| medium | + fs01, mail, honeypot; 3 clients | 30 GB |
| large | + siem (correlate alerts with packets); 6 clients | 42 GB |

### 3. Pentest — attack a weak company network

Purpose: practise the whole attack chain: recon, web exploitation, AD attacks, lateral movement and
Linux privilege escalation, against many vulnerable clients and servers.

- `redteam` gets the main Kali tools installed natively on Ubuntu (Johan's choice), on top of
  today's set: Metasploit, Burp Suite Community, sqlmap, hydra, john, hashcat, gobuster/ffuf,
  nikto, responder, impacket, netexec, bloodhound, enum4linux-ng, seclists and wordlists (exact
  list: builder's call, arm64-available only).
- Targets (Johan's choices):
  - **Vulnerable web apps:** `vulntarget` (DVWA, Juice Shop) and a new `webtarget2` (≈3 GB) with
    more apps such as WebGoat and others available for arm64.
  - **Weak Active Directory:** `dc01`, `fs01` and the clients with today's planted weaknesses
    (kerberoastable `svc_sql`, AS-REP `svc_legacy`, weak seeded passwords) plus more for the attack
    chain: weak ACLs, passwords in share files and user descriptions, local admin reuse on clients.
  - **Linux privesc boxes:** new `privesc01..N` VMs (≈1 GB each), each with a different
    misconfiguration (SUID binary, sudo rule, writable cron job or service, weak SSH key, secrets in
    files), with a flag file for root.
- No SIEM, so attacks aren't detected (pure offence).

| Size | VMs | ≈RAM |
|---|---|---|
| small | redteam, vulntarget, dc01, 1 privesc box, 1 client | 13 GB |
| medium | + fs01, webtarget2, 2 more privesc boxes; 3 clients | 24 GB |
| large | + mail, 1 more privesc box (4 in total); 6 clients | 33 GB |

### 4. DevSecOps CI/CD — secure (and break) a pipeline

Purpose: see how code, dependencies, containers and the pipeline itself are attacked and defended.
All four parts are in (Johan's choice):

- `gitea` (Git server, Actions runner, container registry) and `prod` (deploy target), as today.
- **Code and dependency scans** as pipeline steps that can fail the build: Semgrep (SAST),
  Gitleaks (secrets), OSV-Scanner (dependencies).
- **Container and IaC scans:** Trivy (image, Dockerfile, IaC), cosign image signing and a policy
  check before `prod` pulls an image.
- **A vulnerable demo app and pipeline** to find and fix: a leaked secret in history, unpinned
  actions, a poisonable pipeline (e.g. a workflow that runs untrusted PR code with secrets), a
  vulnerable dependency, an over-privileged runner.
- **DAST and monitoring:** a new `dast` VM (≈2 GB) running OWASP ZAP against the deployed app; in
  large, `siem` receives Gitea, runner and registry logs.

| Size | VMs | ≈RAM |
|---|---|---|
| small | gitea, prod, 1 developer client | 8 GB |
| medium | + dast; 3 developer clients | 14 GB |
| large | + siem (pipeline and registry events); 6 developer clients | 26 GB |

## How it works (decided)

- Labs and sizes are data, not code paths: one definition per lab (VM list per size, client count,
  purpose text, client kind, time weights). The menu, `--list`, the summary totals, the deploy's
  `VM_ENABLED` / `NUM_CLIENTS`, `lab-info.sh` and `lab-doctor.sh` all read the same definition.
- A state file records the running lab and size (written at deploy, removed by teardown).
- The menu: numbered choices, `b` to go back, `q` to quit, default answers in brackets; it never
  launches anything before the final confirmation.

## Decisions from the design review

- **Case PCAPs (Johan's choice):** `arkime` downloads a pinned list of case PCAPs during
  provisioning (fixed URLs, SHA-256 checked); they never touch the Mac's disk, stay in their
  password-protected zips until opened, and go away with the VM at teardown.
- **No terminal (my call):** without `--lab`/`--size` and without a terminal on stdin, the script
  exits with an error that names the flags. It never deploys a default lab.
- **`soc large` (my call):** means the new, trimmed SOC lab. Wiring to `gitea`, `prod` and `mgmt`
  (inventory, lab-info, agents, scenarios) becomes conditional on those VMs being in the lab. There
  is no hidden "everything" option.
- **Running lab without a state file (my call):** a lab counts as running if any known lab VM
  shows in `multipass list`; teardown removes every known lab VM, state file or not.
- **RAM (my call):** the totals computed from the VM specs are the truth, and the tables above are
  estimates. A test asserts every lab's large size is at most 48 GB; at deploy time the script only
  warns when the Mac has less RAM than the lab needs plus headroom.
- **Measured times (my call):** kept in the lab definitions and updated by hand after a test run of
  each lab and size; the deploy prints its real elapsed time at the end so there is a number to
  copy in.
- **DevSecOps clients (my call):** a separate developer-client template with no domain join;
  `siem` there runs without `dc01`.

## Left to the builder

- The file format for the lab definitions and where they live (e.g. `labs/*.conf` sourced by bash,
  or one table in the deploy script).
- The exact tool lists, app choices for `webtarget2`, the privesc scenarios, the forensics attack
  scenario and which case PCAPs to fetch, as long as they are arm64-capable and stay inside the lab.
- How a VM template differs per lab (e.g. `redteam` with or without the Kali tool set, `sensor` as
  gateway only in forensics) — template variables or separate templates.
- The rough time formula (e.g. serial launch time per VM plus the slowest provisioning VM), to be
  replaced by measured times.
- VM specs for the new VMs, within the RAM ceiling.

## Open risks

- Arkime, Metasploit and some web apps may have no arm64 builds; the builder must check each and
  pick alternatives.
- Routing forensics traffic through `sensor` changes DNS and default routes on members; dc01 joins
  and package installs must still work.
- Measured times may differ a lot from the estimates (siem and caldera already take 20–45 minutes).
