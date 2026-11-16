"""On wide screens Status puts Health and Storage side by side under the summary; Settings stays one column."""

import re
import unittest

from tests.webjs import WEB

CSS = (WEB / "status.css").read_text(encoding="utf-8")
STATUS = (WEB / "status.js").read_text(encoding="utf-8")
SETTINGS = (WEB / "settings.js").read_text(encoding="utf-8")


def block(source, opening):
    """The rules inside the first block that starts with `opening`."""
    start = source.index(opening) + len(opening)
    depth, end = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(source[end], 0)
        end += 1
    return source[start : end - 1]


class StatusColumns(unittest.TestCase):
    def test_two_columns_only_from_700px_and_in_rem_so_larger_text_keeps_one(self):
        query = re.search(r"@container shell \(min-width: ([\d.]+)rem\) \{", CSS)
        self.assertIsNotNone(query, "the wide rule must be a container query in rem, not px")
        self.assertEqual(float(query.group(1)) * 16, 700)
        wide = block(CSS, query.group(0))
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", wide)

    def test_summary_and_banner_span_the_width(self):
        wide = block(CSS, re.search(r"@container shell \(min-width: [\d.]+rem\) \{", CSS).group(0))
        for part in (".banner", ".status-summary"):
            self.assertIn(f".content.status > {part}", wide)
        self.assertIn("grid-column: 1 / -1", wide)

    def test_status_marks_its_parts(self):
        self.assertIn('class: "content status"', STATUS)
        self.assertIn('class: "section status-summary"', STATUS)
        self.assertNotIn("status-settings", STATUS)
        self.assertNotIn("status-settings", CSS)

    def test_settings_stays_one_readable_column(self):
        self.assertIn('class: "content settings"', SETTINGS)
        self.assertRegex(CSS, r"\.content\.settings \{[^}]*max-width: 40rem")


if __name__ == "__main__":
    unittest.main()
