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

    def test_wazuh_key_is_checked(self):
        """The Wazuh apt key is imported only if its fingerprint is Wazuh's (as pinned by Wazuh's own installer)."""
        tmpls = [t for t in TEMPLATES if "GPG-KEY-WAZUH" in t.read_text()]
        self.assertGreater(len(tmpls), 10)
        for tmpl in tmpls:
            with self.subTest(template=tmpl.name):
                script = "\n".join(provision_script(render(tmpl.read_text())))
                self.assertNotIn("GPG-KEY-WAZUH | gpg", script, "a key piped into gpg is never checked")
                self.assertIn("curl -fsSL --retry 5", script)
                self.assertIn('= 0DCFCA5547B19D2A6099506096B3EE5F29111145 ]', script)
                self.assertLess(script.index("0DCFCA5547B19D2A6099506096B3EE5F29111145"),
                                script.index("--import /tmp/wazuh.key"), "check before import")

    def test_dc_optional_for_attackers(self):
        """redteam and sensor point DNS at dc01 only when the lab has one."""
        for name in ("redteam", "sensor"):
            with self.subTest(template=name):
                text = (ROOT / "cloud-init-templates" / f"{name}.yaml.tmpl").read_text()
                lines = provision_script(render(text, DC_IP=""))
                assert_guarded(self, lines, "DC_IP", "/etc/systemd/resolved.conf.d/lab-dns.conf",
                               "systemctl restart systemd-resolved")

    def test_kali_tools_flag(self):
        """redteam installs the Kali tool set only when the deploy renders KALI_TOOLS=1 (pentest)."""
        text = (ROOT / "cloud-init-templates" / "redteam.yaml.tmpl").read_text()
        self.assertIn('"KALI_TOOLS=$(lab_kali_tools "${LAB}")"', DEPLOY)
        on = provision_script(render(text, KALI_TOOLS="1"))
        script = "\n".join(on)
        self.assertIn('KALI_TOOLS="1"', script)
        self.assertIn('KALI_TOOLS="0"', "\n".join(provision_script(render(text, KALI_TOOLS="0"))))
        for needle in ("apt-get install -y metasploit-framework", "https://apt.metasploit.com/",
                       "BURP_VERSION=2026.10.1", "type=linuxarm64",
                       "1d8995cf82ca045c4f585a9cebaf3d18f4a073557a0fe05276d4cf6c69fa7478",
                       "/usr/local/bin/burpsuite", "/home/ubuntu/.msf4/initial_setup_complete", "sqlmap", "john", "ffuf", "nikto", "dirb",
                       "SecLists", "Responder", "enum4linux-ng"):
            self.assertIn(needle, script)
        for tag in ("SECLISTS_TAG=2026.2", "RESPONDER_TAG=v3.2.2.0", "ENUM4LINUX_NG_TAG=v1.3.10"):
            self.assertIn(tag, script)
        guard = 'if [ "${KALI_TOOLS}" = "1" ]; then'
        i_open = on.index(next(ln for ln in on if ln.strip() == guard))
        indent = len(on[i_open]) - len(on[i_open].lstrip())
        i_close = next(i for i in range(i_open + 1, len(on))
                       if on[i].strip() == "fi" and len(on[i]) - len(on[i].lstrip()) == indent)
        block = "\n".join(on[i_open:i_close])
        outside = "\n".join(on[:i_open] + on[i_close:])
        for needle in ("metasploit", "burp", "sqlmap", "nikto", "SecLists", "Responder", "enum4linux-ng"):
            with self.subTest(tool=needle):
                self.assertIn(needle, block)
                self.assertNotIn(needle, outside)

    def test_webtarget2_renders(self):
        """webtarget2 runs WebGoat and OWASP VulnerableApp from pinned, multi-arch images."""
        text = (ROOT / "cloud-init-templates" / "webtarget2.yaml.tmpl").read_text()
        self.assertNotIn("Stub", text)
        lines = provision_script(render(text))
        script = "\n".join(lines)
        # Pinned by digest (the multi-arch index): a re-pushed tag can't change what runs.
        for image in ("webgoat/webgoat:v2026.5@sha256:95c23e70502d0e0e11a826a73889e2ca93f965171202c17c0c59353ba67be86a",
                      "sasanlabs/owasp-vulnerableapp:2.2.54@sha256:001d5b76b29d52989ece3a176deda23988e3fee45be28a3015e813fafe9e892b"):
            self.assertIn(image, script)
        self.assertNotIn(":latest", script)
        runs = [ln for ln in lines if "docker run" in ln]
        self.assertEqual(len(runs), 2)
        for run in runs:
            self.assertIn("--restart unless-stopped", run)
        for port in ("-p 8080:8080", "-p 9090:9090", "-p 9091:9090"):
            self.assertIn(port, script)
        self.assertTrue(any(ln.strip().startswith("lab_step ") for ln in lines))

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


TMPL = ROOT / "cloud-init-templates"
# Pinned DevSecOps tools: version and the sha256 / image digest checked on the VM.
DEVSECOPS_PINS = {
    "gitea": ["GITLEAKS_VERSION=8.30.1", "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
              "OSV_VERSION=2.7.0", "4061ef4c021ed3c47f203b818f337e351336cc5cd67c0274fc36bd1dfc728dd7",
              "TRIVY_VERSION=0.76.1", "e174dff2a6dba5e3ae41f8d0537a6324405bd08132497340304c6b20e0f16a1f",
              "COSIGN_VERSION=3.1.4", "ade83b7aac62e131cacfe96d958948b2150cc9efec46a570a2649b8e27dedad2",
              "semgrep/semgrep:1.182.0@sha256:4a34048c04fef049f71665047f074cffb00cc3d89482f965a5cd151d589237af",
              "SEMGREP_RULES_COMMIT=16b9e6d45f10649f6a531ec4838ed9924372c3bc"],
    "prod": ["COSIGN_VERSION=3.1.4", "ade83b7aac62e131cacfe96d958948b2150cc9efec46a570a2649b8e27dedad2"],
    "devclient": ["TEA_VERSION=0.16.0", "0e14a036322eeb1fc55e266acafeb8490e523ed99f9232034ae74861e2339879"],
    "dast": ["zaproxy/zap-stable:2.16.1@sha256:7840969c7c9fead565bf9734b12f49f6886db90b1d35b1f74d79710bbd081dab"],
}


def written_file(text, path):
    """The content of one write_files entry of a rendered template."""
    doc = yaml.safe_load(text)
    for entry in doc.get("write_files") or []:
        if entry["path"] == path:
            return entry["content"]
    raise AssertionError(f"no {path}")


def flag_block(lines, var):
    """(blocks, outside): every `if [ "${var}" = "1" ]; then` ... matching fi/else, and all other lines."""
    guard = f'if [ "${{{var}}}" = "1" ]; then'
    inside = set()
    for i_open, ln in enumerate(lines):
        if ln.strip() != guard:
            continue
        indent = len(ln) - len(ln.lstrip())
        i_close = next(i for i in range(i_open + 1, len(lines))
                       if lines[i].strip() in ("fi", "else") and len(lines[i]) - len(lines[i].lstrip()) == indent)
        inside.update(range(i_open, i_close))
    assert inside, f"no {guard!r}"
    return ("\n".join(ln for i, ln in enumerate(lines) if i in inside),
            "\n".join(ln for i, ln in enumerate(lines) if i not in inside))


class DevSecOps(unittest.TestCase):
    """The DevSecOps lab: developer clients, dast, the scanners on gitea and the signature check on prod."""

    def test_devclient_renders_without_dc(self):
        text = (TMPL / "devclient.yaml.tmpl").read_text()
        self.assertNotIn("Stub", text)
        self.assertNotIn("__DC_IP__", text)
        self.assertNotIn("realm join", text)
        self.assertNotIn("sssd", text)
        lines = provision_script(render(text, HOSTNAME="devclient01"))
        script = "\n".join(lines)
        for needle in DEVSECOPS_PINS["devclient"] + ["sha256sum -c", "apt-get install -y git"]:
            self.assertIn(needle, script)
        self.assertNotIn("gitea", script.lower().replace("dl.gitea.com", ""),
                         "provisioning must not need gitea (it may not exist yet)")
        self.assertTrue(any(ln.strip().startswith("lab_step ") for ln in lines))
        self.assertEqual(yaml.safe_load(render(text, HOSTNAME="devclient02"))["hostname"], "devclient02")

    def test_dast_renders(self):
        text = (TMPL / "dast.yaml.tmpl").read_text()
        self.assertNotIn("Stub", text)
        rendered = render(text)
        script = "\n".join(provision_script(rendered))
        for needle in DEVSECOPS_PINS["dast"]:
            self.assertIn(needle, script)
        self.assertNotIn(":latest", rendered)
        scan = written_file(rendered, "/usr/local/sbin/lab-zap-scan")
        self.assertIn("zap-baseline.py", scan)
        self.assertIn("timeout ", scan)
        self.assertIn("report.html", scan)
        self.assertIn("report.json", scan)
        # A timeout kills the docker client, not the container: the scan names its container and
        # removes it, and only a scan that produced a report becomes "latest".
        self.assertIn('--name "${name}"', scan)
        self.assertIn('docker rm -f "${name}"', scan)
        self.assertEqual(scan.count("ln -sfn"), 1)
        self.assertLess(scan.index('if [ -s "${dir}/report.json" ]'), scan.index("ln -sfn"))
        timer = written_file(rendered, "/etc/systemd/system/lab-zap.timer")
        self.assertIn("OnUnitActiveSec=", timer)
        self.assertIn("systemctl enable lab-zap.timer", script)
        wire = written_file(rendered, "/root/dast-wire.sh")
        self.assertIn("/etc/lab-dast.conf", wire)

    def test_devsecops_workflow_has_scanners(self):
        labdata = load_labdata()
        wf = labdata.DEVSECOPS_PIPELINE
        for tool in ("semgrep", "gitleaks", "osv-scanner", "trivy", "cosign"):
            self.assertIn(tool, wf)
        doc = yaml.safe_load(wf)
        jobs = doc["jobs"]
        self.assertIn("policy-gate", jobs)
        self.assertEqual(jobs["deploy"]["needs"], "policy-gate")
        self.assertIn("build", jobs["policy-gate"]["needs"])
        build_needs = jobs["build"]["needs"]
        for gate in ("secret-scan", "sast", "dependency-scan", "iac-scan"):
            self.assertIn(gate, build_needs)
        self.assertIn("cosign sign", wf)
        self.assertIn("lab-policy", wf)
        deploy = "".join(s.get("run", "") for s in jobs["deploy"]["steps"])
        self.assertIn('REF=$(lab-policy ', deploy, "deploy the digest the policy gate passed")
        self.assertIn('"$REF"', deploy)

    def test_semgrep_rules_are_pinned_and_local(self):
        """sast scans with semgrep-rules at a pinned commit, mounted read-only, not the live registry."""
        sast = "".join(s.get("run", "") for s in yaml.safe_load(load_labdata().DEVSECOPS_PIPELINE)["jobs"]["sast"]["steps"])
        self.assertIn("--config /rules/python", sast)
        self.assertIn("--config /rules/dockerfile", sast)
        self.assertNotIn("p/", sast, "registry rule sets change under the lab")
        script = "\n".join(provision_script(render((TMPL / "gitea.yaml.tmpl").read_text())))
        guarded, _ = flag_block(script.splitlines(), "DEVSECOPS")
        self.assertIn("semgrep/semgrep-rules.git", guarded)
        self.assertIn('rev-parse HEAD)" = "${SEMGREP_RULES_COMMIT}"', guarded)
        self.assertIn("-v /opt/semgrep-rules:/rules:ro", guarded)

    def test_devsecops_planted_issues(self):
        """The demo repo's history plants the exercises the README lists."""
        labdata = load_labdata()
        commits = labdata.devsecops_commits(random.Random(3))
        tree, added_secret_in, removed_in = {}, None, None
        key = re.compile(r"AKIA[0-9A-Z]{16}")
        for i, (msg, files, deleted) in enumerate(commits):
            self.assertTrue(msg)
            for path, body in files.items():
                if key.search(body):
                    added_secret_in = (i, path)
                tree[path] = body
            for path in deleted:
                if added_secret_in and path == added_secret_in[1]:
                    removed_in = i
                del tree[path]
        self.assertIsNotNone(added_secret_in, "a commit adds an AWS-style key")
        self.assertIsNotNone(removed_in, "a later commit removes it again")
        self.assertGreater(removed_in, added_secret_in[0])
        self.assertFalse(any(key.search(b) for b in tree.values()), "the secret is only in history")
        self.assertNotEqual(labdata.devsecops_commits(random.Random(4))[added_secret_in[0]][1],
                            commits[added_secret_in[0]][1], "the key is random per lab")
        self.assertIn("requests==2.31.0", tree["requirements.txt"])
        self.assertEqual(tree[".gitea/workflows/pipeline.yml"], labdata.DEVSECOPS_PIPELINE)
        prt = yaml.safe_load(tree[".gitea/workflows/pr-preview.yml"])
        self.assertIn("pull_request_target", prt[True] if True in prt else prt["on"])
        self.assertIn("secrets.REGISTRY_TOKEN", tree[".gitea/workflows/pr-preview.yml"])
        self.assertIn("scripts/preview.sh", tree)
        uses = re.findall(r"uses: (\S+)", tree[".gitea/workflows/pr-checks.yml"])
        self.assertTrue(uses)
        for ref in uses:
            self.assertNotRegex(ref, r"@[0-9a-f]{40}$", "planted: actions pinned to tags, not commits")
        for path, body in tree.items():
            if path.endswith((".yml", ".yaml")):
                yaml.safe_load(body)

    def test_devsecops_flag_on_gitea_and_prod(self):
        self.assertIn('"DEVSECOPS=$(lab_devsecops "${LAB}")"', DEPLOY)
        gitea = (TMPL / "gitea.yaml.tmpl").read_text()
        lines = provision_script(render(gitea, DEVSECOPS="1"))
        block, outside = flag_block(lines, "DEVSECOPS")
        self.assertGreaterEqual(block.count("sha256sum -c"), 1)
        for needle in DEVSECOPS_PINS["gitea"] + ["generate-key-pair", "COSIGN_KEY", "signing-config create",
                                                 "install -m 755 /opt/cicd/devsecops/lab-policy"]:
            with self.subTest(gitea=needle):
                self.assertIn(needle, block)
                self.assertNotIn(needle, outside)
        policy = written_file(render(gitea, DEVSECOPS="1"), "/opt/cicd/devsecops/lab-policy")
        self.assertIn("cosign verify", policy)
        prod = (TMPL / "prod.yaml.tmpl").read_text()
        lines = provision_script(render(prod, DEVSECOPS="1"))
        block, outside = flag_block(lines, "DEVSECOPS")
        for needle in DEVSECOPS_PINS["prod"] + ["sha256sum -c"]:
            with self.subTest(prod=needle):
                self.assertIn(needle, block)
                self.assertNotIn(needle, outside)
        deploy = written_file(render(prod, DEVSECOPS="1"), "/usr/local/bin/lab-deploy").splitlines()
        block, outside = flag_block(deploy, "DEVSECOPS")
        self.assertIn("cosign verify", block)
        self.assertIn("deploy_rejected", block)
        self.assertNotIn("cosign", outside)

    def test_devsecops_seeding_needs_no_dc01(self):
        """Stage 6 seeds the DevSecOps repo, the release, dast and the developer clients itself."""
        self.assertIn("seed_devsecops", DEPLOY)
        body = DEPLOY[DEPLOY.index("seed_devsecops() {"):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("dc01", body)
        for needle in ("seed_vm gitea devsecops", "/root/cicd-release.sh", "/root/dast-wire.sh",
                       "seed_vm \"${name}\" devclient"):
            self.assertIn(needle, body)
        labdata = load_labdata()
        self.assertEqual(labdata.dev_user("devclient03"), "dev03")
        source = (ROOT / "seed" / "labdata.py").read_text()
        self.assertIn('"devsecops": role_devsecops', source)


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
