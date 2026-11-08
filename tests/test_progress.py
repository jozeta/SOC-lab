"""Checks lib/progress.sh in plain mode (stdout not a terminal, e.g. a deploy logged to a file).

Run:   python3 -m unittest discover -s tests -v
"""

import pathlib
import subprocess
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


if __name__ == "__main__":
    unittest.main()
