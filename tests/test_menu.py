"""Checks the lab menu in lib/menu.sh (flags, numbered menu, summary), driven through a bash 4+.

Run:   python3 -m unittest discover -s tests -v
"""

import os
import pathlib
import subprocess
import unittest

from helpers import BASH

ROOT = pathlib.Path(__file__).resolve().parent.parent

DRIVER = ('source lib/labs.sh; source lib/menu.sh; parse_lab_args "$@"; choose_lab; '
          'print_lab_summary "$LAB" "$SIZE"; confirm_lab && echo CONFIRMED')


def run(*args, stdin="", tty=False, env=None):
    e = dict(os.environ)
    e.pop("LAB_MENU_TTY", None)
    if tty:
        e["LAB_MENU_TTY"] = "1"
    e.update(env or {})
    return subprocess.run([BASH, "-c", DRIVER, "_", *args], cwd=ROOT, input=stdin,
                          capture_output=True, text=True, env=e)


def out_of(r):
    return r.stdout + r.stderr


@unittest.skipUnless(BASH, "needs bash 4+")
class MenuTests(unittest.TestCase):
    def test_flags_skip_the_menu(self):
        r = run("--lab", "pentest", "--size", "small", "--yes")
        self.assertEqual(r.returncode, 0, out_of(r))
        self.assertIn("redteam", r.stdout)
        self.assertIn("CONFIRMED", r.stdout)

    def test_bad_lab_value(self):
        r = run("--lab", "foo")
        self.assertEqual(r.returncode, 2)
        self.assertIn("valid labs: soc forensics pentest devsecops", out_of(r))

    def test_bad_size_value(self):
        r = run("--size", "xl")
        self.assertEqual(r.returncode, 2)
        self.assertIn("valid sizes: small medium large", out_of(r))

    def test_missing_value(self):
        self.assertEqual(run("--lab").returncode, 2)
        self.assertEqual(run("--size").returncode, 2)

    def test_unknown_flag(self):
        self.assertEqual(run("--bogus").returncode, 2)

    def test_help(self):
        r = run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--lab", r.stdout)
        self.assertNotIn("CONFIRMED", r.stdout)

    def test_no_terminal_no_flags(self):
        r = run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("--lab", out_of(r))
        self.assertIn("--size", out_of(r))
        self.assertNotIn("CONFIRMED", out_of(r))

    def test_menu_choices(self):
        r = run(stdin="3\n1\ny\n", tty=True)
        self.assertEqual(r.returncode, 0, out_of(r))
        self.assertIn("Pentest", r.stdout)
        self.assertIn("vulntarget", r.stdout)
        self.assertNotIn("webtarget2", r.stdout)
        self.assertIn("CONFIRMED", r.stdout)

    def test_menu_back_and_default_no(self):
        # soc, back to the lab list, forensics, small, Enter at the confirmation = No
        r = run(stdin="1\nb\n2\n1\n\n", tty=True)
        self.assertIn("arkime", r.stdout)
        self.assertNotIn("CONFIRMED", r.stdout)

    def test_back_from_confirm_returns_to_size(self):
        r = run(stdin="3\n1\nb\n3\ny\n", tty=True)
        self.assertIn("mail", r.stdout)
        self.assertIn("CONFIRMED", r.stdout)

    def test_quit_exits_zero_without_deploying(self):
        for text in ("q\n", "1\nq\n", "1\n1\nq\n"):
            r = run(stdin=text, tty=True)
            self.assertEqual(r.returncode, 0, text)
            self.assertNotIn("CONFIRMED", r.stdout)

    def test_end_of_input_does_not_loop(self):
        for text in ("", "1\n", "1\n1\n"):
            r = run(stdin=text, tty=True)
            self.assertNotIn("CONFIRMED", r.stdout)

    def test_invalid_choice_asks_again(self):
        r = run(stdin="9\nx\n3\n1\ny\n", tty=True)
        self.assertIn("CONFIRMED", r.stdout)

    def test_ram_warning(self):
        r = run("--lab", "soc", "--size", "large", env={"LAB_HOST_RAM_GB": "16"}, stdin="\n")
        self.assertIn("WARNING: this lab needs", r.stdout)
        self.assertNotIn("CONFIRMED", r.stdout)

    def test_no_ram_warning_on_big_host(self):
        r = run("--lab", "soc", "--size", "small", "--yes", env={"LAB_HOST_RAM_GB": "64"})
        self.assertNotIn("WARNING", r.stdout)

    def test_summary_lists_every_vm(self):
        r = run("--lab", "forensics", "--size", "medium", "--yes")
        vms = subprocess.run([BASH, "-c", "source lib/labs.sh; lab_vms forensics medium"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
        for name in vms + ["client01", "client02", "client03"]:
            self.assertIn(name, r.stdout)

    def test_developer_clients(self):
        r = run("--lab", "devsecops", "--size", "medium", "--yes")
        self.assertIn("devclient03", r.stdout)

    def test_list_flag(self):
        r = run("--list")
        self.assertEqual(r.returncode, 0, out_of(r))
        for title in ("SOC / blue team", "Network forensics", "Pentest", "DevSecOps CI/CD"):
            self.assertIn(title, r.stdout)
        for size in ("small", "medium", "large"):
            self.assertEqual(r.stdout.count(f"  {size} "), 4, size)
        self.assertNotIn("CONFIRMED", r.stdout)

    def test_flags_set_globals(self):
        script = ('source lib/labs.sh; source lib/menu.sh; parse_lab_args --lab soc --size large --yes --dry-run; '
                  'echo "$LAB $SIZE $ASSUME_YES $LIST_ONLY $DRY_RUN"')
        r = subprocess.run([BASH, "-c", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "soc large 1 0 1")


if __name__ == "__main__":
    unittest.main()
