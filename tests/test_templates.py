"""Checks every cloud-init template without deploying a VM:

- every __PLACEHOLDER__ in a template is one that deploy-soc-lab.sh fills in (typos would reach
  the VM as literal text),
- the template, rendered with dummy values, is valid YAML and starts with #cloud-config,
- every shell script it writes (e.g. /root/provision.sh) passes `bash -n` and ShellCheck.

Needs PyYAML and shellcheck:   python3 -m unittest discover -s tests -v
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / "cloud-init-templates").glob("*.yaml.tmpl"))
DEPLOY = (ROOT / "deploy-soc-lab.sh").read_text()
PLACEHOLDER = re.compile(r"__([A-Z0-9_]+)__")


def render(text):
    """What render_template does, with a dummy value for every placeholder."""
    return PLACEHOLDER.sub(lambda m: f"dummy-{m.group(1).lower()}", text)


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


if __name__ == "__main__":
    unittest.main()
