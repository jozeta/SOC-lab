#!/usr/bin/env python3
"""labdata.py -- random, up-to-date test data that makes the SOC lab feel lived in.

deploy-soc-lab.sh copies this file to the VMs after provisioning and runs one
role on each, as root:

    python3 labdata.py roster <seed> <clients>    print a new staff roster (JSON)
    python3 labdata.py <role> <config> <roster>   install that VM's data
    python3 labdata.py services <roster>          the roster's service accounts (adds new ones)

Roles: dc01, fs01, client, gitea, mgmt, vulntarget, ansible-ctrl, sensor,
lam, velociraptor, mail.

Some roles also install traffic that never stops, so every tool keeps getting
logs: simulated staff on each client (Kerberos logons, file shares, web apps
with real logins, FTP, telnet, SSH, downloads, git), routine Ansible runs from
ansible-ctrl, scheduled Semaphore jobs and Uptime Kuma checks on mgmt, a
Velociraptor analyst running hunts, and a backup job on fs01. They run this
same file as "labdata.py loop <config>" (or "run <config>" for one pass).

Everything is generated: names, titles, passwords, files, invoices, commits.
Dates are relative to "today" on the VM, so a lab built next year looks
current next year. The roster is generated once per lab (the deploy script
keeps it in .rendered-cloud-init/lab-roster.json) so every VM agrees on who
works here. Standard library only: no pip, no third-party downloads.

Re-running a role is safe: existing users, files, repos and projects are kept.
"""
import base64
import csv
import datetime as dt
import io
import json
import os
import pwd
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

TODAY = dt.date.today()
NOW = dt.datetime.now()

# --------------------------------------------------------------------------
# Word lists (fictional; nothing here is a real person or company)
# --------------------------------------------------------------------------
FIRST = """Emma Liam Olivia Noah Ava Elias Maja Hugo Alice William Ella Lucas Wilma Oscar
Freja Leo Saga Axel Astrid Ludvig Ebba Isak Alva Theo Selma Viktor Signe Nils Agnes Erik
Clara Anton Hanna Filip Ida Gustav Linnea Johan Sara Henrik Nora Mats Karin Daniel Lena
Samir Amira Yusuf Leila Mateo Sofia Priya Arjun Mei Chen Tomas Anja Pekka Aino Fatima
Omar Grace Ethan Chloe James Zoe Rafael Ines Jonas Petra""".split()
LAST = """Lindqvist Andersson Johansson Karlsson Nilsson Eriksson Larsson Olsson Persson
Svensson Gustafsson Pettersson Jonsson Jansson Hansson Bengtsson Lindberg Lindström
Lundgren Berg Holm Ekström Sandberg Forsberg Sjöberg Wallin Engström Nyberg Hedlund
Björk Åberg Lund Mattsson Fredriksson Henriksson Sundberg Axelsson Nordin Khan Ahmed
Novak Moreau Rossi Müller García Silva Kowalski Virtanen Nguyen Patel Okafor Brennan
Murphy Walsh Fischer Jensen Hagen Dahl Strand""".split()
ASCII = str.maketrans({"å": "a", "ä": "a", "ö": "o", "é": "e", "ü": "u", "í": "i",
                       "Å": "a", "Ä": "a", "Ö": "o"})

# department -> (head title, other titles, relative headcount)
DEPARTMENTS = {
    "Finance":     ("Head of Finance", ["Accountant", "Financial Controller",
                                       "Payroll Specialist", "Accounts Payable Clerk"], 6),
    "HR":          ("HR Manager", ["HR Generalist", "Recruiter", "HR Coordinator"], 4),
    "Engineering": ("Engineering Manager", ["Software Engineer", "Senior Software Engineer",
                                           "DevOps Engineer", "QA Engineer"], 10),
    "Sales":       ("Sales Director", ["Account Executive", "Sales Engineer",
                                       "Customer Success Manager"], 8),
    "Marketing":   ("Marketing Manager", ["Marketing Specialist", "Content Writer",
                                          "Graphic Designer"], 5),
    "IT":          ("IT Manager", ["Systems Administrator", "Service Desk Analyst",
                                   "Network Engineer"], 6),
    "Legal":       ("General Counsel", ["Legal Counsel", "Compliance Officer"], 3),
    "Operations":  ("Head of Operations", ["Operations Analyst", "Office Manager",
                                           "Facilities Coordinator"], 4),
}
OFFICES = ["Stockholm HQ", "Stockholm HQ", "Stockholm HQ", "Gothenburg", "Malmö", "London", "Remote"]
WORDS = """Maple Orbit Cedar Harbor Falcon Granite River Copper Lantern Meadow Summit Willow
Raven Comet Pebble Ember Glacier Thistle Juniper Nimbus Quartz Sparrow Tundra Velvet""".split()
CUSTOMER_A = ["Nordic", "Baltic", "Blue", "Northern", "Green", "Silver", "Polar", "Coastal",
              "Iron", "Bright", "Alpine", "Urban", "Summit", "Harbor"]
CUSTOMER_B = ["Logistics", "Foods", "Analytics", "Retail", "Energy", "Health", "Media",
              "Robotics", "Freight", "Textiles", "Pharma", "Consulting", "Labs", "Marine"]
CUSTOMER_C = ["AB", "AB", "Oy", "AS", "Ltd", "GmbH", "ApS"]
PROJECTS = ["Aurora", "Basalt", "Cobalt", "Driftwood", "Evergreen", "Fjord", "Gemini",
            "Halcyon", "Ironclad", "Juniper"]

ALL_STAFF_GROUP = "All-Staff"
VPN_GROUP = "VPN-Users"


def season(d):
    return {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
            6: "Summer", 7: "Summer", 8: "Summer"}.get(d.month, "Autumn")


def quarter(d):
    return f"Q{(d.month - 1) // 3 + 1}-{d.year}"


def recent_time(rng, max_days, min_days=0):
    """A timestamp in the past, weighted towards recent days, during office hours."""
    days = min_days + int((max_days - min_days) * rng.random() ** 2)
    d = NOW - dt.timedelta(days=days)
    while d.weekday() >= 5:                       # people rarely save on weekends
        d -= dt.timedelta(days=1)
    d = d.replace(hour=rng.randint(7, 18), minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)
    if d > NOW:                                   # "today" means earlier today
        d = NOW - dt.timedelta(minutes=rng.randint(5, 180))
    return d


def log(msg):
    # The prefix lets deploy-soc-lab.sh pick out the result line from tool noise
    # (Velociraptor's gRPC warnings, apt) that lands in the same log.
    print(f"[labdata] {msg}", flush=True)


def run(cmd, check=True, timeout=600, **kw):
    """Run a command; on failure show its output (the deploy log keeps it).
    Never lets a tool wait for keyboard input (smbclient, git and ssh fall
    back to password prompts), and never runs longer than `timeout`."""
    if "input" not in kw:
        kw["stdin"] = subprocess.DEVNULL
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError(f"{' '.join(cmd)} timed out after {timeout}s")
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({res.returncode}): "
                           f"{res.stdout.strip()} {res.stderr.strip()}")
    return res


# --------------------------------------------------------------------------
# Roster (generated once, on the deploy host's behalf)
# --------------------------------------------------------------------------
def make_password(rng):
    a, b = rng.sample(WORDS, 2)
    return f"{a}{b}{rng.randint(10, 99)}{rng.choice('!#%+?')}"


def roster(seed, num_clients, size=46):
    rng = random.Random(seed)
    used_names, used_sams, users = set(), set(), []
    weights = {d: v[2] for d, v in DEPARTMENTS.items()}
    total = sum(weights.values())
    for dept, (head, titles, w) in DEPARTMENTS.items():
        count = max(2, round(size * w / total))
        for i in range(count):
            while True:
                given, sn = rng.choice(FIRST), rng.choice(LAST)
                if (given, sn) not in used_names:
                    break
            used_names.add((given, sn))
            sam = f"{given}.{sn}".lower().translate(ASCII)
            n = 2
            while sam in used_sams:
                sam = f"{given}.{sn}{n}".lower().translate(ASCII)
                n += 1
            used_sams.add(sam)
            hired = TODAY - dt.timedelta(days=int(3000 * rng.random() ** 1.5) + 3)
            users.append({
                "sam": sam, "given": given, "sn": sn, "dept": dept,
                "title": head if i == 0 else rng.choice(titles),
                "office": rng.choice(OFFICES),
                "phone": f"+46 8 555 {rng.randint(100, 999)} {rng.randint(10, 99)}",
                "employee_id": f"E{rng.randint(10000, 99999)}",
                "hired": hired.isoformat(), "left": None, "enabled": True,
                "password": make_password(rng), "weak": None, "vpn": rng.random() < 0.45,
                "salary": rng.randrange(38000, 95000, 500) + (30000 if i == 0 else 0),
                "client": None,
            })
    by_dept = {}
    for u in users:
        by_dept.setdefault(u["dept"], []).append(u)
    for members in by_dept.values():
        for u in members[1:]:
            u["manager"] = members[0]["sam"]
        members[0]["manager"] = None
    staff = [u for u in users if u["manager"]]
    # Realistic weak spots for the spraying exercise: new hires still on the
    # welcome password, and a few people on "<Season><Year>!".
    newest = sorted(staff, key=lambda u: u["hired"], reverse=True)[:2]
    for u in newest:
        u["hired"] = (TODAY - dt.timedelta(days=rng.randint(3, 25))).isoformat()
        u["password"], u["weak"] = f"Welcome{TODAY.year}!", "new-hire default password"
    for u in rng.sample([u for u in staff if not u["weak"]], 3):
        u["password"], u["weak"] = f"{season(TODAY)}{TODAY.year}!", "season+year password"
    # Leavers: disabled accounts nobody cleaned up.
    for u in rng.sample([u for u in staff if not u["weak"]], 3):
        u["enabled"] = False
        u["left"] = (TODAY - dt.timedelta(days=rng.randint(10, 120))).isoformat()
    # Each client is somebody's workstation (3 people share one in the lab).
    # Each workstation gets one Engineering/IT person (git, SSH, telnet traffic).
    active = [u for u in users if u["enabled"]]
    rng.shuffle(active)
    tech = [u for u in active if u["dept"] in ("Engineering", "IT")]
    for c in range(1, num_clients + 1):
        name = f"client{c:02d}"
        pick = [u for u in tech if not u["client"]][:1]
        pick += [u for u in active if not u["client"] and u not in pick][:3 - len(pick)]
        for u in pick:
            u["client"] = name
    computers = []
    for d in DEPARTMENTS:
        for _ in range(rng.randint(2, 4)):
            kind = rng.choice(["WS", "LT", "LT"])
            name = f"{kind}-{d[:3].upper()}-{rng.randint(100, 999)}"
            if any(c["name"] == name for c in computers):    # random numbers can repeat
                continue
            computers.append({
                "name": name,
                "os": "Windows 11 Enterprise" if rng.random() < 0.85 else "macOS 15",
                "desc": f"{'Laptop' if kind == 'LT' else 'Desktop'}, {d}",
            })
    computers += [
        {"name": "SRV-PRINT01", "os": "Windows Server 2022", "desc": "Print server, Stockholm HQ"},
        {"name": "SRV-SQL01", "os": "Windows Server 2019", "desc": "Billing database (svc_sql)"},
        {"name": "SRV-BACKUP01", "os": "Windows Server 2016", "desc": "Backup server (svc_legacy) -- decommission pending"},
    ]
    company = "Kestrel Nordic AB"
    result = {"company": company, "generated": TODAY.isoformat(), "seed": seed,
              "users": users, "computers": computers}
    result["services"] = service_accounts(result)
    return result


# --------------------------------------------------------------------------
# dc01: OUs, groups, users, managers, computers -- written locally with
# samba-tool, so seeding does not show up as hundreds of audit alerts.
# --------------------------------------------------------------------------
def role_dc01(cfg, ros):
    base_dn = "DC=" + ",DC=".join(cfg["realm"].lower().split("."))
    have_ous = set(run(["samba-tool", "ou", "list"]).stdout.split())
    have_groups = set(run(["samba-tool", "group", "list"]).stdout.splitlines())
    have_users = set(run(["samba-tool", "user", "list"]).stdout.splitlines())
    have_computers = {c.rstrip("$").upper() for c in run(["samba-tool", "computer", "list"]).stdout.split()}

    for dept in DEPARTMENTS:
        if f"OU={dept},OU=LabUsers" not in have_ous:
            run(["samba-tool", "ou", "create", f"OU={dept},OU=LabUsers,{base_dn}"])
    for g, desc in [(f"Dept-{d}", f"Everyone in {d}") for d in DEPARTMENTS] + [
            (VPN_GROUP, "Allowed to use the remote-access VPN")]:
        if g not in have_groups:
            run(["samba-tool", "group", "add", g, f"--description={desc}"])
    if ALL_STAFF_GROUP not in have_groups:
        run(["samba-tool", "group", "add", ALL_STAFF_GROUP, "--group-type=Distribution",
             "--description=all-staff@ mailing list"])

    created = 0
    for u in ros["users"]:
        if u["sam"] in have_users:
            continue
        desc = f"Left the company {u['left']}" if u["left"] else f"Hired {u['hired']}"
        run(["samba-tool", "user", "create", u["sam"], u["password"],
             f"--given-name={u['given']}", f"--surname={u['sn']}",
             f"--userou=OU={u['dept']},OU=LabUsers", f"--department={u['dept']}",
             f"--job-title={u['title']}", f"--company={ros['company']}",
             f"--physical-delivery-office={u['office']}", f"--telephone-number={u['phone']}",
             f"--mail-address={u['sam']}@{cfg['realm'].lower()}", f"--description={desc}"])
        if not u["enabled"]:
            run(["samba-tool", "user", "disable", u["sam"]])
        created += 1

    members = {f"Dept-{d}": [] for d in DEPARTMENTS}
    members.update({VPN_GROUP: [], ALL_STAFF_GROUP: [], "Finance": [], "HelpDesk": [], "IT-Admins": []})
    for u in ros["users"]:
        members[f"Dept-{u['dept']}"].append(u["sam"])
        if u["enabled"]:
            members[ALL_STAFF_GROUP].append(u["sam"])
        if u["vpn"]:
            members[VPN_GROUP].append(u["sam"])   # leavers keep VPN access: a finding
        if u["dept"] == "Finance":
            members["Finance"].append(u["sam"])   # -> read access to \\fs01\finance
        if u["title"] in ("Service Desk Analyst",):
            members["HelpDesk"].append(u["sam"])
        if u["title"] in ("Systems Administrator", "IT Manager", "Network Engineer"):
            members["IT-Admins"].append(u["sam"])
    for g, sams in members.items():
        if sams:
            run(["samba-tool", "group", "addmembers", g, ",".join(sams)], check=False)

    # Manager and employee ID are not samba-tool options: one LDIF via ldbmodify.
    sam_db = "/var/lib/samba/private/sam.ldb"
    dn_of = {}
    for u in ros["users"]:
        dn_of[u["sam"]] = f"CN={u['given']} {u['sn']},OU={u['dept']},OU=LabUsers,{base_dn}"
    ldif = []
    for u in ros["users"]:
        ldif += [f"dn: {dn_of[u['sam']]}", "changetype: modify",
                 "replace: employeeID", f"employeeID: {u['employee_id']}", "-"]
        if u["manager"]:
            ldif += ["replace: manager", f"manager: {dn_of[u['manager']]}", "-"]
        ldif.append("")
    with tempfile.NamedTemporaryFile("w", suffix=".ldif", delete=False) as fh:
        fh.write("\n".join(ldif))
    run(["ldbmodify", "-H", sam_db, fh.name])
    os.unlink(fh.name)

    for c in ros["computers"]:
        if c["name"].upper() in have_computers:
            continue
        have_computers.add(c["name"].upper())        # older rosters can list a name twice
        run(["samba-tool", "computer", "add", c["name"], "--computerou=OU=LabComputers",
             f"--description={c['desc']}"])
        dn = f"CN={c['name']},OU=LabComputers,{base_dn}"
        with tempfile.NamedTemporaryFile("w", suffix=".ldif", delete=False) as fh:
            fh.write(f"dn: {dn}\nchangetype: modify\nreplace: operatingSystem\n"
                     f"operatingSystem: {c['os']}\n-\n")
        run(["ldbmodify", "-H", sam_db, fh.name], check=False)
        os.unlink(fh.name)
    disabled = sum(not u["enabled"] for u in ros["users"])
    log(f"{created} new staff accounts ({len(ros['users'])} total, {disabled} disabled leavers), "
        f"{len(DEPARTMENTS)} department OUs and groups, {len(ros['computers'])} computer accounts")


# --------------------------------------------------------------------------
# File helpers
# --------------------------------------------------------------------------
def docx(paragraphs):
    """A minimal but valid Word document (opens in Word and LibreOffice)."""
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    heading_ppr = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
    parts = []
    for p in paragraphs:
        head = p.startswith("# ")
        parts.append("<w:p>" + (heading_ppr if head else "") + "<w:r>"
                     + ("<w:rPr><w:b/></w:rPr>" if head else "")
                     + '<w:t xml:space="preserve">' + esc(p[2:] if head else p) + "</w:t></w:r></w:p>")
    body = "".join(parts)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f"<w:body>{body}</w:body></w:document>")
    return buf.getvalue()


def csv_text(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


WRITERS = []


class Writer:
    """Writes files with a past modification time and an owner, never
    overwriting. Once a role has filled a folder it is marked as seeded, and
    later runs leave it alone (even if a newer labdata.py would pick other
    random file names)."""

    def __init__(self, root, rng, default_owner=None, group=None):
        self.root, self.rng, self.owner, self.group, self.count = root, rng, default_owner, group, 0
        self.marker = "/var/lib/labdata/" + root.strip("/").replace("/", "_") + ".seeded"
        self.frozen = os.path.exists(self.marker)
        WRITERS.append(self)

    def finish(self):
        os.makedirs(os.path.dirname(self.marker), exist_ok=True)
        open(self.marker, "w").close()

    def put(self, rel, content, when=None, owner=None, mode=0o664):
        path = os.path.join(self.root, rel)
        if self.frozen or os.path.exists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = content if isinstance(content, bytes) else content.encode()
        with open(path, "wb") as fh:
            fh.write(data)
        os.chmod(path, mode)
        uid = gid = -1
        try:
            if owner or self.owner:
                uid = pwd.getpwnam(owner or self.owner).pw_uid
            if self.group:
                import grp
                gid = grp.getgrnam(self.group).gr_gid
            os.chown(path, uid, gid)
        except (KeyError, OSError):
            pass                                  # unknown to NSS: leave root-owned
        when = when or recent_time(self.rng, 200)
        ts = time.mktime(when.timetuple())
        os.utime(path, (ts, ts))
        self.count += 1


def settle_dirs(root):
    """Give every folder the time of its newest file, like a real share."""
    for d, _, names in sorted(os.walk(root), key=lambda t: -t[0].count(os.sep)):
        times = [os.path.getmtime(os.path.join(d, n)) for n in names] + \
                [os.path.getmtime(os.path.join(d, n)) for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))]
        if times and d != root:
            os.utime(d, (max(times), max(times)))


def customers(rng, n):
    names = set()
    while len(names) < n:
        names.add(f"{rng.choice(CUSTOMER_A)} {rng.choice(CUSTOMER_B)} {rng.choice(CUSTOMER_C)}")
    return sorted(names)


def fake_iban(rng):
    return "SE" + "".join(str(rng.randint(0, 9)) for _ in range(22))


# --------------------------------------------------------------------------
# fs01: a company file share with years of "real" documents
# --------------------------------------------------------------------------
def role_fs01(cfg, ros):
    rng = random.Random(ros["seed"] + 1)
    users = ros["users"]
    active = [u for u in users if u["enabled"]]
    dept = {d: [u for u in active if u["dept"] == d] for d in DEPARTMENTS}
    company, realm = ros["company"], cfg["realm"].lower()
    pub = Writer("/srv/shares/public", rng)
    fin = Writer("/srv/shares/finance", rng, group="finance")

    def who(d):
        return rng.choice(dept[d] or active)["sam"]

    # --- Company-wide ---
    phone = [["Name", "Title", "Department", "Office", "Phone", "Email"]]
    for u in sorted(active, key=lambda u: u["sn"]):
        phone.append([f"{u['given']} {u['sn']}", u["title"], u["dept"], u["office"],
                      u["phone"], f"{u['sam']}@{realm}"])
    pub.put("Company/Phone-List.csv", csv_text(phone), recent_time(rng, 14), who("Operations"))
    org = [f"# {company} -- organisation ({TODAY:%B %Y})", ""]
    for d in DEPARTMENTS:
        head = next((u for u in users if u["dept"] == d and not u["manager"]), None)
        if head:
            org.append(f"## {d} -- {head['given']} {head['sn']} ({head['title']})")
            org += [f"- {u['given']} {u['sn']}, {u['title']}" for u in dept[d] if u is not head]
            org.append("")
    pub.put("Company/Org-Chart.md", "\n".join(org), recent_time(rng, 30), who("HR"))
    pub.put(f"Company/Employee-Handbook-{TODAY.year}.docx", docx([
        f"# {company} Employee Handbook {TODAY.year}",
        "Welcome! This handbook explains how we work, our benefits and our policies.",
        "# Working hours", "Core hours are 09:00-15:00. Flexible time outside core hours.",
        "# IT accounts", f"Your account is firstname.lastname@{realm}. Never share your password.",
        "New starters receive a temporary password from the Service Desk and must change it.",
        "# Expenses", "Submit receipts within 30 days through the expense system."]),
        recent_time(rng, 90, 30), who("HR"))
    holidays = [["Date", "Holiday"]] + [[f"{TODAY.year}-{m}", n] for m, n in [
        ("01-01", "New Year's Day"), ("01-06", "Epiphany"), ("05-01", "May Day"),
        ("06-06", "National Day"), ("06-20", "Midsummer Eve"), ("12-24", "Christmas Eve"),
        ("12-25", "Christmas Day"), ("12-26", "Boxing Day"), ("12-31", "New Year's Eve")]]
    pub.put(f"Company/Holiday-Calendar-{TODAY.year}.csv", csv_text(holidays), recent_time(rng, 300, 200))
    for i in range(6):
        d = (TODAY.replace(day=1) - dt.timedelta(days=30 * i))
        pub.put(f"Company/Newsletters/Newsletter-{d:%Y-%m}.docx", docx([
            f"# {company} newsletter -- {d:%B %Y}",
            f"Welcome to our new colleagues in {rng.choice(list(DEPARTMENTS))}!",
            f"Project {rng.choice(PROJECTS)} reached its {rng.choice(['beta', 'pilot', 'launch'])} milestone.",
            f"Reminder: the office in {rng.choice(OFFICES[:-1])} is closed on Friday for maintenance."]),
            dt.datetime.combine(d, dt.time(10, 0)), who("Marketing"))

    # --- HR ---
    for pol in ["Remote-Work-Policy", "Travel-and-Expense-Policy", "Code-of-Conduct",
                "Information-Security-Policy", "Acceptable-Use-Policy", "Parental-Leave-Policy"]:
        title = pol.replace("-", " ")
        pub.put(f"HR/Policies/{pol}.docx", docx([
            f"# {title}", f"Owner: HR. Version {rng.randint(1, 4)}.{rng.randint(0, 9)}, reviewed {quarter(TODAY)}.",
            "# Scope", f"Applies to all employees and contractors of {company}.",
            "# Policy", "Lorem ipsum policy text. Questions go to hr@" + realm + "."]),
            recent_time(rng, 400, 60), who("HR"))
    for name in ["Onboarding-Checklist", "Offboarding-Checklist", "Performance-Review-Template"]:
        pub.put(f"HR/Templates/{name}.docx", docx([f"# {name.replace('-', ' ')}",
                "[ ] Item one", "[ ] Item two", "[ ] Sign-off by manager"]), recent_time(rng, 500, 100))
    leavers = [u for u in users if not u["enabled"]]
    offb = [["Name", "Last day", "Account disabled", "VPN removed", "Laptop returned"]]
    offb += [[f"{u['given']} {u['sn']}", u["left"], "yes", "no" if u["vpn"] else "n/a",
              rng.choice(["yes", "no", "pending"])] for u in leavers]
    pub.put(f"HR/Offboarding-Tracker-{TODAY.year}.csv", csv_text(offb), recent_time(rng, 7), who("HR"))

    # --- IT (with a classic mistake left in a script) ---
    pub.put("IT/Service-Desk-FAQ.md", "\n".join([
        "# Service Desk FAQ", "",
        "**Forgot your password?** Call the Service Desk on +46 8 555 100 00.",
        f"**New starter?** Your first password is Welcome{TODAY.year}! -- change it on first login.",
        "**File shares:** \\\\fs01\\public (everyone), \\\\fs01\\finance (Finance only).",
        "**VPN:** request access through your manager (group VPN-Users)."]),
        recent_time(rng, 60), who("IT"))
    pub.put("IT/Guest-WiFi.txt", f"SSID: Kestrel-Guest\nPassword: {rng.choice(WORDS)}{TODAY.year}\n"
            f"Rotated: {(TODAY - dt.timedelta(days=rng.randint(5, 40))).isoformat()}\n",
            recent_time(rng, 40), who("IT"))
    pub.put("IT/scripts/backup-job.sh", "\n".join([
        "#!/bin/bash", "# Nightly copy of the finance share to SRV-BACKUP01.",
        "# TODO: move the credentials to a vault before the audit!",
        "BACKUP_USER=svc_legacy", f"BACKUP_PASS='{cfg['svc_legacy_pass']}'",
        f"smbclient //fs01.{realm}/finance -U \"$BACKUP_USER%$BACKUP_PASS\" -c 'prompt; recurse; mget *'", ""]),
        recent_time(rng, 700, 300), who("IT"), mode=0o775)
    inv = [["Hostname", "Type", "Operating system", "Department", "Last seen"]]
    for c in ros["computers"]:
        inv.append([c["name"], c["desc"].split(",")[0], c["os"],
                    c["desc"].split(", ")[-1], recent_time(rng, 30).date().isoformat()])
    pub.put("IT/Asset-Inventory.csv", csv_text(inv), recent_time(rng, 10), who("IT"))
    for i in range(8):
        when = recent_time(rng, 120)
        pub.put(f"IT/Change-Requests/CR-{when:%Y%m%d}-{rng.randint(100, 999)}.md", "\n".join([
            f"# Change request {when:%Y-%m-%d}",
            f"Requested by: {who('IT')}", f"System: {rng.choice(['dc01', 'fs01', 'siem', 'VPN', 'firewall', 'SRV-SQL01'])}",
            f"Change: {rng.choice(['Patch to latest', 'Rotate certificate', 'Increase disk', 'Firewall rule for vendor', 'Disable SMBv1'])}",
            f"Risk: {rng.choice(['Low', 'Low', 'Medium', 'High'])}", f"Status: {rng.choice(['Approved', 'Done', 'Done', 'Rejected'])}"]),
            when, who("IT"))

    # --- Sales / Marketing ---
    custs = customers(rng, 18)
    pipe = [["Customer", "Owner", "Stage", "Value (SEK)", "Expected close"]]
    for c in custs:
        pipe.append([c, who("Sales"), rng.choice(["Lead", "Qualified", "Proposal", "Negotiation", "Won", "Lost"]),
                     rng.randrange(50000, 2500000, 5000), (TODAY + dt.timedelta(days=rng.randint(-30, 120))).isoformat()])
    pub.put(f"Sales/Pipeline-{quarter(TODAY)}.csv", csv_text(pipe), recent_time(rng, 5), who("Sales"))
    prices = [["SKU", "Product", "List price (SEK)"]] + [
        [f"KN-{rng.randint(1000, 9999)}", f"{rng.choice(WORDS)} {rng.choice(['Basic', 'Pro', 'Enterprise', 'Support'])}",
         rng.randrange(990, 99000, 10)] for _ in range(14)]
    pub.put(f"Sales/Price-List-{TODAY.year}.csv", csv_text(prices), recent_time(rng, 200, 60), who("Sales"))
    for c in rng.sample(custs, 6):
        pub.put(f"Sales/Proposals/{c.replace(' ', '-')}-proposal.docx", docx([
            f"# Proposal for {c}", f"Prepared by {who('Sales')}, {TODAY:%B %Y}",
            "Scope, pricing and timeline as discussed."]), recent_time(rng, 90), who("Sales"))
    pub.put(f"Marketing/Campaign-Plan-{quarter(TODAY)}.docx", docx([
        f"# Campaign plan {quarter(TODAY)}", "Channels: LinkedIn, newsletter, two webinars.",
        f"Budget: {rng.randrange(80, 400)} kSEK."]), recent_time(rng, 30), who("Marketing"))
    pub.put("Marketing/Brand-Guidelines.md", "# Brand guidelines\n\nPrimary colour: #1F4E79. Font: Inter.\n",
            recent_time(rng, 600, 200), who("Marketing"))

    # --- Projects: meeting notes from the last weeks ---
    for p in rng.sample(PROJECTS, 5):
        owner = rng.choice(active)
        pub.put(f"Projects/{p}/README.md", f"# Project {p}\n\nOwner: {owner['given']} {owner['sn']}\n"
                f"Status: {rng.choice(['green', 'green', 'amber', 'red'])}\n", recent_time(rng, 60), owner["sam"])
        for _ in range(rng.randint(3, 7)):
            when = recent_time(rng, 75)
            att = rng.sample(active, 4)
            pub.put(f"Projects/{p}/Meeting-Notes-{when:%Y-%m-%d}.docx", docx([
                f"# {p} weekly -- {when:%Y-%m-%d}",
                "Attendees: " + ", ".join(f"{a['given']} {a['sn']}" for a in att),
                f"Decision: {rng.choice(['ship the pilot', 'delay one sprint', 'add a security review', 'hire a contractor'])}.",
                f"Action: {att[0]['given']} to follow up by {(when + dt.timedelta(days=7)):%Y-%m-%d}."]),
                when, att[0]["sam"])

    # --- Finance (restricted share) ---
    for i in range(45):
        when = recent_time(rng, 180)
        c = rng.choice(custs)
        net = rng.randrange(4000, 450000, 50)
        num = f"INV-{when:%Y%m}-{rng.randint(1000, 9999)}"
        fin.put(f"Invoices/{when:%Y-%m}/{num}.txt", "\n".join([
            f"{company} -- INVOICE {num}", f"Date: {when:%Y-%m-%d}", f"Customer: {c}",
            f"Net: {net:,} SEK", f"VAT 25%: {net // 4:,} SEK", f"Total: {net + net // 4:,} SEK",
            f"Due: {(when + dt.timedelta(days=30)):%Y-%m-%d}", f"Pay to: {fake_iban(rng)}"]),
            when, who("Finance"), mode=0o660)
    budget = [["Department", "Headcount", f"Budget {TODAY.year} (kSEK)", "Spent YTD (kSEK)"]]
    for d in DEPARTMENTS:
        b = rng.randrange(800, 12000, 50)
        budget.append([d, len(dept[d]), b, int(b * TODAY.timetuple().tm_yday / 365 * rng.uniform(0.8, 1.15))])
    fin.put(f"Budgets/Budget-{TODAY.year}.csv", csv_text(budget), recent_time(rng, 20), who("Finance"), mode=0o660)
    fin.put(f"Budgets/Budget-{TODAY.year + 1}-DRAFT.csv", csv_text(budget), recent_time(rng, 4), who("Finance"), mode=0o660)
    payday = TODAY.replace(day=25) if TODAY.day >= 25 else (TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=25)
    for m in range(3):
        month = payday
        payday = (payday.replace(day=1) - dt.timedelta(days=1)).replace(day=25)
        pay = [["Employee ID", "Name", "Department", "Monthly salary (SEK)", "Bank account"]]
        pay += [[u["employee_id"], f"{u['given']} {u['sn']}", u["dept"], u["salary"], fake_iban(rng)]
                for u in sorted(active, key=lambda u: u["employee_id"])]
        fin.put(f"Payroll/Payroll-{month:%Y-%m}.csv", csv_text(pay),
                dt.datetime.combine(month - dt.timedelta(days=3), dt.time(14, 12)), who("Finance"), mode=0o660)
    vend = [["Vendor", "Org no", "IBAN", "Contact"]]
    vend += [[c, f"55{rng.randint(10000000, 99999999)}", fake_iban(rng), f"billing@{c.split()[0].lower()}.example"]
             for c in customers(rng, 12)]
    fin.put("Vendors/Vendor-Master.csv", csv_text(vend), recent_time(rng, 45), who("Finance"), mode=0o660)
    apt("smbclient")                                  # the backup job below needs it
    settle_dirs("/srv/shares/public")
    settle_dirs("/srv/shares/finance")
    # The backup job in IT/scripts really runs, twice a day (and fails on
    # finance: svc_legacy was never given access -- visible in the logs).
    install_timer("lab-backup", "Backup job from \\\\fs01\\public\\IT\\scripts",
                  {"profile": "backup", "log": "/var/log/lab-backup.log"},
                  calendar="*-*-* 01,13:30", spread_min=20)
    log(f"{pub.count} files on \\\\fs01\\public, {fin.count} on \\\\fs01\\finance; lab-backup.timer enabled")


# --------------------------------------------------------------------------
# Traffic: systemd timers that keep the lab busy, so every tool has logs.
# Each VM type runs a profile ("labdata.py run <config>"); everything is
# random, and staff work office hours (much quieter at night and weekends).
# Ground truth goes to /var/log/lab-*.log on the VM that did it.
# --------------------------------------------------------------------------
HISTORY = {
    "IT": ["sudo apt update && sudo apt upgrade -y", "ssh ubuntu@fs01", "kinit {sam}", "klist",
           "smbclient -L //fs01.{realm} -k", "systemctl status sssd", "realm list",
           "getent passwd {sam}@{realm}", "dig _ldap._tcp.{realm} SRV", "sudo journalctl -u sssd --since today",
           "sqlcmd -S SRV-SQL01 -U svc_sql -P '{svc_sql}' -Q 'select @@version'", "history | grep ssh"],
    "Engineering": ["git clone http://gitea:3000/engineering/billing-api.git", "cd billing-api",
                    "git checkout -b feature/invoice-export", "python3 -m venv .venv", "source .venv/bin/activate",
                    "pip install -r requirements.txt", "pytest -q", "git commit -am 'wip'", "git push -u origin HEAD",
                    "docker ps", "curl -s http://prod:8080/health"],
    "default": ["ls", "cd Documents", "ls -la", "smbclient //fs01.{realm}/public -k -c ls",
                "libreoffice --calc Budget.csv", "cd ..", "df -h"],
}
USER_AGENTS = [
    "Mozilla/5.0 (X11; Ubuntu; Linux aarch64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
]
REPOS_BY_DEPT = {"Engineering": ["billing-api", "intranet"], "IT": ["infra-ansible", "helpdesk-scripts", "intranet"]}
SSH_TARGETS = ["dc01", "fs01", "siem", "sensor", "gitea", "prod", "mgmt", "velociraptor", "mail"]
SSH_CMDS = ["uptime", "df -h /", "free -m", "sudo journalctl -n 20 --no-pager", "systemctl --failed --no-pager",
            "last -n 5", "sudo tail -n 20 /var/log/syslog", "docker ps 2>/dev/null || true", "who"]
TIMER_DIRS = "/etc"


CALDERA_BLUE = ("blue", "admin")      # Caldera's --insecure defaults (see caldera.yaml.tmpl)
STATUS_DOWNLOADS = ["VPN-Client-Setup-Guide.pdf", "Network-Map.pdf", "Printer-Drivers.zip", "Wallpaper-2026.png"]


def service_accounts(ros):
    """Accounts for the legacy FTP drop and telnet console on sensor and for
    Uptime Kuma (stored in the roster; derived from the seed when missing)."""
    rng = random.Random(f"{ros['seed']}-services")
    derived = {"ftp_user": "scanner", "ftp_pass": make_password(rng),
               "telnet_user": "netops", "telnet_pass": make_password(rng),
               "kuma_user": "admin", "kuma_pass": make_password(rng)}
    return {**derived, **ros.get("services", {})}       # rosters from older versions lack the newer ones


def busy():
    """How likely people are to be at work right now (LAB_ACTIVITY_BUSY=0..1 overrides)."""
    if os.environ.get("LAB_ACTIVITY_BUSY"):
        return max(0.05, min(1.0, float(os.environ["LAB_ACTIVITY_BUSY"])))
    now = dt.datetime.now()
    if now.weekday() < 5 and 8 <= now.hour < 17:
        return 1.0
    if now.weekday() < 5 and (7 <= now.hour < 8 or 17 <= now.hour < 20):
        return 0.5
    return 0.2


def install_timer(name, desc, config, every_min=None, spread_min=5, user="root", calendar=None):
    """Install /usr/local/sbin/<name> (this file) and a timer that runs a profile."""
    etc = f"{TIMER_DIRS}/{name}"
    os.makedirs(etc, mode=0o700, exist_ok=True)
    path = f"{etc}/config.json"
    with open(path, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)
        json.dump(config, fh)
    if user != "root":
        shutil.chown(etc, user)
        shutil.chown(path, user)
    shutil.copy(__file__, f"/usr/local/sbin/{name}")
    os.chmod(f"/usr/local/sbin/{name}", 0o755)
    when = (f"OnCalendar={calendar}\nPersistent=true\n" if calendar
            else f"OnBootSec={spread_min + 2}min\nOnUnitActiveSec={every_min}min\n")
    with open(f"/etc/systemd/system/{name}.service", "w") as fh:
        fh.write(f"[Unit]\nDescription={desc}\nAfter=network-online.target\n\n"
                 f"[Service]\nType=oneshot\nUser={user}\nTimeoutStartSec=30min\n"
                 f"ExecStart=/usr/bin/python3 /usr/local/sbin/{name} run {path}\n")
    with open(f"/etc/systemd/system/{name}.timer", "w") as fh:
        fh.write(f"[Unit]\nDescription={desc} (timer)\n\n[Timer]\n{when}"
                 f"RandomizedDelaySec={spread_min}min\n\n[Install]\nWantedBy=timers.target\n")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", f"{name}.timer"])


def install_loop(name, desc, config, user="root"):
    """Install /usr/local/sbin/<name> (this file) as a service that never stops:
    it runs its profile, sleeps a random while, and goes again (systemd
    restarts it if it ever dies)."""
    etc = f"{TIMER_DIRS}/{name}"
    os.makedirs(etc, mode=0o700, exist_ok=True)
    path = f"{etc}/config.json"
    with open(path, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)
        json.dump(config, fh)
    if user != "root":
        shutil.chown(etc, user)
        shutil.chown(path, user)
    shutil.copy(__file__, f"/usr/local/sbin/{name}")
    os.chmod(f"/usr/local/sbin/{name}", 0o755)
    with open(f"/etc/systemd/system/{name}.service", "w") as fh:
        fh.write(f"[Unit]\nDescription={desc}\nAfter=network-online.target\nWants=network-online.target\n\n"
                 f"[Service]\nUser={user}\nExecStart=/usr/bin/python3 /usr/local/sbin/{name} loop {path}\n"
                 "Restart=always\nRestartSec=30\nNice=10\n\n[Install]\nWantedBy=multi-user.target\n")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", f"{name}.service"])
    run(["systemctl", "restart", f"{name}.service"])


# profile -> (function, min gap, max gap) in seconds; staff gaps stretch at night
LOOP_GAPS = {"staff": (30, 240), "ansible": (900, 2700), "semaphore": (1800, 5400), "velociraptor": (3600, 14400),
             "mailflow": (300, 1500)}


def loop(path):
    cfg = json.load(open(path))
    prof = cfg["profile"]
    logf = cfg.get("log", f"/var/log/lab-{prof}.log")
    rng = random.Random()
    lo, hi = LOOP_GAPS[prof]
    while True:
        try:
            events = PROFILES[prof](cfg, rng)
        except Exception as e:                                             # noqa: BLE001 -- never stop
            events = [f"error: {e}"]
        try:
            with open(logf, "a") as fh:
                for e in events:
                    fh.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {e}\n")
        except OSError:
            pass
        gap = rng.uniform(lo, hi) / (busy() if prof in ("staff", "mailflow") else 1)
        time.sleep(gap)


def activity_key(comment):
    """SSH key the deploy script authorizes on the servers (ubuntu@)."""
    key = f"{TIMER_DIRS}/lab-activity-key/id_ed25519"
    os.makedirs(os.path.dirname(key), mode=0o755, exist_ok=True)
    if not os.path.exists(key):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", key])
    return key


def apt(*pkgs):
    missing = [p for p in pkgs if run(["dpkg", "-s", p], check=False).returncode]
    if missing:
        # unattended-upgrades often holds the dpkg lock on a fresh VM: wait for it
        run(["apt-get", "-o", "DPkg::Lock::Timeout=600", "install", "-y", *missing], timeout=1500,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})


def role_client(cfg, ros, client):
    rng = random.Random(f"{ros['seed']}-{client}")
    realm = cfg["realm"].lower()
    apt("smbclient", "git", "dnsutils")
    people = [u for u in ros["users"] if u["client"] == client]
    for u in people:
        try:
            pw = pwd.getpwnam(f"{u['sam']}@{realm}")
        except KeyError:
            log(f"WARN: {u['sam']} not known to sssd yet -- skipped")
            continue
        home = pw.pw_dir
        if not os.path.isdir(home):
            shutil.copytree("/etc/skel", home, symlinks=True)
        w = Writer(home, rng, default_owner=pw.pw_name)
        w.put("Documents/todo.md", "\n".join([
            "# TODO", f"- [ ] {rng.choice(['Finish', 'Review', 'Send'])} {rng.choice(['report', 'slides', 'budget'])} for {rng.choice(PROJECTS)}",
            "- [x] Change password (IT reminder)", f"- [ ] 1:1 with {u['manager'] or 'the board'}"]), recent_time(rng, 3))
        for _ in range(rng.randint(3, 6)):
            when = recent_time(rng, 60)
            w.put(f"Documents/{rng.choice(['notes', 'draft', 'report', 'minutes'])}-{when:%Y-%m-%d}.docx",
                  docx([f"# {u['dept']} notes {when:%Y-%m-%d}", "Personal working notes."]), when)
        for name in [f"invoice-{rng.randint(1000, 9999)}.pdf", f"{rng.choice(WORDS).lower()}-setup.deb",
                     f"Screenshot {recent_time(rng, 20):%Y-%m-%d at %H.%M.%S}.png"]:
            w.put(f"Downloads/{name}", os.urandom(rng.randint(2000, 40000)), recent_time(rng, 30))
        hist = HISTORY.get(u["dept"], HISTORY["default"]) + HISTORY["default"]
        w.put(".bash_history", "\n".join(
            rng.choice(hist).format(sam=u["sam"], realm=realm, svc_sql=cfg["svc_sql_pass"])
            for _ in range(rng.randint(30, 80))) + "\n", recent_time(rng, 1), mode=0o600)
        for d in ("Desktop", "Pictures"):
            os.makedirs(os.path.join(home, d), exist_ok=True)
        settle_dirs(home)
        run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", home])
        os.chmod(home, 0o750)
    apt("expect", "telnet", "ftp")
    activity_key(f"lab-activity@{client}")
    for old in ("lab-activity.timer",):                       # earlier versions used a timer
        run(["systemctl", "disable", "--now", old], check=False)
    install_loop("lab-activity", "SOC-lab simulated staff on this workstation (never stops)", {
        "profile": "staff", "log": "/var/log/lab-activity.log", "realm": cfg["realm"], "ips": cfg["ips"],
        "proxy": f"http://{cfg['ips']['sensor']}:3128" if cfg["ips"].get("sensor") else "",
        # who else works here (for writing mail): names only, no passwords
        "directory": [{k: p[k] for k in ("sam", "given", "sn", "dept")} for p in ros["users"] if p["enabled"]],
        "dev_pass": cfg["seed_pass"], "services": service_accounts(ros),
        "users": [{k: u[k] for k in ("sam", "password", "dept", "title", "given", "sn")} for u in people]})
    log(f"homes for {', '.join(u['sam'] for u in people) or 'nobody'}; lab-activity service (staff loop) running")


def smb_files(listing):
    r"""File paths (\Dir\name) from `smbclient -c 'recurse; ls'` output."""
    import re
    files, cwd = [], ""
    for ln in listing.splitlines():
        if ln.startswith("\\"):
            cwd = ln.strip()
            continue
        m = re.match(r"^  (.+?)\s+([ADHNRS]+)\s+\d+\s+\w{3} \w{3} ", ln)
        if m and "D" not in m.group(2) and not m.group(1).startswith("."):
            files.append(f"{cwd}\\{m.group(1)}")
    # Anyone can write to \\fs01\public, and these paths go into an smbclient
    # -c command line (where ; separates commands and ! runs a local shell).
    # Only keep names made of plain characters.
    return [f for f in files if SAFE_SMB_PATH.fullmatch(f)]


SAFE_SMB_PATH = __import__("re").compile(r"[\w\\ .,()+-]+")


class Web:
    """A tiny browser: cookies, one user agent, never raises."""

    def __init__(self, ua, proxy=None):
        import http.cookiejar
        import ssl
        self.ua = ua
        # Through the office proxy on sensor when there is one (HTTPS as CONNECT);
        # never through whatever *_proxy happens to be in the environment.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))

    def get(self, url, data=None, headers=None, timeout=10):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        elif isinstance(data, str):
            data = data.encode()
            headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        req = urllib.request.Request(url, data=data, headers={"User-Agent": self.ua, **(headers or {})})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:                                    # noqa: BLE001 -- traffic must never crash
            return 0, ""


class KumaIO:
    """Just enough of Uptime Kuma's socket.io API (Engine.IO v4 over HTTP
    long-polling, so no websocket library is needed)."""

    def __init__(self, base, timeout=30):
        self.url = base.rstrip("/") + "/socket.io/?EIO=4&transport=polling"
        self.timeout, self.ack, self.events = timeout, 0, {}
        self.url += "&sid=" + json.loads(self._get(self.url)[1:])["sid"]
        self._post("40")
        self._read(lambda: "connected" in self.events)

    def _get(self, url):
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return r.read().decode(errors="replace")

    def _post(self, body):
        req = urllib.request.Request(self.url, data=body.encode(), headers={"Content-Type": "text/plain;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            r.read()

    def _read(self, done):
        deadline = time.time() + self.timeout
        while not done():
            if time.time() > deadline:
                raise TimeoutError("no answer from Uptime Kuma")
            for pkt in self._get(self.url).split("\x1e"):
                if pkt == "2":                                  # engine.io ping
                    self._post("3")
                elif pkt.startswith("40"):
                    self.events["connected"] = True
                elif pkt.startswith("42"):                      # event: 42["name", data...]
                    msg = json.loads(pkt[2:])
                    self.events[msg[0]] = msg[1:]
                elif pkt.startswith("43"):                      # ack: 43<id>[result]
                    i = pkt.index("[")
                    self.events[f"ack{pkt[2:i]}"] = json.loads(pkt[i:])

    def call(self, event, *args):
        """Emit an event and wait for its acknowledgement (the server's reply)."""
        self.ack += 1
        n = self.ack
        self._post(f"42{n}" + json.dumps([event, *args]))
        self._read(lambda: f"ack{n}" in self.events)
        reply = self.events.pop(f"ack{n}")
        return reply[0] if reply else {}                        # e.g. logout acks with no data

    def wait(self, event):
        self._read(lambda: event in self.events)
        return self.events[event]


def token_in(page, name):
    import re
    m = re.search(rf"name=['\"]{name}['\"] value=['\"]([^'\"]+)['\"]", page)
    return m.group(1) if m else ""


class Staff:
    """One person on a workstation doing a few minutes of normal work."""

    def __init__(self, cfg, u, rng):
        self.cfg, self.u, self.rng = cfg, u, rng
        self.ip = cfg["ips"].get
        self.realm = cfg["realm"]
        self.web = Web(USER_AGENTS[sum(map(ord, u["sam"])) % len(USER_AGENTS)], cfg.get("proxy"))
        self.env = {**os.environ, "KRB5CCNAME": f"FILE:/tmp/lab-activity-{u['sam']}.cc"}
        self.events = []

    def ev(self, msg):
        self.events.append(f"{self.u['sam']}: {msg}")

    def logon(self):
        principal = f"{self.u['sam']}@{self.realm}"
        if self.rng.random() < 0.05:                                       # a typo now and then
            run(["kinit", principal], check=False, env=self.env, input=self.u["password"][:-1] + "x\n")
            self.ev("mistyped the password")
        ok = run(["kinit", principal], check=False, env=self.env, input=self.u["password"] + "\n").returncode == 0
        self.ev("logged on (Kerberos)" if ok else "logon FAILED")
        return ok

    def files(self):
        fs = f"//fs01.{self.realm.lower()}"
        smb = ["smbclient", f"{fs}/public", "--use-kerberos=required", "-N", "-c"]
        files = smb_files(run(smb + ["recurse; ls"], check=False, env=self.env).stdout)
        for path in self.rng.sample(files, min(len(files), self.rng.randint(1, 4))):
            run(smb + [f'get "{path}" /tmp/lab-activity.tmp'], check=False, env=self.env)
            self.ev(f"opened \\\\fs01\\public{path}")
        if self.rng.random() < 0.3:
            note = f"/tmp/lab-activity-{self.u['sam']}.txt"
            with open(note, "a") as fh:
                fh.write(f"{dt.datetime.now():%H:%M} {self.rng.choice(['call with customer', 'review', 'standup', 'fixing a bug', 'budget question'])}\n")
            run(smb + [f'mkdir "Scratch"; mkdir "Scratch\\{self.u["sam"]}"; '
                       f'put {note} "Scratch\\{self.u["sam"]}\\notes-{dt.date.today()}.txt"'], check=False, env=self.env)
            self.ev("saved notes to \\\\fs01\\public\\Scratch")
        if self.u["dept"] == "Finance":
            out = run(["smbclient", f"{fs}/finance", "--use-kerberos=required", "-N", "-c", "recurse; ls"],
                      check=False, env=self.env).stdout
            inv = smb_files(out)
            if inv:
                f = self.rng.choice(inv)
                run(["smbclient", f"{fs}/finance", "--use-kerberos=required", "-N", "-c",
                     f'get "{f}" /tmp/lab-activity.tmp'], check=False, env=self.env)
                self.ev(f"opened \\\\fs01\\finance{f}")
        elif self.rng.random() < 0.04:                                     # curiosity: access denied
            run(["smbclient", f"{fs}/finance", "--use-kerberos=required", "-N", "-c", "ls"], check=False, env=self.env)
            self.ev("tried \\\\fs01\\finance (denied)")

    def dns(self):
        names = [f"fs01.{self.realm.lower()}", f"_ldap._tcp.{self.realm.lower()}", "gitea", "prod",
                 "www.wikipedia.org", "outlook.office.com", "github.com", "www.svt.se", "ubuntu.com"]
        for n in self.rng.sample(names, 3):
            run(["dig", "+short", "+time=2", "SRV" if n.startswith("_") else "A", n], check=False)
        self.ev("DNS lookups")

    def browse(self):
        """Visit two or three apps people in this department would use."""
        ip, w, r = self.ip, self.web, self.rng
        visits = [self.demo_app, self.status_page, self.gitea_browse, self.juice_shop, self.intranet,
                  self.downloads, self.downloads, self.ftp]
        if self.u["dept"] in ("Finance", "Operations", "HR"):
            visits += [self.ftp]
        if self.u["dept"] in ("Engineering", "IT"):
            visits += [self.gitea_dev, self.gitea_dev, self.ssh_admin]
        if self.u["dept"] == "IT":
            visits += [self.admin_consoles, self.ssh_admin, self.lam, self.lam, self.telnet, self.telnet,
                       self.kuma, self.kuma, self.caldera]
        if self.u["title"] in ("QA Engineer", "Software Engineer", "Senior Software Engineer") or self.u["dept"] == "IT":
            visits += [self.dvwa]
        for v in r.sample(visits, min(len(visits), r.randint(2, 4))):
            try:
                v()
            except Exception as e:                                         # noqa: BLE001
                self.ev(f"{v.__name__} error: {e}")
        if self.u["dept"] == "Engineering" and r.random() < 0.03:
            self.demo_push()
        del ip, w

    def demo_app(self):
        base = f"http://{self.ip('prod')}:8080"
        self.web.get(base + "/")
        self.web.get(base + "/health")
        self.ev("used demo-app on prod")

    def status_page(self):
        base = f"http://{self.ip('sensor')}"
        for p in self.rng.sample(["/", "/", "/services.html", "/changes.html"], 2):
            self.web.get(base + p)
        self.ev("checked the status page on sensor")

    def intranet(self):
        self.web.get(f"http://{self.ip('gitea')}:3000/engineering/intranet")
        self.ev("read the intranet repo page")

    def gitea_browse(self):
        base = f"http://{self.ip('gitea')}:3000"
        repo = self.rng.choice(list(REPOS))
        for p in ["/explore/repos", f"/engineering/{repo}", f"/engineering/{repo}/issues",
                  f"/engineering/{repo}/commits/branch/main"][:self.rng.randint(2, 4)]:
            self.web.get(base + p)
        self.ev(f"browsed engineering/{repo} on gitea")

    def gitea_dev(self):
        """Developers: API login, clone/pull, sometimes push a small change."""
        sam, pw = self.u["sam"], self.u["password"]
        base = f"http://{self.ip('gitea')}:3000"
        auth = base64.b64encode(f"{sam}:{pw}".encode()).decode()
        self.web.get(base + "/api/v1/user", headers={"Authorization": "Basic " + auth})
        repo = self.rng.choice(REPOS_BY_DEPT.get(self.u["dept"], ["intranet"]))
        work = f"/var/lib/lab-activity/{sam}/{repo}"
        hdr = ["-c", f"http.extraHeader=Authorization: Basic {auth}"]
        if os.path.isdir(work):
            run(["git", "-C", work, *hdr, "pull", "-q", "--rebase"], check=False)
            self.ev(f"git pull engineering/{repo}")
        else:
            os.makedirs(os.path.dirname(work), exist_ok=True)
            run(["git", *hdr, "clone", "-q", f"{base}/engineering/{repo}.git", work], check=False)
            self.ev(f"git clone engineering/{repo}")
        if os.path.isdir(work) and self.rng.random() < 0.15:
            msg = self.rng.choice(COMMIT_MSGS)
            with open(os.path.join(work, "CHANGELOG.md"), "a") as fh:
                fh.write(f"- {dt.date.today()}: {msg}\n")
            who = ["-c", f"user.name={self.u['given']} {self.u['sn']}",
                   "-c", f"user.email={sam}@{self.realm.lower()}"]
            run(["git", "-C", work, *who, "commit", "-qam", msg], check=False)
            if run(["git", "-C", work, *hdr, "push", "-q"], check=False).returncode:
                run(["git", "-C", work, "reset", "-q", "--hard", "@{u}"], check=False)
                self.ev(f"push to engineering/{repo} rejected")
            else:
                self.ev(f"pushed '{msg}' to engineering/{repo}")

    def demo_push(self):
        """Ship a tiny change through the whole CI/CD pipeline (as the dev account)."""
        auth = base64.b64encode(f"dev:{self.cfg['dev_pass']}".encode()).decode()
        hdr = ["-c", f"http.extraHeader=Authorization: Basic {auth}"]
        work = tempfile.mkdtemp()
        try:
            run(["git", *hdr, "clone", "-q", f"http://{self.ip('gitea')}:3000/labadmin/demo-app.git", work])
            with open(os.path.join(work, "README.md"), "a") as fh:
                fh.write(f"\n- {dt.datetime.now():%Y-%m-%d %H:%M}: {self.rng.choice(COMMIT_MSGS)} ({self.u['given']})\n")
            run(["git", "-C", work, "-c", f"user.name={self.u['given']} {self.u['sn']}", "-c",
                 f"user.email={self.u['sam']}@{self.realm.lower()}", "commit", "-qam", "Update README"])
            run(["git", "-C", work, *hdr, "push", "-q"])
            self.ev("pushed to labadmin/demo-app -> CI/CD pipeline run")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def juice_shop(self):
        """Shopping on a break: search, log in (registering the first time), view the basket."""
        base = f"http://{self.ip('vulntarget')}:3000"
        w = self.web
        w.get(base + "/")
        for q in self.rng.sample(["apple", "juice", "banana", "lemon", "t-shirt", "mug", "sticker", "melon"], 2):
            w.get(f"{base}/rest/products/search?q={q}")
        email, pw = f"{self.u['sam']}@kestrel-mail.example", self.u["password"]
        st, body = w.get(base + "/rest/user/login", {"email": email, "password": pw})
        if st != 200:
            w.get(base + "/api/Users", {"email": email, "password": pw, "passwordRepeat": pw,
                                        "securityQuestion": {"id": 2}, "securityAnswer": self.u["sn"]})
            st, body = w.get(base + "/rest/user/login", {"email": email, "password": pw})
        if st == 200:
            auth = json.loads(body)["authentication"]
            w.get(f"{base}/rest/basket/{auth['bid']}", headers={"Authorization": f"Bearer {auth['token']}"})
            self.ev("shopped on Juice Shop (logged in)")
        else:
            self.ev("browsed Juice Shop")

    def dvwa(self):
        base = f"http://{self.ip('vulntarget')}"
        w = self.web
        _, page = w.get(base + "/login.php")
        w.get(base + "/login.php", f"username=admin&password=password&Login=Login&user_token={token_in(page, 'user_token')}")
        for p in self.rng.sample(["/vulnerabilities/sqli/?id=1&Submit=Submit", "/vulnerabilities/xss_r/?name=test",
                                  "/vulnerabilities/exec/", "/instructions.php", "/security.php", "/about.php"], 3):
            w.get(base + p)
        self.ev("tested DVWA pages (QA)")

    def admin_consoles(self):
        """IT staff keep the admin consoles open (they log in themselves; we only load them)."""
        consoles = [("Wazuh", f"https://{self.ip('siem')}/"), ("Wazuh API", f"https://{self.ip('siem')}:55000/"),
                    ("Velociraptor", f"https://{self.ip('velociraptor')}:8889/"),
                    ("Semaphore", f"http://{self.ip('mgmt')}:3000/"), ("Uptime Kuma", f"http://{self.ip('mgmt')}:3001/"),
                    ("Caldera", f"http://{self.ip('caldera')}:8888/")]
        for name, url in self.rng.sample(consoles, 2):
            if "None" not in url:
                self.web.get(url)
                self.ev(f"opened {name}")

    def password_attempt(self):
        """The real password, or (now and then) a typo of it."""
        if self.rng.random() < 0.04:
            return self.u["password"][:-1] + "x", False
        return self.u["password"], True

    def lam(self):
        """IT looks people up in LDAP Account Manager with their own domain account."""
        import html
        import re
        base = f"http://{self.ip('lam')}/lam/templates"
        _, page = self.web.get(base + "/login.php")
        me = f"{self.u['given']} {self.u['sn']}"
        dn = next((html.unescape(v) for v, text in re.findall(r'<option value="([^"]+)"\s*[^>]*>([^<]*)', page)
                   if text.strip() == me), None)
        if not dn:
            self.ev("opened LDAP Account Manager (not set up for this account)")
            return
        pw, real = self.password_attempt()
        _, page = self.web.get(base + "/login.php", urllib.parse.urlencode(
            {"username": dn, "passwd": pw, "language": "en_US.utf8", "checklogin": "Login"}))
        if not real:
            self.ev("LDAP Account Manager: wrong password")
            return
        _, page = self.web.get(f"{base}/lists/list.php?type=user")
        if "Administrator" not in page:
            self.ev("LDAP Account Manager: login failed")
            return
        self.web.get(f"{base}/lists/list.php?type={self.rng.choice(['group', 'host'])}")
        self.ev("looked up accounts in LDAP Account Manager")

    def kuma(self):
        """IT glances at Uptime Kuma (shared admin login, like most small offices)."""
        acct = self.cfg["services"]
        if not acct.get("kuma_pass"):
            return
        io_ = KumaIO(f"http://{self.ip('mgmt')}:3001")
        typo = self.rng.random() < 0.04
        res = io_.call("login", {"username": acct["kuma_user"], "token": "",
                                 "password": acct["kuma_pass"] + ("x" if typo else "")})
        if not res.get("ok"):
            self.ev("Uptime Kuma: login failed")
            return
        monitors = io_.wait("monitorList")[0]
        down = [m["name"] for m in monitors.values() if not m.get("active", True)]
        io_.call("logout")
        self.ev(f"checked Uptime Kuma ({len(monitors)} monitors{', paused: ' + ', '.join(down) if down else ''})")

    def caldera(self):
        """The blue team checks Caldera for agents and operations (read-only)."""
        base = f"http://{self.ip('caldera')}:8888"
        typo = self.rng.random() < 0.04
        self.web.get(base + "/enter", urllib.parse.urlencode(
            {"username": CALDERA_BLUE[0], "password": CALDERA_BLUE[1] + ("x" if typo else "")}))
        st, _ = self.web.get(base + "/api/v2/agents")
        if st != 200:
            self.ev("Caldera: login failed")
            return
        for p in self.rng.sample(["/api/v2/operations", "/api/v2/adversaries", "/api/v2/abilities"], 2):
            self.web.get(base + p, timeout=30)
        self.ev("checked Caldera agents and operations (blue)")

    def ssh_admin(self):
        """Admin work over SSH with the workstation's key."""
        target = self.rng.choice([t for t in SSH_TARGETS if self.ip(t)])
        cmd = self.rng.choice(SSH_CMDS)
        res = run(["ssh", "-i", f"{TIMER_DIRS}/lab-activity-key/id_ed25519", "-o", "BatchMode=yes",
                   "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=5",
                   "-o", f"UserKnownHostsFile={TIMER_DIRS}/lab-activity-key/known_hosts",
                   f"ubuntu@{self.ip(target)}", cmd], check=False, timeout=60)
        self.ev(f"ssh {target} '{cmd}'" + ("" if res.returncode == 0 else f" (exit {res.returncode})"))

    def ftp(self):
        """Fetch a scan from the old scan-to-FTP drop (shared account, like many offices)."""
        import ftplib
        acct = self.cfg["services"]
        with ftplib.FTP(self.ip("sensor"), timeout=15) as f:
            f.login(acct["ftp_user"], acct["ftp_pass"])
            names = [n for n in f.nlst("scans") if n.endswith((".pdf", ".txt"))]
            for n in self.rng.sample(names, min(len(names), self.rng.randint(1, 3))):
                with open("/tmp/lab-activity.tmp", "wb") as out:
                    f.retrbinary(f"RETR {n}", out.write)
                self.ev(f"ftp get {n}")
            if self.rng.random() < 0.25:
                data = io.BytesIO(f"Scanned by {self.u['given']} {dt.datetime.now():%Y-%m-%d %H:%M}\n".encode())
                f.storbinary(f"STOR scans/scan_{dt.datetime.now():%Y%m%d_%H%M%S}.txt", data)
                self.ev("ftp put a new scan")

    def telnet(self):
        """IT checks the legacy console over telnet (cleartext, on purpose)."""
        acct = self.cfg["services"]
        cmd = self.rng.choice(["uptime", "show-ports", "df -h", "who"])
        script = (f'set timeout 15\nspawn telnet {self.ip("sensor")}\n'
                  f'expect "login:"\nsend "{acct["telnet_user"]}\\r"\n'
                  f'expect "Password:"\nsend "{acct["telnet_pass"]}\\r"\n'
                  f'expect "$ "\nsend "{cmd}\\r"\nexpect "$ "\nsend "exit\\r"\nexpect eof\n')
        res = run(["expect", "-c", script], check=False, timeout=60)
        self.ev(f"telnet to the legacy console, ran '{cmd}'" + ("" if res.returncode == 0 else " (failed)"))

    def downloads(self):
        """Download documents and tools: status page files and raw files from gitea."""
        picks = [f"http://{self.ip('sensor')}/downloads/{n}" for n in STATUS_DOWNLOADS]
        picks += [f"http://{self.ip('gitea')}:3000/engineering/{r}/raw/branch/main/README.md" for r in REPOS]
        for url in self.rng.sample(picks, 2):
            self.web.get(url, timeout=20)
            self.ev(f"downloaded {url.rsplit('/', 1)[-1]} from {url.split('/')[2]}")

    def mail(self):
        """Check mail (IMAP, or webmail now and then); sometimes write one."""
        if not self.ip("mail"):
            return
        if self.rng.random() < 0.2:
            self.webmail()
        else:
            self.imap()
        if self.rng.random() < 0.35:
            self.send_mail()

    def imap(self):
        import imaplib
        pw, real = self.password_attempt()
        with imaplib.IMAP4(self.ip("mail"), timeout=20) as im:
            try:
                im.login(self.u["sam"], pw)
            except imaplib.IMAP4.error:
                self.ev("mail: IMAP login failed" + ("" if real else " (typo)"))
                return
            im.select("INBOX")
            _, unseen = im.search(None, "UNSEEN")
            ids = unseen[0].split()
            for i in ids[:5]:
                im.fetch(i, "(BODY[])")                          # reading marks it seen
            if ids and self.rng.random() < 0.2:                  # tidy up: delete a newsletter
                _, news = im.search(None, "FROM", "news")
                if news[0]:
                    im.store(news[0].split()[0], "+FLAGS", "\\Deleted")
                    im.expunge()
            im.logout()
        self.ev(f"read mail (IMAP): {min(len(ids), 5)} new of {len(ids)}")

    def webmail(self):
        import re
        base = f"http://{self.ip('mail')}/roundcube/"
        _, page = self.web.get(base)
        tok = re.search(r'name="_token" value="([^"]+)"', page)
        pw, real = self.password_attempt()
        _, page = self.web.get(base + "?_task=login", urllib.parse.urlencode({
            "_token": tok.group(1) if tok else "", "_task": "login", "_action": "login",
            "_timezone": "Europe/Stockholm", "_url": "", "_user": self.u["sam"], "_pass": pw}))
        if "_task=logout" not in page:
            self.ev("webmail: login failed" + ("" if real else " (typo)"))
            return
        self.web.get(base + "?_task=mail&_mbox=INBOX")
        tok = re.search(r'"request_token":"([^"]+)"', page)
        self.web.get(base + f"?_task=logout&_token={tok.group(1) if tok else ''}")
        self.ev("read mail in webmail")

    def send_mail(self):
        import smtplib
        realm = self.realm.lower()
        people = [p for p in self.cfg.get("directory", []) if p["sam"] != self.u["sam"]]
        if not people:
            return
        peer = self.rng.choice([p for p in people if p["dept"] == self.u["dept"]] or people)
        subject = self.rng.choice(INTERNAL_SUBJECTS).format(project=self.rng.choice(PROJECTS), q=quarter(TODAY),
                                                            customer=self.rng.choice(customers(self.rng, 6)))
        msg = make_mail(f"{self.u['sam']}@{realm}", f"{self.u['given']} {self.u['sn']}",
                        f"{peer['given']} {peer['sn']} <{peer['sam']}@{realm}>", subject,
                        mail_body(self.rng, peer["given"], subject) + f"\n{self.u['given']}", dt.datetime.now())
        with smtplib.SMTP(self.ip("mail"), 587, timeout=20) as smtp:
            smtp.login(self.u["sam"], self.u["password"])       # submission, cleartext AUTH (legacy setup)
            smtp.send_message(msg)
        self.ev(f"sent mail to {peer['sam']}: {subject}")

    def logoff(self):
        run(["kdestroy"], check=False, env=self.env)


def profile_staff(cfg, rng):
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    if rng.random() > busy() or not cfg["users"]:
        return ["(quiet: nobody at their desk)"]
    events = []
    for u in rng.sample(cfg["users"], min(len(cfg["users"]), rng.choice([1, 1, 2, 3]))):
        s = Staff(cfg, u, rng)
        if s.logon():
            for step in rng.sample([s.files, s.browse, s.dns, s.mail], 4):
                try:
                    step()
                except Exception as e:                                     # noqa: BLE001
                    s.ev(f"{step.__name__} error: {e}")
            s.logoff()
        events += s.events
    if os.path.exists("/tmp/lab-activity.tmp"):
        os.unlink("/tmp/lab-activity.tmp")
    return events


def profile_ansible(cfg, rng):
    """ansible-ctrl: routine checks over SSH against the whole lab."""
    proj = "/home/ubuntu/soc-lab-ansible"
    if not os.path.exists(f"{proj}/inventory.ini"):
        return ["no inventory yet"]
    book = rng.choices(["ping.yml", "disk-report.yml", "audit-local-users.yml"], [3, 2, 1])[0]
    res = run(["ansible-playbook", "-l", "all:!honeypot:!redteam", f"playbooks/{book}"], check=False,
              cwd=proj, timeout=900, env={**os.environ, "ANSIBLE_NOCOLOR": "1"})
    recap = [ln for ln in res.stdout.splitlines() if "ok=" in ln]
    return [f"ansible-playbook {book}: {len(recap)} hosts, exit {res.returncode}"]


def profile_semaphore(cfg, rng):
    """mgmt: Semaphore runs one of its task templates, like a scheduled job."""
    w = Web("semaphore-scheduler")
    w.get("http://127.0.0.1:3000/api/auth/login", {"auth": "admin", "password": cfg["admin_pass"]})
    _, projects = w.get("http://127.0.0.1:3000/api/projects")
    pid = next((p["id"] for p in json.loads(projects or "[]") if p["name"] == "Lab operations"), None)
    if not pid:
        return ["no Semaphore project"]
    _, templates = w.get(f"http://127.0.0.1:3000/api/project/{pid}/templates")
    tpl = [t for t in json.loads(templates or "[]") if "patch" not in t["playbook"]]
    t = rng.choice(tpl)
    st, _ = w.get(f"http://127.0.0.1:3000/api/project/{pid}/tasks", {"template_id": t["id"]})
    return [f"Semaphore task '{t['name']}' started ({st})"]


def profile_backup(cfg, rng):
    """fs01: the nightly backup job from \\\\fs01\\public\\IT\\scripts (uses svc_legacy)."""
    work = tempfile.mkdtemp()
    try:
        res = run(["bash", "/srv/shares/public/IT/scripts/backup-job.sh"], check=False, cwd=work, timeout=600)
        return [f"backup-job.sh exit {res.returncode}: {(res.stdout + res.stderr).strip().splitlines()[-1:]}"]
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# gitea: an engineering org with real-looking history, issues, a leaked secret
# --------------------------------------------------------------------------
class Api:
    def __init__(self, base, user, password):
        self.base = base
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def __call__(self, method, path, body=None, sudo=None, ok=(200, 201, 204)):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", self.auth)
        req.add_header("Content-Type", "application/json")
        if sudo:
            req.add_header("Sudo", sudo)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                return r.status, json.loads(data) if data else None
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return e.code, None
            return e.code, e.read().decode(errors="replace")


REPOS = {
    "billing-api": ("Invoice and payment API used by Finance", "Engineering"),
    "intranet": ("Company intranet (static site)", "Marketing"),
    "infra-ansible": ("Ansible playbooks for the lab servers", "IT"),
    "helpdesk-scripts": ("Small scripts the Service Desk uses", "IT"),
}
COMMIT_MSGS = ["Fix rounding in VAT calculation", "Add pagination to invoice list", "Bump dependencies",
               "Refactor config loading", "Add unit tests for export", "Fix typo in README",
               "Handle empty customer name", "Log request IDs", "Speed up the report query",
               "Update CI notes", "Add healthcheck endpoint", "Tidy imports", "Fix timezone bug in due dates",
               "Document the deploy steps", "Remove dead code", "Add retry on timeout"]
ISSUES = {
    "billing-api": ["Export to CSV is slow for large customers", "Login page shows a stack trace on bad input",
                    "Upgrade base image to Python 3.13", "Add audit logging for invoice changes",
                    "Due dates are off by one day around DST", "Rotate the database password",
                    "Flaky test: test_export_month_end", "Credit notes are missing VAT"],
    "intranet": ["Dark mode for the intranet", "Phone list link is broken", "Add the new Malmö office",
                 "Newsletter archive is not sorted by date", "Search does not find policies"],
    "infra-ansible": ["Playbook fails on hosts without python3-apt", "Patch playbook should skip dc01 in office hours",
                      "Add a playbook for certificate expiry", "Disk report: include /var", "Document how to run locally"],
    "helpdesk-scripts": ["unlock-user.sh should log who ran it", "Reset script uses the default password",
                         "Add a script to list locked accounts", "Document how to run locally"],
}


def git(work, *args, env=None):
    return run(["git", "-C", work, *args], env=env)


def role_gitea(cfg, ros):
    rng = random.Random(ros["seed"] + 2)
    api = Api("http://127.0.0.1:3000/api/v1", "labadmin", cfg["admin_pass"])
    realm = cfg["realm"].lower()
    devs = [u for u in ros["users"] if u["enabled"] and u["dept"] in ("Engineering", "IT")]
    for u in devs:
        if api("GET", f"/users/{u['sam']}")[0] != 200:
            st, body = api("POST", "/admin/users", {
                "username": u["sam"], "email": f"{u['sam']}@{realm}", "password": u["password"],
                "full_name": f"{u['given']} {u['sn']}", "must_change_password": False})
            if st not in (200, 201):
                log(f"WARN: gitea user {u['sam']}: {st} {body}")
    org = "engineering"
    if api("GET", f"/orgs/{org}")[0] != 200:
        api("POST", "/orgs", {"username": org, "full_name": f"{ros['company']} Engineering",
                              "visibility": "public"})
    made = 0
    for repo, (desc, team) in REPOS.items():
        st, info = api("GET", f"/repos/{org}/{repo}")
        if st == 200 and not info.get("empty"):
            continue
        if st != 200:
            api("POST", f"/orgs/{org}/repos", {"name": repo, "description": desc,
                                               "default_branch": "main", "private": False})
        authors = [u for u in devs if u["dept"] == team] or devs
        if team == "Marketing":
            authors = devs
        for u in authors:
            api("PUT", f"/repos/{org}/{repo}/collaborators/{u['sam']}", {"permission": "write"})
        push_history(cfg, org, repo, desc, authors, rng)
        for title in rng.sample(ISSUES[repo], rng.randint(3, min(6, len(ISSUES[repo])))):
            a = rng.choice(authors)
            st, issue = api("POST", f"/repos/{org}/{repo}/issues",
                            {"title": title, "body": f"Seen in {quarter(TODAY)}. Steps to reproduce in the thread."},
                            sudo=a["sam"])
            if st in (200, 201) and isinstance(issue, dict):
                if rng.random() < 0.6:
                    api("POST", f"/repos/{org}/{repo}/issues/{issue['number']}/comments",
                        {"body": rng.choice(["I can reproduce this.", "Looking into it.", "+1, hit this yesterday.",
                                             "Fixed in main, please verify."])}, sudo=rng.choice(authors)["sam"])
                if rng.random() < 0.35:
                    api("PATCH", f"/repos/{org}/{repo}/issues/{issue['number']}", {"state": "closed"})
        made += 1
    log(f"{len(devs)} developer accounts, org '{org}' with {made} new repos (history, issues)")


def repo_files(cfg, repo, rng):
    if repo == "billing-api":
        return {
            "README.md": "# billing-api\n\nInvoice and payment API.\n\n    pip install -r requirements.txt\n    flask run\n",
            "requirements.txt": "flask==3.1.3\nsqlalchemy==2.0.36\nrequests==2.32.3\n",
            "app/__init__.py": "from flask import Flask\n\napp = Flask(__name__)\n",
            "app/invoices.py": "VAT = 0.25\n\n\ndef total(net):\n    return round(net * (1 + VAT), 2)\n",
            "tests/test_invoices.py": "from app.invoices import total\n\n\ndef test_total():\n    assert total(100) == 125\n",
            "CHANGELOG.md": "# Changelog\n",
        }
    if repo == "intranet":
        return {"README.md": "# intranet\n\nStatic intranet site.\n",
                "index.html": "<!doctype html><title>Kestrel intranet</title><h1>Welcome</h1>\n",
                "news.html": "<!doctype html><title>News</title><h1>News</h1>\n", "CHANGELOG.md": "# Changelog\n"}
    if repo == "infra-ansible":
        files = {f"playbooks/{n}": body for n, body in PLAYBOOKS.items()}
        files.update({"README.md": "# infra-ansible\n\nPlaybooks for the lab servers. Run from ansible-ctrl.\n",
                      "CHANGELOG.md": "# Changelog\n"})
        return files
    return {"README.md": "# helpdesk-scripts\n\nScripts for the Service Desk.\n",
            "unlock-user.sh": "#!/bin/bash\n# Usage: unlock-user.sh <sam>\nsamba-tool user unlock \"$1\"\n",
            "reset-password.sh": "#!/bin/bash\n# Usage: reset-password.sh <sam>\n"
                                 f"samba-tool user setpassword \"$1\" --newpassword='Welcome{TODAY.year}!' --must-change-at-next-login\n",
            "CHANGELOG.md": "# Changelog\n"}


def push_history(cfg, org, repo, desc, authors, rng):
    work = tempfile.mkdtemp()
    try:
        git(work, "init", "-q", "-b", "main")
        files = repo_files(cfg, repo, rng)
        n = rng.randint(14, 30)
        start = NOW - dt.timedelta(days=rng.randint(100, 160))
        stamps = sorted(start + (NOW - start) * rng.random() for _ in range(n))
        leak_at = rng.randint(3, n // 2) if repo == "billing-api" else -1
        for i, when in enumerate(stamps):
            a = rng.choice(authors)
            if i == 0:
                for path, body in files.items():
                    os.makedirs(os.path.dirname(os.path.join(work, path)) or work, exist_ok=True)
                    with open(os.path.join(work, path), "w") as fh:
                        fh.write(body)
                msg = "Initial import"
            elif i == leak_at:
                with open(os.path.join(work, "config.env"), "w") as fh:
                    fh.write(f"DB_HOST=SRV-SQL01\nDB_USER=svc_sql\nDB_PASSWORD={cfg['svc_sql_pass']}\n")
                msg = "Add database config for staging"
            elif i == leak_at + 1:
                os.unlink(os.path.join(work, "config.env"))
                with open(os.path.join(work, ".gitignore"), "a") as fh:
                    fh.write("config.env\n")
                msg = "Remove config.env from the repo (use env vars)"
            else:
                msg = rng.choice(COMMIT_MSGS)
                with open(os.path.join(work, "CHANGELOG.md"), "a") as fh:
                    fh.write(f"- {when:%Y-%m-%d}: {msg}\n")
            env = {**os.environ, "GIT_AUTHOR_NAME": f"{a['given']} {a['sn']}",
                   "GIT_AUTHOR_EMAIL": f"{a['sam']}@{cfg['realm'].lower()}",
                   "GIT_COMMITTER_NAME": f"{a['given']} {a['sn']}",
                   "GIT_COMMITTER_EMAIL": f"{a['sam']}@{cfg['realm'].lower()}",
                   "GIT_AUTHOR_DATE": when.isoformat(), "GIT_COMMITTER_DATE": when.isoformat()}
            git(work, "add", "-A", env=env)
            git(work, "commit", "-q", "-m", msg, env=env)
        auth = base64.b64encode(f"labadmin:{cfg['admin_pass']}".encode()).decode()
        git(work, "-c", f"http.extraHeader=Authorization: Basic {auth}", "push", "-q",
            f"http://127.0.0.1:3000/{org}/{repo}.git", "main")
    finally:
        shutil.rmtree(work)


# --------------------------------------------------------------------------
# ansible-ctrl: day-to-day playbooks (also the infra-ansible repo on gitea)
# --------------------------------------------------------------------------
PLAYBOOKS = {
    "ping.yml": "- name: Check every lab host answers\n  hosts: all\n  gather_facts: false\n  tasks:\n"
                "    - ansible.builtin.ping:\n",
    "patch.yml": "- name: Patch servers one at a time\n  hosts: all\n  become: true\n  serial: 1\n  tasks:\n"
                 "    - name: Upgrade packages\n      ansible.builtin.apt:\n        update_cache: true\n        upgrade: safe\n"
                 "    - name: Reboot if needed\n      ansible.builtin.reboot:\n      when: ansible_facts.pkg_mgr == 'apt'\n"
                 "      failed_when: false\n",
    "disk-report.yml": "- name: Disk usage report\n  hosts: all\n  gather_facts: true\n  tasks:\n"
                       "    - ansible.builtin.debug:\n        msg: \"{{ inventory_hostname }}: {{ (ansible_mounts | selectattr('mount', 'eq', '/') | first).size_available | human_readable }} free\"\n",
    "audit-local-users.yml": "- name: List local accounts with a login shell\n  hosts: all\n  become: true\n  tasks:\n"
                             "    - name: Accounts with a login shell\n"
                             "      ansible.builtin.shell: \"awk -F: '$7 ~ /sh$/ {print $1}' /etc/passwd\"\n"
                             "      register: users\n      changed_when: false\n"
                             "    - ansible.builtin.debug:\n        var: users.stdout_lines\n",
}


def role_ansible(cfg, ros):
    base = "/home/ubuntu/soc-lab-ansible/playbooks"
    os.makedirs(base, exist_ok=True)
    for name, body in PLAYBOOKS.items():
        path = os.path.join(base, name)
        if not os.path.exists(path):
            with open(path, "w") as fh:
                fh.write(body)
    run(["chown", "-R", "ubuntu:ubuntu", "/home/ubuntu/soc-lab-ansible"])
    os.makedirs("/var/log/lab", exist_ok=True)
    shutil.chown("/var/log/lab", "ubuntu")
    install_loop("lab-ansible", "SOC-lab routine Ansible checks against the lab (never stops)",
                 {"profile": "ansible", "log": "/var/log/lab/ansible.log"}, user="ubuntu")
    log(f"{len(PLAYBOOKS)} playbooks in ~/soc-lab-ansible/playbooks; lab-ansible loop running")


# --------------------------------------------------------------------------
# mgmt: a Semaphore project wired to the infra-ansible repo on gitea, with
# an SSH key the deploy script authorizes on the servers, run on a loop.
# --------------------------------------------------------------------------
def role_mgmt(cfg, ros):
    w = Web("labdata-seed")
    base = "http://127.0.0.1:3000/api"

    def call(method, path, body=None):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with w.opener.open(req, timeout=30) as r:
            data = r.read()
            return json.loads(data) if data else None

    def get_or_create(kind, name, body):
        found = [x for x in call("GET", f"/project/{pid}/{kind}") or [] if x["name"] == name]
        if found:
            return found[0]
        call("POST", f"/project/{pid}/{kind}", body)
        return [x for x in call("GET", f"/project/{pid}/{kind}") if x["name"] == name][0]

    call("POST", "/auth/login", {"auth": "admin", "password": cfg["admin_pass"]})
    proj = [p for p in call("GET", "/projects") or [] if p["name"] == "Lab operations"]
    pid = proj[0]["id"] if proj else call("POST", "/projects", {"name": "Lab operations", "alert": False})["id"]
    keyfile = activity_key("semaphore@mgmt")
    none_key = get_or_create("keys", "No key (public repo)", {"name": "No key (public repo)", "type": "none",
                                                              "project_id": pid})
    ssh_key = get_or_create("keys", "lab ubuntu key", {
        "name": "lab ubuntu key", "type": "ssh", "project_id": pid,
        "ssh": {"login": "ubuntu", "private_key": open(keyfile).read()}})
    gitea = cfg["ips"].get("gitea") or "gitea"
    repo = get_or_create("repositories", "infra-ansible", {
        "name": "infra-ansible", "project_id": pid, "git_url": f"http://{gitea}:3000/engineering/infra-ansible.git",
        "git_branch": "main", "ssh_key_id": none_key["id"]})
    skip = ("honeypot", "redteam")
    hosts = "\n".join(f"{n} ansible_host={ip}" for n, ip in sorted(cfg["ips"].items()) if ip and n not in skip)
    inv_body = {"name": "Lab hosts", "project_id": pid, "type": "static", "ssh_key_id": ssh_key["id"],
                "inventory": f"[all]\n{hosts}\n\n[all:vars]\nansible_user=ubuntu\n"
                             "ansible_ssh_common_args='-o StrictHostKeyChecking=accept-new'\n"}
    inv = get_or_create("inventory", "Lab hosts", inv_body)
    if inv.get("ssh_key_id") != ssh_key["id"]:                 # older seeds used no key
        call("PUT", f"/project/{pid}/inventory/{inv['id']}", {**inv_body, "id": inv["id"]})
    env = get_or_create("environment", "Default", {"name": "Default", "project_id": pid, "json": "{}", "env": "{}"})
    have = {t["name"] for t in call("GET", f"/project/{pid}/templates") or []}
    for name in PLAYBOOKS:
        title = name.removesuffix(".yml").replace("-", " ").capitalize()
        if title not in have:
            call("POST", f"/project/{pid}/templates", {
                "project_id": pid, "name": title, "playbook": f"playbooks/{name}", "app": "ansible", "type": "",
                "repository_id": repo["id"], "inventory_id": inv["id"], "environment_id": env["id"]})
    install_loop("lab-semaphore", "SOC-lab scheduled Semaphore jobs (never stops)",
                 {"profile": "semaphore", "admin_pass": cfg["admin_pass"], "log": "/var/log/lab-semaphore.log"})
    log(f"Semaphore project 'Lab operations' with {len(PLAYBOOKS)} task templates; lab-semaphore loop running")
    seed_kuma(cfg, service_accounts(ros))


def kuma_monitors(ips, realm):
    """What IT watches in Uptime Kuma: every lab service except honeypot/redteam."""
    http = [("Gitea", "gitea", "http://{}:3000/"), ("demo-app (prod)", "prod", "http://{}:8080/health"),
            ("IT status page", "sensor", "http://{}/"), ("Semaphore", "mgmt", "http://{}:3000/"),
            ("Juice Shop", "vulntarget", "http://{}:3000/"), ("DVWA", "vulntarget", "http://{}/login.php"),
            ("Wazuh dashboard", "siem", "https://{}/"), ("Velociraptor", "velociraptor", "https://{}:8889/"),
            ("Caldera", "caldera", "http://{}:8888/"), ("LDAP Account Manager", "lam", "http://{}/lam/"),
            ("Webmail", "mail", "http://{}/roundcube/"), ("DFIR-IRIS", "iris", "https://{}/login")]
    ports = [("dc01 LDAP", "dc01", 389), ("dc01 Kerberos", "dc01", 88), ("fs01 SMB", "fs01", 445),
             ("Wazuh agents", "siem", 1514), ("sensor FTP", "sensor", 21), ("ansible-ctrl SSH", "ansible-ctrl", 22),
             ("Mail SMTP", "mail", 25), ("Mail IMAP", "mail", 143), ("Web proxy", "sensor", 3128)]
    out = [{"type": "http", "name": n, "url": u.format(ips[h]), "ignoreTls": u.startswith("https")}
           for n, h, u in http if ips.get(h)]
    for m in out:
        if m["name"] == "Velociraptor":                 # its GUI answers 401 until you log in
            m["accepted_statuscodes"] = ["200-299", "300-399", "401"]
    out += [{"type": "port", "name": n, "hostname": ips[h], "port": p} for n, h, p in ports if ips.get(h)]
    if ips.get("dc01"):
        out.append({"type": "dns", "name": "AD DNS", "hostname": f"fs01.{realm.lower()}",
                    "dns_resolve_server": ips["dc01"], "port": 53})
    return out


def seed_kuma(cfg, acct):
    """Create Uptime Kuma's admin account (first-run setup) and its monitors."""
    for _ in range(30):
        try:
            io_ = KumaIO("http://127.0.0.1:3001")
            break
        except OSError:
            time.sleep(5)
    else:
        log("WARN: Uptime Kuma did not answer on 3001 -- monitors not created")
        return
    if "setup" in io_.events:                                   # no account yet
        res = io_.call("setup", acct["kuma_user"], acct["kuma_pass"])
        if not res.get("ok"):
            log(f"WARN: Uptime Kuma setup failed: {res.get('msg')}")
            return
        io_ = KumaIO("http://127.0.0.1:3001")
    res = io_.call("login", {"username": acct["kuma_user"], "password": acct["kuma_pass"], "token": ""})
    if not res.get("ok"):
        log("WARN: Uptime Kuma already has a different admin account -- monitors not created")
        return
    have = {m["name"] for m in io_.wait("monitorList")[0].values()}
    added = 0
    for m in kuma_monitors(cfg["ips"], cfg["realm"]):
        if m["name"] in have:
            continue
        mon = {"url": "https://", "method": "GET", "interval": 60, "retryInterval": 60, "resendInterval": 0,
               "maxretries": 1, "timeout": 30, "notificationIDList": {}, "ignoreTls": False, "upsideDown": False,
               "packetSize": 56, "expiryNotification": False, "maxredirects": 10,
               "accepted_statuscodes": ["200-299", "300-399"], "dns_resolve_type": "A",
               "dns_resolve_server": cfg["ips"].get("dc01", "127.0.0.1"), "docker_container": "",
               "docker_host": None, "proxyId": None, "httpBodyEncoding": "json", "kafkaProducerBrokers": [],
               "kafkaProducerSaslOptions": {"mechanism": "None"}, "kafkaProducerSsl": False,
               "kafkaProducerAllowAutoTopicCreation": False, "gamedigGivenPortOnly": True, **m}
        res = io_.call("add", mon)
        if res.get("ok"):
            added += 1
        else:
            log(f"WARN: Uptime Kuma monitor '{m['name']}': {res.get('msg')}")
    log(f"Uptime Kuma: admin account and {added} new monitors (login: {acct['kuma_user']})")


# --------------------------------------------------------------------------
# mail: mailboxes for everyone, AD logins for Dovecot, a few weeks of old
# mail, and outside mail arriving all day (lab-mailflow).
# --------------------------------------------------------------------------
NEWSLETTERS = [("news@nordic-tech-weekly.example", "Nordic Tech Weekly"),
               ("hello@cloudcost-digest.example", "CloudCost Digest"),
               ("events@sec-meetup.example", "Security Meetup Stockholm"),
               ("noreply@office-supplies.example", "OfficeSupplies")]
VENDOR_DOMAINS = ["papercraft-supplies.example", "nordlink-telecom.example", "kaffekompaniet.example",
                  "cleanroom-facility.example", "hostingpartner.example"]
INTERNAL_SUBJECTS = ["{project} status update", "Agenda for Monday", "Budget Q{q} -- comments?",
                     "Customer meeting with {customer}", "Lunch on Thursday?", "Notes from today's standup",
                     "Draft for review: {project} proposal", "Re: holiday schedule", "Quick question about {project}",
                     "Travel request", "Slides for the all-hands"]
ALL_STAFF_SUBJECTS = [("it", "IT maintenance window this Saturday"), ("it", "Reminder: lock your screen"),
                      ("hr", "Welcome our new colleagues"), ("hr", "Updated parental leave policy"),
                      ("hr", "Kestrel summer party -- save the date"), ("it", "New VPN client available")]


def mail_body(rng, given, topic):
    lines = [f"Hi {given},", "",
             rng.choice([f"Just following up on {topic}.", f"Here is the latest on {topic}.",
                         f"Could you have a look at {topic} before Friday?",
                         f"Thanks for the input on {topic} -- I've updated the document."]),
             rng.choice(["Let me know what you think.", "No rush, but before the weekly would be great.",
                         "I put the details on \\\\fs01\\public\\Projects.", "Happy to discuss over a call."]),
             "", "Best regards,"]
    return "\n".join(lines)


def make_mail(sender, sender_name, to, subject, body, when, attachment=None):
    from email.message import EmailMessage
    from email.utils import format_datetime, make_msgid
    m = EmailMessage()
    m["From"] = f"{sender_name} <{sender}>"
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = format_datetime(when.astimezone())
    m["Message-ID"] = make_msgid(domain=sender.split("@")[1])
    m.set_content(body)
    if attachment:
        name, data = attachment
        m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return m


def inbound_mail(rng, realm, people, when):
    """One normal message from outside: a newsletter, an invoice, a customer question."""
    kind = rng.choices(["newsletter", "invoice", "customer", "notice"], [4, 2, 2, 1])[0]
    dom = realm.lower()
    if kind == "newsletter":
        sender, name = rng.choice(NEWSLETTERS)
        to = rng.choice(people)
        subject = f"{name}: {rng.choice(['This week in cloud', 'Top stories', 'Your monthly digest', 'Upcoming events'])}"
        body = f"{name}\n\nThis week's highlights, upcoming events and offers.\n\nUnsubscribe: reply STOP."
        return make_mail(sender, name, f"{to['given']} {to['sn']} <{to['sam']}@{dom}>", subject, body, when)
    if kind == "invoice":
        vendor = rng.choice(VENDOR_DOMAINS)
        no = f"INV-{when:%Y}-{rng.randint(1000, 9999)}"
        return make_mail(f"billing@{vendor}", vendor.split(".")[0].replace("-", " ").title(), f"finance@{dom}",
                         f"Invoice {no}", f"Hello,\n\nPlease find invoice {no} attached.\nDue in 30 days.\n\nKind regards",
                         when, (f"{no}.pdf", b"%PDF-1.4\n" + os.urandom(rng.randint(2000, 9000))))
    if kind == "customer":
        cust = rng.choice(customers(rng, 12))
        slug = cust.split()[0].lower()
        return make_mail(f"contact@{slug}-{rng.randint(10, 99)}.example", cust, f"sales@{dom}",
                         rng.choice(["Question about your offer", "Request for a quote", "Follow-up on our call"]),
                         f"Hello,\n\nWe would like to know more about pricing for next quarter.\n\nRegards,\n{cust}", when)
    to = rng.choice(people)
    return make_mail("no-reply@parcel-tracking.example", "Parcel Tracking", f"{to['sam']}@{dom}",
                     "Your parcel is on its way", "Your parcel will be delivered within 2 working days.", when)


def role_mail(cfg, ros):
    import mailbox
    realm = cfg["realm"].lower()
    base = ",".join(f"dc={p}" for p in realm.split("."))
    rng = random.Random(f"{ros['seed']}-mail")
    users = ros["users"]
    # AD logins for Dovecot: an LDAPS bind as <user>@<realm>, verified against dc01's CA.
    ca = cfg.get("dc_ca_pem", "")
    if "BEGIN CERTIFICATE" not in ca:
        raise RuntimeError("no CA certificate from dc01 in the config")
    with open("/etc/dovecot/lab-dc01-ca.pem", "w") as fh:
        fh.write(ca)
    if f"dc01.{realm}" not in open("/etc/hosts").read():            # the certificate is issued to DC01.<realm>
        with open("/etc/hosts", "a") as fh:
            fh.write(f"{cfg['ips']['dc01']} dc01.{realm} dc01\n")
    with open("/etc/dovecot/dovecot-ldap.conf.ext", "w") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(f"uris = ldaps://dc01.{realm}\ntls_ca_cert_file = /etc/dovecot/lab-dc01-ca.pem\n"
                 f"tls_require_cert = hard\nldap_version = 3\nbase = {base}\n"
                 f"auth_bind = yes\nauth_bind_userdn = %n@{realm}\n")
    with open("/etc/dovecot/conf.d/98-soc-lab-auth.conf", "w") as fh:
        fh.write("# SOC-lab: passwords are checked against AD (LDAPS bind as the user)\n"
                 "passdb {\n  driver = ldap\n  args = /etc/dovecot/dovecot-ldap.conf.ext\n}\n")
    # Mailboxes for everyone (leavers too: their mail is kept), and group addresses.
    with open("/etc/postfix/vmailbox", "w") as fh:
        for u in users:
            fh.write(f"{u['sam']}@{realm} {realm}/{u['sam']}/\n")
    groups = {"it": "IT", "helpdesk": "IT", "finance": "Finance", "hr": "HR", "sales": "Sales"}
    with open("/etc/postfix/virtual", "w") as fh:
        for alias, dept in groups.items():
            members = [f"{u['sam']}@{realm}" for u in users if u["dept"] == dept and u["enabled"]]
            fh.write(f"{alias}@{realm} {', '.join(members)}\n")
        everyone = [f"{u['sam']}@{realm}" for u in users if u["enabled"]]
        fh.write(f"all-staff@{realm} {', '.join(everyone)}\ninfo@{realm} sales@{realm}\n")
    run(["postmap", "/etc/postfix/vmailbox"])
    run(["postmap", "/etc/postfix/virtual"])
    run(["systemctl", "reload", "postfix"])
    run(["systemctl", "restart", "dovecot"])
    # A few weeks of old mail, written straight into the Maildirs.
    marker = "/var/lib/labdata/mail.seeded"
    added = 0
    if not os.path.exists(marker):
        people = [u for u in users if u["enabled"]]
        by_dept = {}
        for u in people:
            by_dept.setdefault(u["dept"], []).append(u)
        os.makedirs(f"/var/vmail/{realm}", exist_ok=True)    # Maildir() only creates the last level
        for u in users:
            box = mailbox.Maildir(f"/var/vmail/{realm}/{u['sam']}", create=True)
            msgs = []
            for _ in range(rng.randint(8, 20)):
                when = recent_time(rng, 45)
                peer = rng.choice(by_dept.get(u["dept"], people))
                subject = rng.choice(INTERNAL_SUBJECTS).format(project=rng.choice(PROJECTS), q=quarter(when.date()),
                                                                customer=rng.choice(customers(rng, 6)))
                msgs.append((make_mail(f"{peer['sam']}@{realm}", f"{peer['given']} {peer['sn']}",
                                       f"{u['sam']}@{realm}", subject, mail_body(rng, u["given"], subject) +
                                       f"\n{peer['given']}", when), when))
            for _ in range(rng.randint(2, 5)):
                when = recent_time(rng, 45)
                frm, subject = rng.choice(ALL_STAFF_SUBJECTS)
                msgs.append((make_mail(f"{frm}@{realm}", "IT" if frm == "it" else "HR", f"all-staff@{realm}",
                                       subject, f"Hello everyone,\n\n{subject}. Details on the intranet.\n\n{frm.upper()}",
                                       when), when))
            for _ in range(rng.randint(2, 6)):
                when = recent_time(rng, 45)
                msgs.append((inbound_mail(rng, realm, [u], when), when))
            for msg, when in msgs:
                mm = mailbox.MaildirMessage(msg)
                age_days = (NOW - when).days
                if age_days > 1 or rng.random() < 0.6:         # older mail has been read
                    mm.set_subdir("cur")
                    mm.add_flag("S")
                mm.set_date(when.timestamp())
                box.add(mm)
                added += 1
        run(["chown", "-R", "vmail:vmail", "/var/vmail"])
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").close()
    install_loop("lab-mailflow", "SOC-lab mail from outside: newsletters, invoices, customers (never stops)", {
        "profile": "mailflow", "log": "/var/log/lab-mailflow.log", "realm": cfg["realm"],
        "people": [{k: u[k] for k in ("sam", "given", "sn", "dept")} for u in users if u["enabled"]]})
    # Check the AD login the way a mail client would.
    import imaplib
    probe = next(u for u in users if u["enabled"])
    try:
        with imaplib.IMAP4("127.0.0.1") as im:
            ok = im.login(probe["sam"], probe["password"])[0] == "OK"
    except imaplib.IMAP4.error:
        ok = False
    log(f"{len(users)} mailboxes, {added} old messages, group addresses; lab-mailflow running"
        + ("" if ok else " -- WARN: test IMAP login against AD failed"))


def profile_mailflow(cfg, rng):
    """mail: outside mail arrives through the front door (SMTP on port 25)."""
    import smtplib
    if rng.random() > busy():
        return ["(quiet)"]
    msg = inbound_mail(rng, cfg["realm"], cfg["people"], dt.datetime.now())
    with smtplib.SMTP("127.0.0.1", 25, timeout=30) as smtp:
        smtp.send_message(msg)
    return [f"delivered '{msg['Subject']}' from {msg['From']} to {msg['To']}"]


# --------------------------------------------------------------------------
# lam: point LDAP Account Manager at the domain (LDAPS, verified against
# dc01's CA) so IT can log in with their own accounts.
# --------------------------------------------------------------------------
def role_lam(cfg, ros):
    realm = cfg["realm"].lower()
    base = ",".join(f"dc={p}" for p in realm.split("."))
    conf_dir = "/var/lib/ldap-account-manager/config"
    conf = f"{conf_dir}/lam.conf"
    main_cfg = open(f"{conf_dir}/config.cfg").read()
    if "logLevel: 4" in main_cfg:                       # 4 = warnings only; 5 also logs logins
        with open(f"{conf_dir}/config.cfg", "w") as fh:
            fh.write(main_cfg.replace("logLevel: 4", "logLevel: 5"))
    current = open(conf).read()
    if "dc=my-domain,dc=com" not in current.lower():
        log("server profile already configured -- left as it is")
        return
    ca = cfg.get("dc_ca_pem", "")
    if "BEGIN CERTIFICATE" not in ca:
        raise RuntimeError("no CA certificate from dc01 in the config")
    with open("/etc/ldap/lab-dc01-ca.pem", "w") as fh:
        fh.write(ca)
    ldap_conf = open("/etc/ldap/ldap.conf").read()
    if "lab-dc01-ca.pem" not in ldap_conf:
        with open("/etc/ldap/ldap.conf", "a") as fh:
            fh.write("\n# SOC-lab: trust dc01's Samba CA for LDAPS (certificates are still checked)\n"
                     "TLS_CACERT /etc/ldap/lab-dc01-ca.pem\n")
    hosts = open("/etc/hosts").read()
    if f"dc01.{realm}" not in hosts:                     # the certificate is issued to DC01.<realm>
        with open("/etc/hosts", "a") as fh:
            fh.write(f"{cfg['ips']['dc01']} dc01.{realm} dc01\n")
    admins = [f"cn=Administrator,cn=Users,{base}"] + [
        f"CN={u['given']} {u['sn']},OU=IT,OU=LabUsers,{base}"
        for u in ros["users"] if u["dept"] == "IT" and u["enabled"]]
    passwd = next((ln for ln in current.splitlines() if ln.startswith("Passwd:")), "Passwd: lam")
    out = []
    for ln in open(f"{conf_dir}/windows_samba4.sample.conf").read().splitlines():
        if ln.startswith("ServerURL:"):
            ln = f"ServerURL: ldaps://dc01.{realm}:636"
        elif ln.startswith("Admins:"):
            ln = "Admins: " + ";".join(admins)
        elif ln.startswith("Passwd:"):
            ln = passwd                                  # keep LAM's own config password
        elif ln.startswith("loginSearchSuffix:"):
            ln = f"loginSearchSuffix: {base}"
        out.append(ln.replace("dc=my-domain,dc=com", base))
    with open(conf, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write("\n".join(out) + "\n")
    shutil.chown(conf, "www-data", "root")
    run(["systemctl", "restart", "apache2"])
    # Check the result the way a person would: log in as Administrator and list users.
    w = Web("labdata-seed")
    w.get("http://127.0.0.1/lam/templates/login.php")
    w.get("http://127.0.0.1/lam/templates/login.php", urllib.parse.urlencode(
        {"username": admins[0], "passwd": cfg["admin_pass"], "language": "en_US.utf8", "checklogin": "Login"}))
    _, page = w.get("http://127.0.0.1/lam/templates/lists/list.php?type=user")
    ok = "Administrator" in page
    log(f"server profile for {realm} over LDAPS, {len(admins) - 1} IT staff can log in"
        + ("" if ok else " -- WARN: test login did not show the user list"))


# --------------------------------------------------------------------------
# velociraptor: an API user for scripted hunts, a few hunts to start with,
# and an analyst who keeps running hunts during office hours.
# --------------------------------------------------------------------------
VELO = ["/usr/local/bin/velociraptor", "--api_config", "/etc/velociraptor/api.config.yaml"]
VELO_HUNTS = {
    "Linux.Ssh.AuthorizedKeys": "Who can SSH in? (authorized_keys on all endpoints)",
    "Linux.Sys.Users": "Local accounts review",
    "Linux.Sys.Crontab": "Scheduled tasks review",
    "Linux.Sys.Pslist": "Process snapshot",
    "Linux.Network.NetstatEnriched": "Listening ports and connections",
    "Linux.Syslog.SSHLogin": "SSH logins from syslog",
    "Linux.Sys.LastUserLogin": "Last logins (wtmp)",
    "Linux.Sys.BashHistory": "Shell history sweep",
    "Linux.Sys.Services": "Services inventory",
    "Linux.Sys.SUID": "SUID binaries",
    "Linux.Users.RootUsers": "Accounts with UID 0",
    "Generic.Client.Info": "Endpoint inventory",
}


def velo_hunt(artifact, description):
    vql = (f"SELECT hunt(description={json.dumps(description)}, artifacts={json.dumps(artifact)}, "
           "expires=now() + 86400).HuntId AS id FROM scope()")
    res = run([*VELO, "query", "--format", "json", vql], check=False, timeout=120)
    try:
        return json.loads(res.stdout)[0]["id"]
    except (ValueError, LookupError, TypeError):
        return None


def role_velociraptor(cfg, ros):
    api = "/etc/velociraptor/api.config.yaml"
    if not os.path.exists(api):
        server = ["/usr/local/bin/velociraptor", "--config", "/etc/velociraptor/server.config.yaml"]
        run([*server, "config", "api_client", "--name", "lab-analyst", "--role", "administrator", api])
        os.chmod(api, 0o600)
        # The API user also needs a GUI account (random password: it never logs in there),
        # and the running server only picks it up after a restart.
        run([*server, "user", "add", "--role", "administrator", "lab-analyst",
             base64.b64encode(os.urandom(18)).decode()])
        run(["systemctl", "restart", "velociraptor"])
    for _ in range(30):
        if run([*VELO, "query", "SELECT * FROM info()"], check=False, timeout=30).returncode == 0:
            break
        time.sleep(5)
    else:
        raise RuntimeError("Velociraptor API did not answer")
    marker = "/var/lib/labdata/velociraptor-hunts.seeded"
    started = []
    if not os.path.exists(marker):
        for art in ["Generic.Client.Info", "Linux.Sys.Users", "Linux.Ssh.AuthorizedKeys", "Linux.Sys.Crontab"]:
            if velo_hunt(art, "Baseline: " + VELO_HUNTS[art]):
                started.append(art)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").close()
    install_loop("lab-velociraptor", "SOC-lab analyst running Velociraptor hunts (never stops)",
                 {"profile": "velociraptor", "log": "/var/log/lab-velociraptor.log"})
    log(f"API user lab-analyst, {len(started)} baseline hunts; lab-velociraptor loop running")


def profile_velociraptor(cfg, rng):
    """An analyst starts one hunt every few hours during the working day."""
    if rng.random() > busy():
        return ["(quiet: analyst not at their desk)"]
    art = rng.choice(list(VELO_HUNTS))
    hid = velo_hunt(art, f"{VELO_HUNTS[art]} ({dt.date.today():%Y-%m-%d})")
    return [f"hunt {hid} started: {art}" if hid else f"hunt {art} could not be started"]


# --------------------------------------------------------------------------
# sensor: the office's legacy services -- a status page with downloads, a
# scan-to-FTP drop and a telnet console. They live on sensor because NAT
# means it only sees its own traffic: this way Suricata and Zeek see HTTP,
# FTP and telnet sessions (cleartext logins included) from the staff.
# --------------------------------------------------------------------------
def role_sensor(cfg, ros):
    rng = random.Random(f"{ros['seed']}-sensor")
    acct = service_accounts(ros)
    apt("vsftpd", "inetutils-telnetd", "inetutils-inetd")
    for user, pw, shell in ((acct["ftp_user"], acct["ftp_pass"], "/usr/sbin/nologin"),
                            (acct["telnet_user"], acct["telnet_pass"], "/bin/bash")):
        if run(["id", user], check=False).returncode:
            run(["useradd", "--create-home", "--shell", shell, user])
        run(["chpasswd"], input=f"{user}:{pw}\n")
    if "/usr/sbin/nologin" not in open("/etc/shells").read():
        with open("/etc/shells", "a") as fh:
            fh.write("/usr/sbin/nologin\n")            # vsftpd's PAM check wants the shell listed
    # telnet console banner + a tiny "show-ports" command
    with open("/usr/local/bin/show-ports", "w") as fh:
        fh.write("#!/bin/sh\nss -tln | awk 'NR>1 {print $4}'\n")
    os.chmod("/usr/local/bin/show-ports", 0o755)
    with open("/etc/issue.net", "w") as fh:
        fh.write(f"{ros['company']} legacy network console -- authorised staff only\n")
    # vsftpd: local logins only, chrooted, writable scan drop, its own log
    conf = open("/etc/vsftpd.conf").read()
    for k, v in {"listen": "YES", "listen_ipv6": "NO", "anonymous_enable": "NO", "local_enable": "YES",
                 "write_enable": "YES", "chroot_local_user": "YES", "allow_writeable_chroot": "YES",
                 "xferlog_enable": "YES", "xferlog_std_format": "NO", "log_ftp_protocol": "NO",
                 "vsftpd_log_file": "/var/log/vsftpd.log", "dual_log_enable": "YES",
                 "pasv_min_port": "40000", "pasv_max_port": "40100"}.items():
        lines = [ln for ln in conf.splitlines() if not ln.lstrip("#").startswith(k + "=")]
        conf = "\n".join(lines + [f"{k}={v}"]) + "\n"
    open("/etc/vsftpd.conf", "w").write(conf)
    home = pwd.getpwnam(acct["ftp_user"]).pw_dir
    scans = Writer(os.path.join(home, "scans"), rng, default_owner=acct["ftp_user"])
    for _ in range(25):
        when = recent_time(rng, 45)
        scans.put(f"scan_{when:%Y%m%d_%H%M%S}.pdf", b"%PDF-1.4\n" + os.urandom(rng.randint(3000, 30000)), when)
    run(["chown", "-R", f"{acct['ftp_user']}:{acct['ftp_user']}", home])
    run(["systemctl", "restart", "vsftpd"])
    # telnet through inetd
    inetd = open("/etc/inetd.conf").read()
    if "telnetd" not in inetd or inetd.count("#<off># telnet"):
        inetd = "\n".join(ln for ln in inetd.splitlines() if "telnet" not in ln)
        inetd += "\ntelnet stream tcp nowait root /usr/sbin/tcpd /usr/sbin/telnetd\n"
        open("/etc/inetd.conf", "w").write(inetd)
    run(["systemctl", "enable", "inetutils-inetd"], check=False)
    run(["systemctl", "restart", "inetutils-inetd"])
    # status page with downloadable files
    www = "/srv/lab-status"
    os.makedirs(f"{www}/downloads", exist_ok=True)
    svc = ["Email", "VPN", "File shares", "Intranet", "Billing", "Printers"]
    rows = "".join(f"<tr><td>{s}</td><td>{rng.choice(['Operational'] * 5 + ['Degraded'])}</td></tr>" for s in svc)
    page = (f"<!doctype html><meta charset=utf-8><title>{ros['company']} status</title>"
            f"<h1>{ros['company']} IT status</h1><table>{rows}</table>"
            "<p><a href=services.html>Services</a> | <a href=changes.html>Planned changes</a></p><ul>"
            + "".join(f"<li><a href=downloads/{n}>{n}</a></li>" for n in STATUS_DOWNLOADS) + "</ul>")
    open(f"{www}/index.html", "w").write(page)
    open(f"{www}/services.html", "w").write("<!doctype html><title>Services</title><h1>Services</h1>"
                                            + "".join(f"<p>{s}</p>" for s in svc))
    open(f"{www}/changes.html", "w").write(f"<!doctype html><title>Changes</title><h1>Planned changes</h1>"
                                           f"<p>{(TODAY + dt.timedelta(days=rng.randint(2, 20))):%Y-%m-%d}: "
                                           "firewall maintenance 22:00-23:00</p>")
    for n in STATUS_DOWNLOADS:
        p = f"{www}/downloads/{n}"
        if not os.path.exists(p):
            with open(p, "wb") as fh:
                fh.write(os.urandom(rng.randint(50000, 400000)))
    with open("/etc/systemd/system/lab-status-page.service", "w") as fh:
        fh.write("[Unit]\nDescription=SOC-lab IT status page (port 80)\nAfter=network-online.target\n\n"
                 f"[Service]\nExecStart=/usr/bin/python3 -m http.server 80 --directory {www}\n"
                 "Restart=always\n\n[Install]\nWantedBy=multi-user.target\n")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "lab-status-page"])
    # ship the FTP log to Wazuh too (telnet logins land in auth.log already)
    ossec = "/var/ossec/etc/ossec.conf"
    if os.path.exists(ossec) and "vsftpd.log" not in open(ossec).read():
        with open(ossec, "a") as fh:
            fh.write("\n<ossec_config>\n  <localfile>\n    <log_format>syslog</log_format>\n"
                     "    <location>/var/log/vsftpd.log</location>\n  </localfile>\n</ossec_config>\n")
        run(["systemctl", "restart", "wazuh-agent"], check=False)
    log(f"status page :80, FTP drop ({acct['ftp_user']}, {scans.count} scans), telnet console ({acct['telnet_user']})")


PROFILES = {"staff": profile_staff, "ansible": profile_ansible, "semaphore": profile_semaphore,
            "backup": profile_backup, "velociraptor": profile_velociraptor, "mailflow": profile_mailflow}


def run_once(path):
    """One pass of a profile (used by timers, and handy for testing)."""
    cfg = json.load(open(path))
    events = PROFILES[cfg["profile"]](cfg, random.Random())
    with open(cfg.get("log", f"/var/log/lab-{cfg['profile']}.log"), "a") as fh:
        for e in events:
            fh.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {e}\n")
    print("\n".join(events))


# --------------------------------------------------------------------------
# vulntarget: DVWA's database, so it's usable straight away
# --------------------------------------------------------------------------
def role_vulntarget(cfg, ros):
    import http.cookiejar
    import re
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    for _ in range(30):
        try:
            page = opener.open("http://127.0.0.1/setup.php", timeout=10).read().decode(errors="replace")
            break
        except OSError:
            time.sleep(5)
    else:
        raise RuntimeError("DVWA did not answer on port 80")
    token = re.search(r"name='user_token' value='([0-9a-f]+)'", page)
    data = f"create_db=Create+%2F+Reset+Database&user_token={token.group(1) if token else ''}".encode()
    opener.open(urllib.request.Request("http://127.0.0.1/setup.php", data=data), timeout=60).read()
    login = opener.open("http://127.0.0.1/login.php", timeout=10).read().decode(errors="replace")
    log("DVWA database created" if "user_token" in login else "WARN: DVWA setup did not complete")


# --------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    role = sys.argv[1]
    if role == "roster":
        print(json.dumps(roster(int(sys.argv[2]), int(sys.argv[3])), indent=1, ensure_ascii=False))
        return
    if role == "services":                       # upgrade an older roster's service accounts
        print(json.dumps(service_accounts(json.load(open(sys.argv[2]))), ensure_ascii=False))
        return
    if role == "loop":
        loop(sys.argv[2])
        return
    if role == "run":
        run_once(sys.argv[2])
        return
    cfg = json.load(open(sys.argv[2]))
    ros = json.load(open(sys.argv[3]))
    roles = {"dc01": role_dc01, "fs01": role_fs01, "gitea": role_gitea, "mgmt": role_mgmt,
             "vulntarget": role_vulntarget, "ansible-ctrl": role_ansible, "sensor": role_sensor,
             "lam": role_lam, "velociraptor": role_velociraptor, "mail": role_mail}
    if role == "client":
        role_client(cfg, ros, sys.argv[4])
    elif role in roles:
        roles[role](cfg, ros)
    else:
        sys.exit(f"unknown role {role}")
    for w in WRITERS:                              # only after the role succeeded
        w.finish()


if __name__ == "__main__":
    main()
