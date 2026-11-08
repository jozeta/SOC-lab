"""Checks the lab definitions in lib/labs.sh (sourced through a bash 4+).

Run:   python3 -m unittest discover -s tests -v
"""

import pathlib
import re
import subprocess
import unittest

from helpers import BASH

ROOT = pathlib.Path(__file__).resolve().parent.parent
LABS = ["soc", "forensics", "pentest", "devsecops"]
SIZES = ["small", "medium", "large"]


def sh(script):
    """Run `source lib/labs.sh; <script>` and return stdout lines."""
    out = subprocess.run([BASH, "-c", f"source lib/labs.sh; {script}"], cwd=ROOT,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.split("\n")[:-1] if out.stdout else []


def vms(lab, size):
    return sh(f"lab_vms {lab} {size}")


def vm_spec(name):
    return sh(f'echo "${{VM_SPEC[{name}]}}"')[0].split()


def ram_gb(text):
    return int(text.rstrip("G"))


@unittest.skipUnless(BASH, "needs bash 4+")
class Labs(unittest.TestCase):
    def test_lab_names(self):
        self.assertEqual(sh("lab_names"), LABS)

    def test_sizes_are_cumulative(self):
        for lab in LABS:
            small, medium, large = (set(vms(lab, s)) for s in SIZES)
            with self.subTest(lab=lab):
                self.assertTrue(small < medium < large)

    def test_every_vm_has_a_spec_and_role(self):
        for lab in LABS:
            for name in vms(lab, "large"):
                with self.subTest(lab=lab, vm=name):
                    spec = vm_spec("privesc" if name.startswith("privesc") else name)
                    self.assertEqual(len(spec), 4)
                    self.assertTrue((ROOT / "cloud-init-templates" / spec[3]).is_file())
                    self.assertTrue(sh(f"vm_role {name}")[0].strip())

    def test_client_role_true_in_every_lab(self):
        """Pentest and forensics small have no siem or velociraptor: the role must not promise agents."""
        role = sh("vm_role client01")[0]
        self.assertIn("Domain-joined", role)
        for tool in ("Wazuh", "Velociraptor", "SIEM", "DFIR"):
            self.assertNotIn(tool, role)

    def test_clients_per_size(self):
        for lab in LABS:
            self.assertEqual([sh(f"lab_clients {lab} {s}")[0] for s in SIZES], ["1", "3", "6"])

    def test_client_names_follow_the_client_kind(self):
        self.assertEqual(sh("lab_client_names soc small"), ["client01"])
        self.assertEqual(sh("lab_client_names pentest medium"), ["client01", "client02", "client03"])
        self.assertEqual(sh("lab_client_names devsecops small"), ["devclient01"])
        self.assertEqual(len(sh("lab_client_names devsecops large")), 6)
        self.assertEqual(sh("lab_client_names devsecops large")[-1], "devclient06")

    def test_large_fits_48gb(self):
        for lab in LABS:
            with self.subTest(lab=lab):
                self.assertLessEqual(int(sh(f"lab_totals {lab} large")[0].split()[1]), 48)

    def test_soc_lab_contents(self):
        large = vms("soc", "large")
        for gone in ("gitea", "prod", "mgmt"):
            self.assertNotIn(gone, large)

    def test_pentest_has_no_siem(self):
        self.assertNotIn("siem", vms("pentest", "large"))

    def test_devsecops_has_no_dc01(self):
        self.assertNotIn("dc01", vms("devsecops", "large"))

    def test_devsecops_clients_are_developers(self):
        self.assertEqual(sh("lab_client_kind devsecops"), ["developer"])
        for lab in ("soc", "forensics", "pentest"):
            self.assertEqual(sh(f"lab_client_kind {lab}"), ["domain"])

    def test_totals_match_specs(self):
        names = vms("pentest", "small")
        cpus = ram = disk = 0
        for n in names + ["client01"]:
            if n == "client01":
                c, m, d, _ = sh('echo "$CLIENT_SPEC"')[0].split()
            else:
                c, m, d, _ = vm_spec("privesc" if n.startswith("privesc") else n)
            cpus += int(c)
            ram += ram_gb(m)
            disk += int(d.rstrip("G"))
        self.assertEqual(sh("lab_totals pentest small")[0], f"{cpus} {ram} {disk}")

    def test_measured_minutes_override(self):
        self.assertEqual(sh("LAB_MEASURED_MINUTES[pentest:small]=42; lab_estimate_minutes pentest small"), ["42"])
        self.assertNotEqual(sh("lab_estimate_minutes pentest small"), ["42"])

    def test_deploy_order_and_clients_excluded(self):
        order = vms("soc", "large")
        self.assertEqual(order[:3], ["ansible-ctrl", "siem", "dc01"])
        self.assertFalse([n for n in order if re.match(r"(dev)?client", n)])
        self.assertEqual(vms("pentest", "large")[-5:],
                         ["webtarget2", "privesc01", "privesc02", "privesc03", "privesc04"])

    def test_all_lab_vms(self):
        every = sh("all_lab_vms")
        for n in ("dc01", "arkime", "dast", "privesc04", "client20", "devclient01", "devclient20"):
            self.assertIn(n, every)
        for lab in LABS:
            self.assertTrue(set(vms(lab, "large")) <= set(every))

    def test_sourcing_is_side_effect_free(self):
        out = subprocess.run([BASH, "-c", "source lib/labs.sh; echo ok"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
