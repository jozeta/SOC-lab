"""Checks every cloud-init template without deploying a VM:

- every __PLACEHOLDER__ in a template is one that deploy-soc-lab.sh fills in (typos would reach
  the VM as literal text),
- the template, rendered with dummy values, is valid YAML and starts with #cloud-config,
- every shell script it writes (e.g. /root/provision.sh) passes `bash -n` and ShellCheck.

Needs PyYAML and shellcheck:   python3 -m unittest discover -s tests -v
"""

import importlib.util
import pathlib
import random
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

from helpers import BASH

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / "cloud-init-templates").glob("*.yaml.tmpl"))
DEPLOY = (ROOT / "deploy-soc-lab.sh").read_text()
PLACEHOLDER = re.compile(r"__([A-Z0-9_]+)__")


def render(text, **values):
    """What render_template does, with a dummy value for every placeholder not in `values`."""
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), f"dummy-{m.group(1).lower()}"), text)


def provision_script(text):
    """The rendered /root/provision.sh of a template, as lines."""
    doc = yaml.safe_load(text)
    for path, content in shell_scripts(doc):
        if path == "/root/provision.sh":
            return content.splitlines()
    raise AssertionError("no /root/provision.sh")


def assert_guarded(test, lines, var, first, last):
    """`first` .. `last` (substrings of lines) sit inside one `if [ -n "${var}" ]; then` block."""
    guard = f'if [ -n "${{{var}}}" ]; then'
    i_first = next(i for i, ln in enumerate(lines) if first in ln)
    i_last = max(i for i, ln in enumerate(lines) if last in ln)
    opens = [i for i, ln in enumerate(lines[:i_first]) if ln.strip() == guard]
    test.assertTrue(opens, f"no {guard!r} before {first!r}")
    i_open = opens[-1]
    indent = len(lines[i_open]) - len(lines[i_open].lstrip())
    closes = [i for i in range(i_open + 1, len(lines))
              if lines[i].strip() == "fi" and len(lines[i]) - len(lines[i].lstrip()) == indent]
    test.assertTrue(closes and closes[0] > i_last, f"{last!r} is not inside the {guard!r} block")


def load_labdata():
    spec = importlib.util.spec_from_file_location("labdata", ROOT / "seed" / "labdata.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shell_scripts(doc):
    """(path, content) for every write_files entry that is a shell script."""
    for entry in doc.get("write_files") or []:
        content = entry.get("content") or ""
        if entry.get("path", "").endswith(".sh") or content.startswith(("#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/sh")):
            yield entry["path"], content


class Templates(unittest.TestCase):
    def test_there_are_templates(self):
        self.assertGreater(len(TEMPLATES), 10)

    def test_every_placeholder_is_filled_by_the_deploy_script(self):
        for tmpl in TEMPLATES:
            for key in sorted(set(PLACEHOLDER.findall(tmpl.read_text()))):
                with self.subTest(template=tmpl.name, placeholder=key):
                    self.assertIn(f'"{key}=', DEPLOY, f"__{key}__ is never passed to render_template")

    def test_rendered_templates_are_valid_cloud_config(self):
        for tmpl in TEMPLATES:
            with self.subTest(template=tmpl.name):
                text = render(tmpl.read_text())
                self.assertTrue(text.startswith("#cloud-config"), "first line must be #cloud-config")
                doc = yaml.safe_load(text)
                self.assertIsInstance(doc, dict)

    def test_embedded_shell_scripts_parse_and_pass_shellcheck(self):
        checked = 0
        with tempfile.TemporaryDirectory() as tmp:
            for tmpl in TEMPLATES:
                doc = yaml.safe_load(render(tmpl.read_text()))
                for path, content in shell_scripts(doc):
                    script = pathlib.Path(tmp) / f"{tmpl.name}-{pathlib.Path(path).name}"
                    script.write_text(content)
                    with self.subTest(template=tmpl.name, script=path):
                        syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
                        self.assertEqual(syntax.returncode, 0, syntax.stderr)
                        if shutil.which("shellcheck"):
                            lint = subprocess.run(["shellcheck", "-s", "bash", "-S", "warning", str(script)],
                                                  capture_output=True, text=True)
                            self.assertEqual(lint.returncode, 0, lint.stdout[-3000:])
                    checked += 1
        self.assertGreater(checked, 10, "expected a provision script in most templates")

    def test_wazuh_agent_is_optional(self):
        """Labs without siem render WAZUH_MANAGER_IP empty: no agent is installed then."""
        tmpls = [t for t in TEMPLATES if "__WAZUH_MANAGER_IP__" in t.read_text()]
        self.assertGreater(len(tmpls), 10)
        for tmpl in tmpls:
            with self.subTest(template=tmpl.name):
                lines = provision_script(render(tmpl.read_text(), WAZUH_MANAGER_IP=""))
                assert_guarded(self, lines, "WAZUH_MANAGER", "apt-get install -y wazuh-agent",
                               "systemctl enable --now wazuh-agent")

    def test_dc_optional_for_attackers(self):
        """redteam and sensor point DNS at dc01 only when the lab has one."""
        for name in ("redteam", "sensor"):
            with self.subTest(template=name):
                text = (ROOT / "cloud-init-templates" / f"{name}.yaml.tmpl").read_text()
                lines = provision_script(render(text, DC_IP=""))
                assert_guarded(self, lines, "DC_IP", "/etc/systemd/resolved.conf.d/lab-dns.conf",
                               "systemctl restart systemd-resolved")

    def test_no_empty_authorized_key_without_ansible_ctrl(self):
        """Labs without ansible-ctrl have no management key: no empty or placeholder entry."""
        for tmpl in TEMPLATES:
            text = tmpl.read_text()
            if "ssh_authorized_keys" not in text:
                continue
            with self.subTest(template=tmpl.name):
                self.assertNotIn("__ANSIBLE_PUBKEY__", text)
                doc = yaml.safe_load(render(text, ANSIBLE_KEYS="[]"))
                self.assertEqual(doc.get("ssh_authorized_keys"), [])
                doc = yaml.safe_load(render(text, ANSIBLE_KEYS='["ssh-ed25519 AAAAC3Nz ubuntu@ansible-ctrl"]'))
                self.assertEqual(doc.get("ssh_authorized_keys"), ["ssh-ed25519 AAAAC3Nz ubuntu@ansible-ctrl"])
        self.assertIn('ANSIBLE_KEYS="[]"', DEPLOY)


@unittest.skipUnless(BASH, "needs bash 4+")
class LabData(unittest.TestCase):
    """seed/labdata.py in labs that lack some VMs."""

    def lab_ips(self, lab, size):
        out = subprocess.run([BASH, "-c", f"source lib/labs.sh; lab_deploy_plan {lab} {size}"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return {n: f"10.0.0.{i + 2}" for i, n in enumerate(out.stdout.split())}

    def test_labdata_config_only_lab_vms(self):
        labdata = load_labdata()
        ips = self.lab_ips("pentest", "small")
        ros = labdata.roster(1, 1)
        cfg = {"realm": "LAB.LOCAL", "seed_pass": "x", "ips": {**ips, "siem": "", "gitea": None}}
        conf = labdata.activity_config(cfg, ros, "client01")
        self.assertEqual(set(conf["ips"]), set(ips))
        for absent in ("siem", "gitea", "mail", "fs01", "prod", "sensor"):
            self.assertNotIn(absent, conf["targets"])
        self.assertIn("dc01", conf["targets"])
        self.assertIn("vulntarget", conf["targets"])

    def test_staff_visit_only_lab_vms(self):
        labdata = load_labdata()
        ips = self.lab_ips("pentest", "small")
        ros = labdata.roster(1, 1)
        conf = labdata.activity_config({"realm": "LAB.LOCAL", "seed_pass": "x", "ips": ips}, ros, "client01")
        it = {"sam": "a.b", "password": "p", "dept": "IT", "title": "IT Manager", "given": "A", "sn": "B"}
        staff = labdata.Staff(conf, it, random.Random(1))
        visits = {v.__name__ for v in staff.visits()}
        self.assertTrue(visits, "vulntarget is there: Juice Shop and DVWA")
        self.assertLessEqual(visits, {"juice_shop", "dvwa", "ssh_admin"})
        self.assertFalse(staff.available("files"))
        self.assertFalse(staff.available("mail"))

    def test_soc_small_staff_use_sensor(self):
        labdata = load_labdata()
        conf = labdata.activity_config({"realm": "LAB.LOCAL", "seed_pass": "x", "ips": self.lab_ips("soc", "small")},
                                       labdata.roster(1, 1), "client01")
        self.assertEqual(conf["targets"], ["dc01", "sensor", "siem"])


if __name__ == "__main__":
    unittest.main()
