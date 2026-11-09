"""Checks how the deploy, teardown and lab-info scripts use the lab menu, with a fake `multipass`
on PATH (nothing is ever launched). Each test runs scripts copied into a temp directory, so no
.lab-state or .rendered-cloud-init appears in the repo.

Run:   python3 -m unittest discover -s tests -v
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from helpers import BASH

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER = "Name,State,IPv4,Release,Image\n"
FAKE = """#!/bin/sh
echo "$@" >> "$FAKE_MP_LOG"
case "$1" in
    list) printf '%s' "$FAKE_MP_LIST" ;;
esac
exit 0
"""


def listing(*names):
    return HEADER + "".join(f"{n},Running,10.0.0.{i + 2},Ubuntu,24.04\n" for i, n in enumerate(names))


@unittest.skipUnless(BASH, "needs bash 4+")
class DeployFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "lab"
        self.work.mkdir()
        for f in ("deploy-soc-lab.sh", "teardown-soc-lab.sh", "lab-info.sh", "lab-doctor.sh"):
            shutil.copy(ROOT / f, self.work / f)
        shutil.copytree(ROOT / "lib", self.work / "lib")
        bindir = self.tmp / "bin"
        bindir.mkdir()
        fake = bindir / "multipass"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.log = self.tmp / "mp.log"
        self.log.write_text("")
        self.bindir = bindir

    def run_script(self, script, *args, mp_list=HEADER, env=None):
        e = dict(os.environ)
        e.pop("LAB_MENU_TTY", None)
        e.update({"PATH": f"{self.bindir}:{e['PATH']}", "FAKE_MP_LOG": str(self.log),
                  "FAKE_MP_LIST": mp_list, "LAB_HOST_RAM_GB": "64"})
        e.update(env or {})
        return subprocess.run([BASH, str(self.work / script), *args], cwd=self.work, input="",
                              capture_output=True, text=True, env=e, timeout=120)

    def mp_log(self):
        return self.log.read_text()

    def test_refuses_when_a_lab_vm_exists(self):
        r = self.run_script("deploy-soc-lab.sh", "--lab", "soc", "--size", "small", "--yes",
                            mp_list=listing("dc01"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("teardown-soc-lab.sh", r.stdout + r.stderr)
        self.assertNotIn("launch", self.mp_log())

    def write_state(self, lab="pentest", size="small"):
        (self.work / ".lab-state").write_text(f"lab={lab}\nsize={size}\nstarted=2026-11-08T10:00:00Z\n")

    def test_refusal_shows_the_recorded_lab(self):
        self.write_state()
        r = self.run_script("deploy-soc-lab.sh", "--lab", "soc", "--size", "small", "--yes",
                            mp_list=listing("dc01"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Running: Pentest (small), started 2026-11-08T10:00:00Z", r.stdout + r.stderr)

    def test_resume_allowed_with_matching_state(self):
        self.write_state()
        r = self.run_script("deploy-soc-lab.sh", "--lab", "pentest", "--size", "small", "--yes", "--dry-run",
                            mp_list=listing("dc01"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would launch: dc01", r.stdout)

    def test_resume_refused_with_a_different_size(self):
        self.write_state()
        r = self.run_script("deploy-soc-lab.sh", "--lab", "pentest", "--size", "medium", "--yes",
                            mp_list=listing("dc01"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("teardown-soc-lab.sh", r.stdout + r.stderr)

    def test_list_and_help_work_while_a_lab_runs(self):
        for flag in ("--list", "--help"):
            r = self.run_script("deploy-soc-lab.sh", flag, mp_list=listing("dc01"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_script("deploy-soc-lab.sh", "--list", mp_list=listing("dc01"))
        self.assertIn("Pentest", r.stdout)

    def test_refusal_comes_before_the_menu(self):
        r = self.run_script("deploy-soc-lab.sh", mp_list=listing("client01"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Choose a lab", r.stdout)

    def test_dry_run_pentest_small(self):
        r = self.run_script("deploy-soc-lab.sh", "--lab", "pentest", "--size", "small", "--dry-run", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        launched = [line.split()[2] for line in r.stdout.splitlines() if line.startswith("would launch: ")]
        for vm in ("redteam", "vulntarget", "dc01", "client01"):
            self.assertIn(vm, launched)
        self.assertNotIn("siem", launched)
        self.assertEqual(launched.index("dc01") < launched.index("client01"), True)
        self.assertIn("would launch: dc01 (2 vCPU / 4G / 20G)", r.stdout)
        self.assertNotIn("launch", self.mp_log().replace("list", ""))
        self.assertFalse((self.work / ".lab-state").exists())

    def test_dry_run_devsecops_small(self):
        r = self.run_script("deploy-soc-lab.sh", "--lab", "devsecops", "--size", "small", "--dry-run", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        launched = [line.split()[2] for line in r.stdout.splitlines() if line.startswith("would launch: ")]
        for vm in ("gitea", "prod", "devclient01"):
            self.assertIn(vm, launched)
        for vm in ("dc01", "client01"):
            self.assertNotIn(vm, launched)
        self.assertNotIn("launch", self.mp_log().replace("list", ""))

    def test_teardown_removes_all_known_vms(self):
        (self.work / ".lab-state").write_text("lab=pentest\nsize=small\n")
        r = self.run_script("teardown-soc-lab.sh", mp_list=listing("webtarget2", "devclient01", "unrelated"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self.mp_log()
        self.assertIn("delete --purge webtarget2", log)
        self.assertIn("delete --purge devclient01", log)
        self.assertNotIn("unrelated", log)
        self.assertFalse((self.work / ".lab-state").exists())

    def test_state_file_written(self):
        r = self.run_script("deploy-soc-lab.sh", "--lab", "pentest", "--size", "small", "--dry-run", "--yes",
                            env={"LAB_WRITE_STATE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        state = (self.work / ".lab-state").read_text()
        self.assertIn("lab=pentest", state)
        self.assertIn("size=small", state)
        self.assertIn("started=", state)

    def test_lab_info_shows_the_lab_and_dc01_realm(self):
        (self.work / ".lab-state").write_text("lab=pentest\nsize=small\nstarted=2026-11-08T10:00:00Z\n")
        rd = self.work / ".rendered-cloud-init"
        rd.mkdir()
        (rd / "lab.env").write_text("DOMAIN_REALM=LAB.LOCAL\nDOMAIN_NETBIOS=LAB\nADMIN_PASS=x\n"
                                    "SEED_PASS=x\nSVC_SQL_PASS=x\nSVC_LEGACY_PASS=x\n")
        r = self.run_script("lab-info.sh", mp_list=listing("dc01", "webtarget2"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Lab: Pentest (small)", r.stdout)
        self.assertIn("Samba4 AD domain controller: DNS, Kerberos, LDAP (LAB.LOCAL)", r.stdout)
        self.assertIn("WebGoat", r.stdout)
        self.assertIn("OWASP VulnerableApp", r.stdout)
        self.assertNotIn("unknown", r.stdout)

    def test_doctor_checks_only_lab_vms_that_exist(self):
        r = self.run_script("lab-doctor.sh", mp_list=listing("other-instance") + "webtarget2,Stopped,\n")
        self.assertIn("webtarget2", r.stdout)
        self.assertNotIn("other-instance", r.stdout)
        self.assertNotIn("siem", r.stdout)


if __name__ == "__main__":
    unittest.main()
