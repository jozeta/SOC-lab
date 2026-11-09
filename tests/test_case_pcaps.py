"""Checks seed/case-pcaps.txt, the pinned list of public case PCAPs that arkime downloads, and how
the arkime template uses it (nothing is downloaded here).

Run:   python3 -m unittest discover -s tests -v
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIST = ROOT / "seed" / "case-pcaps.txt"
ARKIME = ROOT / "cloud-init-templates" / "arkime.yaml.tmpl"
DEPLOY = (ROOT / "deploy-soc-lab.sh").read_text()


def entries():
    """(sha256, url, zip password, description) for every non-comment line."""
    out = []
    for line in LIST.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(None, 3)
        assert len(parts) == 4, f"needs <sha256> <url> <zip password> <description>: {line!r}"
        out.append(tuple(parts))
    return out


class CasePcaps(unittest.TestCase):
    def test_case_pcaps_list_is_pinned(self):
        """Every line has a 64-hex SHA-256 and an https:// URL, with no duplicates."""
        rows = entries()
        self.assertGreaterEqual(len(rows), 3)
        for sha, url, password, desc in rows:
            with self.subTest(url=url):
                self.assertRegex(sha, r"^[0-9a-f]{64}$")
                self.assertTrue(url.startswith("https://"), url)
                self.assertRegex(url, r"^https://[A-Za-z0-9./_-]+\.zip$")
                self.assertRegex(password, r"^\S+$")
                self.assertTrue(desc.strip())
        self.assertEqual(len({r[0] for r in rows}), len(rows), "duplicate SHA-256")
        self.assertEqual(len({r[1] for r in rows}), len(rows), "duplicate URL")
        self.assertEqual(len({r[1].rsplit("/", 1)[1] for r in rows}), len(rows), "duplicate file name")

    def test_list_reaches_arkime_verified(self):
        """The deploy hands the list to arkime (base64, one source of truth); arkime checks each
        download with sha256sum, deletes a mismatch and logs it, and never unzips."""
        self.assertIn('"CASE_PCAPS_B64=', DEPLOY)
        self.assertIn("seed/case-pcaps.txt", DEPLOY)
        text = ARKIME.read_text()
        self.assertIn("__CASE_PCAPS_B64__", text)
        self.assertIn("/srv/pcaps/cases", text)
        self.assertIn("sha256sum", text)
        self.assertRegex(text, r"rm -f [^\n]*\.part")
        self.assertIn("/var/log/case-pcaps.log", text)
        self.assertNotRegex(text, r"\bunzip -|\b7z [xe]\b")   # the zips stay closed until an analyst opens one


if __name__ == "__main__":
    unittest.main()
