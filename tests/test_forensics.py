"""The network forensics lab: sensor as the lab's gateway and tap (lib/gateway.sh, the deploy's
network-tap stage, sensor's template), arkime, and redteam's scheduled attack scenario.

Run:   python3 -m unittest discover -s tests -v
"""

import base64
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

from helpers import BASH
from test_templates import provision_script, render

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMPL = ROOT / "cloud-init-templates"
DEPLOY = (ROOT / "deploy-soc-lab.sh").read_text()


def sh(script):
    out = subprocess.run([BASH, "-c", f"source lib/labs.sh; source lib/gateway.sh; {script}"], cwd=ROOT,
                         capture_output=True, text=True)
    return out


def guarded_block(lines, guard):
    """(block, outside): the lines inside the first `guard` ... matching `fi`, and all other lines."""
    i_open = lines.index(next(ln for ln in lines if ln.strip() == guard))
    indent = len(lines[i_open]) - len(lines[i_open].lstrip())
    i_close = next(i for i in range(i_open + 1, len(lines))
                   if lines[i].strip() in ("fi", "else") and len(lines[i]) - len(lines[i].lstrip()) == indent)
    return "\n".join(lines[i_open:i_close]), "\n".join(lines[:i_open] + lines[i_close:])


@unittest.skipUnless(BASH, "needs bash 4+")
class Gateway(unittest.TestCase):
    def test_gateway_dropin(self):
        """The netplan drop-in sends the default route and the other members through sensor, persistently."""
        r = sh("gateway_netplan default 192.168.252.10 192.168.252.20 192.168.252.21")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = yaml.safe_load(r.stdout)
        self.assertEqual(doc["network"]["version"], 2)
        routes = doc["network"]["ethernets"]["default"]["routes"]
        self.assertEqual([x["to"] for x in routes], ["default", "192.168.252.20/32", "192.168.252.21/32"])
        for route in routes:
            self.assertEqual(route["via"], "192.168.252.10")
            self.assertLess(route["metric"], 100, "must beat the DHCP default route (metric 100)")
        self.assertIn("GATEWAY_DROPIN=/etc/netplan/60-lab-gateway.yaml", (ROOT / "lib" / "gateway.sh").read_text())
        self.assertIn("> ${GATEWAY_DROPIN}", (ROOT / "lib" / "gateway.sh").read_text())

    def test_gateway_dropin_rejects_bad_input(self):
        for args in ("default ''", "default not-an-ip", "'bad id' 10.0.0.1", "default 10.0.0.1 '10.0.0.2; reboot'"):
            with self.subTest(args=args):
                r = sh(f"gateway_netplan {args}")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(r.stdout, "")

    def test_gateway_only_in_forensics(self):
        for helper in ("lab_sensor_gateway", "lab_attack_scenario"):
            self.assertEqual([sh(f"{helper} {lab}").stdout.strip() for lab in
                              ("soc", "forensics", "pentest", "devsecops")], ["0", "1", "0", "0"], helper)

    def test_gateway_members(self):
        """Every lab VM but sensor and arkime routes through sensor, clients included."""
        members = sh("gateway_members forensics medium").stdout.split()
        for vm in ("dc01", "fs01", "redteam", "vulntarget", "mail", "honeypot", "client01", "client03"):
            self.assertIn(vm, members)
        for vm in ("sensor", "arkime"):
            self.assertNotIn(vm, members)

    def test_stage_only_in_forensics_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            work = tmp / "lab"
            work.mkdir()
            shutil.copy(ROOT / "deploy-soc-lab.sh", work)
            shutil.copytree(ROOT / "lib", work / "lib")
            bindir = tmp / "bin"
            bindir.mkdir()
            fake = bindir / "multipass"
            fake.write_text('#!/bin/sh\n[ "$1" = list ] && printf "Name,State,IPv4,Release,Image\\n"\nexit 0\n')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", LAB_HOST_RAM_GB="64")
            env.pop("LAB_MENU_TTY", None)
            for lab, expected in (("forensics", True), ("soc", False), ("pentest", False)):
                with self.subTest(lab=lab):
                    r = subprocess.run([BASH, str(work / "deploy-soc-lab.sh"), "--lab", lab, "--size", "medium",
                                        "--dry-run", "--yes"], cwd=work, input="", capture_output=True,
                                       text=True, env=env, timeout=120)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertEqual("would route through sensor:" in r.stdout, expected, r.stdout)
            self.assertRegex(DEPLOY, r'if \[ "\$\(lab_sensor_gateway "\$\{LAB\}"\)" = 1 \][^\n]*; then\n(?:.*\n){0,3}\s+route_through_sensor\n')


# A fake `multipass` for route_through_sensor: logs every call; sensor's gateway restart prints
# $FAKE_TAP, a member named in $FAKE_BAD_MEMBER fails its post-apply check.
FAKE_MP = r"""#!/bin/bash
cmd="$*"
printf '%s\n' "${cmd//$'\n'/ }" >> "$FAKE_MP_LOG"   # one line per call
case "$cmd" in
    *"netplan get ethernets"*) echo "default:"; echo "  dhcp4: true" ;;
    "exec sensor "*) printf '%s\n' "$FAKE_TAP" ;;
    "exec redteam -- sudo sh -c "*lab-attack*) [ -n "${FAKE_REDTEAM_FAIL:-}" ] && exit 1 ;;
    exec\ *rm\ -f\ /etc/netplan/60-lab-gateway.yaml*) : ;;
    exec\ *"netplan apply"*)
        name="$2"
        if [ "$name" = "${FAKE_BAD_MEMBER:-}" ]; then echo "route"; echo "dns"; echo "mirror 000"
        else echo "route 10.0.0.9"; echo "dns 10.0.0.2"; echo "mirror 200"; fi ;;
esac
exit 0
"""

STAGE = r"""
set -euo pipefail
source lib/labs.sh
source lib/gateway.sh
say() { echo "$*"; }
progress_task_start() { :; }
progress_task_done() { :; }
is_enabled() { true; }
run_with_timeout() { shift; "$@"; }
get_ip() {   # sensor 10.0.0.9, arkime 10.0.0.8, dc01 10.0.0.2, the rest 10.0.0.1x; $FAKE_NO_IP has none
    case "$1" in
        "${FAKE_NO_IP:-}") echo "" ;;
        sensor) echo 10.0.0.9 ;; arkime) echo 10.0.0.8 ;; dc01) echo 10.0.0.2 ;;
        *) echo "10.0.0.1$(( $(printf '%s' "$1" | cksum | cut -d' ' -f1) % 10 ))" ;;
    esac
}
LAB=forensics SIZE=small DC_IP=10.0.0.2 DOMAIN_REALM=LAB.LOCAL DOMAIN_NETBIOS=LAB SEED_PASS='Passw0rd!23'
route_through_sensor
"""


@unittest.skipUnless(BASH, "needs bash 4+")
class RouteThroughSensor(unittest.TestCase):
    """Deploy stage 4b (route_through_sensor in lib/gateway.sh) against a fake multipass."""

    def run_stage(self, **env):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        bindir = tmp / "bin"
        bindir.mkdir()
        (bindir / "multipass").write_text(FAKE_MP)
        (bindir / "multipass").chmod(0o755)
        log = tmp / "mp.log"
        log.write_text("")
        e = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_MP_LOG=str(log),
                 RENDERED_DIR=str(tmp), FAKE_TAP="active")
        e.update(env)
        r = subprocess.run([BASH, "-c", f"RENDERED_DIR={tmp}\n{STAGE}"], cwd=ROOT, capture_output=True,
                           text=True, env=e, timeout=60)
        return r, log.read_text().splitlines()

    def dropin_writes(self, calls):
        return [c.split()[1] for c in calls if c.startswith("exec ") and "base64 -d > /etc/netplan/60-lab-gateway.yaml" in c]

    def test_all_members_routed_when_tap_active(self):
        r, calls = self.run_stage()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        members = sh("gateway_members forensics small").stdout.split()
        self.assertEqual(sorted(self.dropin_writes(calls)), sorted(members))
        self.assertFalse([c for c in calls if "rm -f /etc/netplan/60-lab-gateway.yaml" in c])

    def test_tap_not_active_routes_nobody(self):
        for tap in ("inactive", "failed", "Job for lab-gateway.service failed.\nactivating"):
            with self.subTest(tap=tap):
                r, calls = self.run_stage(FAKE_TAP=tap)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(self.dropin_writes(calls), [])
                self.assertEqual(r.stdout.count("[WARN]"), 1, r.stdout)
                self.assertIn("no VM was routed through sensor", r.stdout)

    def test_failed_member_check_removes_its_dropin(self):
        r, calls = self.run_stage(FAKE_BAD_MEMBER="vulntarget")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        removed = [c.split()[1] for c in calls if "rm -f /etc/netplan/60-lab-gateway.yaml" in c]
        self.assertEqual(removed, ["vulntarget"])
        self.assertIn("netplan apply", next(c for c in calls if "rm -f /etc/netplan/60-lab-gateway.yaml" in c))
        self.assertRegex(r.stdout, r"\[WARN\] +vulntarget: .*removed")
        self.assertIn("dc01", self.dropin_writes(calls))

    def test_redteam_config_failure_is_a_warning(self):
        r, calls = self.run_stage(FAKE_REDTEAM_FAIL="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"\[WARN\] +redteam: ")

    def test_targets_skip_members_without_ip(self):
        r, calls = self.run_stage(FAKE_NO_IP="vulntarget")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        write = next(c for c in calls if c.startswith("exec redteam") and "lab-attack" in c)
        b64 = re.search(r"printf '%s' '([A-Za-z0-9+/=]+)' \| base64 -d", write).group(1)
        conf = base64.b64decode(b64).decode()
        self.assertIn("target dc01 10.0.0.2", conf)
        self.assertNotIn("vulntarget", conf)
        self.assertNotRegex(conf, r"(?m)^target \S+ *$")


class SensorTemplate(unittest.TestCase):
    TEXT = (TMPL / "sensor.yaml.tmpl").read_text()

    def test_gateway_flag(self):
        """Forwarding, NAT and the copy to arkime are set up only with SENSOR_GATEWAY=1 (forensics)."""
        self.assertIn('"SENSOR_GATEWAY=$(lab_sensor_gateway "${LAB}")"', DEPLOY)
        lines = provision_script(render(self.TEXT, SENSOR_GATEWAY="0"))
        block, outside = guarded_block(lines, 'if [ "${SENSOR_GATEWAY}" = "1" ]; then')
        for needle in ("60-lab-gateway.conf", "systemctl enable --now lab-gateway"):
            self.assertIn(needle, block)
            self.assertNotIn(needle, outside)
        doc = yaml.safe_load(render(self.TEXT))
        gw = next(e["content"] for e in doc["write_files"] if e["path"] == "/usr/local/lib/lab-gateway/lab-gateway.sh")
        for needle in ("MASQUERADE", "TEE --gateway", "send_redirects=0", "ip_forward=1", "/etc/lab-gateway.conf"):
            self.assertIn(needle, gw)


class RedteamScenario(unittest.TestCase):
    TEXT = (TMPL / "redteam.yaml.tmpl").read_text()

    def scenario(self):
        doc = yaml.safe_load(render(self.TEXT))
        return next(e["content"] for e in doc["write_files"] if e["path"] == "/usr/local/lib/lab-attack/scenario.sh")

    def test_scenario_flag(self):
        """The timer is installed only with ATTACK_SCENARIO=1 (forensics); otherwise the script is removed."""
        self.assertIn('"ATTACK_SCENARIO=$(lab_attack_scenario "${LAB}")"', DEPLOY)
        lines = provision_script(render(self.TEXT, ATTACK_SCENARIO="0"))
        block, outside = guarded_block(lines, 'if [ "${ATTACK_SCENARIO}" = "1" ]; then')
        for needle in ("lab-attack.timer", "OnUnitActiveSec=30min", "systemctl enable --now lab-attack.timer"):
            self.assertIn(needle, block)
            self.assertNotIn(needle, outside)
        self.assertIn("rm -rf /usr/local/lib/lab-attack", outside)

    def test_scenario_uses_shipped_tools_with_timeouts(self):
        """Port scan, Kerberoast, SMB grab, web requests, DNS exfil: each command has a timeout and no stdin."""
        script = re.sub(r"\\\n\s*", " ", self.scenario())   # join continuation lines
        for needle in ("nmap ", "/usr/local/bin/lab-kerberoast", "smbclient ", "curl ", "dig "):
            with self.subTest(tool=needle):
                self.assertIn(needle, script)
        calls = [ln for ln in script.splitlines()
                 if re.match(r"\s*(attack|run_quiet) ", ln)]
        self.assertGreaterEqual(len(calls), 5)
        self.assertRegex(script, r'timeout "\$secs" "\$@" < /dev/null')
        for ln in script.splitlines():
            for tool in ("nmap", "smbclient", "curl", "dig", "lab-kerberoast"):
                if re.match(rf"\s*(/usr/local/bin/)?{tool}\b", ln):
                    self.fail(f"{tool} run outside attack/run_quiet (no timeout): {ln.strip()}")


class ArkimeTemplate(unittest.TestCase):
    TEXT = (TMPL / "arkime.yaml.tmpl").read_text()

    def test_arkime_renders(self):
        """Arkime 6.9.1 (arm64 .deb, SHA-256 pinned) on OpenSearch 3.9.0 (key checked by fingerprint),
        HTTPS viewer on 8005, the analyst tools and the case PCAP library."""
        self.assertNotIn("Stub", self.TEXT)
        lines = provision_script(render(self.TEXT))
        script = "\n".join(lines)
        for needle in ("ARKIME_VERSION=6.9.1", '-1.ubuntu2404_arm64.deb"',
                       "b21fa3bdc3caaed37b1a729461667d70d1faf0fabe32a66f42c72a6e8a62c55e",
                       "A8B2D9E04CD51FEF6AA2DB53BA81D99981191457", "OPENSEARCH_VERSION=3.9.0",
                       "DISABLE_SECURITY_PLUGIN=true", "network.host: 127.0.0.1",
                       "certFile=", "keyFile=", "8005", "tshark", "ngrep", "tcpflow", "zeek/zeek:lts",
                       "arkime_add_user.sh", "initorupgradenoprompt", "/srv/pcaps/cases"):
            with self.subTest(needle=needle):
                self.assertIn(needle, script)
        self.assertGreaterEqual(sum(1 for ln in lines if ln.strip().startswith("lab_step ")), 4)


if __name__ == "__main__":
    unittest.main()
