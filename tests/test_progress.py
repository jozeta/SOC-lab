"""Checks lib/progress.sh in plain mode (stdout not a terminal, e.g. a deploy logged to a file).

Run:   python3 -m unittest discover -s tests -v
"""

import pathlib
import subprocess
import tempfile
import unittest

from helpers import BASH

ROOT = pathlib.Path(__file__).resolve().parent.parent


@unittest.skipUnless(BASH, "needs bash 4+")
class PlainProgress(unittest.TestCase):
    def test_finish_prints_the_elapsed_time_in_plain_mode(self):
        """A logged deploy must still end with 'Deployed in <time>' (the number to copy into lib/labs.sh)."""
        out = subprocess.run([BASH, "-c", 'source lib/progress.sh; progress_finish "Deployed in $(pg_elapsed)"'],
                             cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertRegex(out.stdout, r"Deployed in \d+m\d\ds")


FIXTURE = """\
      KALI_TOOLS="0"
      WAZUH_MANAGER=""
      DEVSECOPS="1"
      lab_step "always"
      if [ "${KALI_TOOLS}" = "1" ]; then
          lab_step "kali only"
          if [ -n "${WAZUH_MANAGER}" ]; then
              lab_step "kali and wazuh"
          fi
      else
          lab_step "no kali"
      fi
      if [ -n "${WAZUH_MANAGER}" ]; then
          lab_step "wazuh agent"
      else
          echo "no siem"
      fi
      if [ "${DEVSECOPS}" = "1" ]; then
          lab_step "scanners"
      fi
      if grep -q x /etc/hosts; then
          lab_step "unknown guard: counted"
      fi
"""


@unittest.skipUnless(BASH, "needs bash 4+")
class StepCount(unittest.TestCase):
    """The step total counts only lab_step lines that will run with this VM's flags."""

    def count(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as f:
            f.write(text)
            f.flush()
            out = subprocess.run([BASH, "-c", 'source lib/progress.sh; pg_count_steps "$1"', "_", f.name],
                                 cwd=ROOT, capture_output=True, text=True, check=True)
        return int(out.stdout.strip())

    def test_guarded_steps_that_wont_run_are_not_counted(self):
        # always, no kali, scanners, unknown guard
        self.assertEqual(self.count(FIXTURE), 4)

    def test_flags_on_count_their_steps(self):
        on = FIXTURE.replace('KALI_TOOLS="0"', 'KALI_TOOLS="1"').replace('WAZUH_MANAGER=""', 'WAZUH_MANAGER="10.0.0.2"')
        # always, kali only, kali and wazuh, wazuh agent, scanners, unknown guard
        self.assertEqual(self.count(on), 6)

    def test_placeholders_are_unknown(self):
        """Before rendering (VMs are registered first), __FLAG__ values count every step, as before."""
        raw = FIXTURE.replace('KALI_TOOLS="0"', 'KALI_TOOLS="__KALI_TOOLS__"').replace(
            'WAZUH_MANAGER=""', 'WAZUH_MANAGER="__WAZUH_MANAGER_IP__"')
        # always, kali only, kali and wazuh, no kali, wazuh agent, scanners, unknown guard
        self.assertEqual(self.count(raw), 7)

    def test_rendering_recounts_the_vm(self):
        """The total registered from the raw template is corrected once the VM's file is rendered."""
        raw = FIXTURE.replace('KALI_TOOLS="0"', 'KALI_TOOLS="__KALI_TOOLS__"')
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "redteam.tmpl").write_text(raw)
            (pathlib.Path(d) / "redteam.yaml").write_text(FIXTURE)
            out = subprocess.run([BASH, "-c", 'source lib/progress.sh; progress_add_vm redteam "$1/redteam.tmpl"; '
                                  'a=${PG_STEPS[redteam]}; progress_recount redteam "$1/redteam.yaml"; '
                                  'progress_recount nosuchvm "$1/redteam.yaml"; echo "$a ${PG_STEPS[redteam]} ${#PG_VMS[@]}"',
                                  "_", d], cwd=ROOT, capture_output=True, text=True, check=True)
        # raw: always, kali only, no kali (kali unknown: both branches), scanners, unknown guard
        self.assertEqual(out.stdout.split(), ["5", "4", "1"])

    def test_render_template_recounts(self):
        deploy = (ROOT / "deploy-soc-lab.sh").read_text()
        body = deploy[deploy.index("render_template() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('progress_recount "$(basename "${output}" .yaml)" "${output}"', body)

    def test_no_steps_is_zero(self):
        self.assertEqual(self.count("echo hi\n"), 0)


if __name__ == "__main__":
    unittest.main()
