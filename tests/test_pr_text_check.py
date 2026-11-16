"""Tests checks the pull request text: it must not state a test count or result, since the Tests run is the result."""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
STEP = TESTS.split("- name: Check the pull request text", 1)[1].split("- name:", 1)[0]


class TestsTextCheckTests(unittest.TestCase):
    """Runs the step's own shell against the description GitHub holds now (a fake `gh` here)."""

    @classmethod
    def setUpClass(cls):
        cls.script = textwrap.dedent(STEP.split("run: |\n", 1)[1]).strip() + "\n"

    def run_check(self, body):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "gh"
            fake.write_text('#!/bin/sh\ncat "$FAKE_BODY"\n', encoding="utf-8")
            fake.chmod(0o755)
            (Path(folder) / "body.md").write_text(body, encoding="utf-8")
            env = {
                "PATH": f"{folder}:{os.environ['PATH']}",
                "FAKE_BODY": str(Path(folder) / "body.md"),
                "GITHUB_REPOSITORY": "example/usher",
                "NUMBER": "7",
            }
            return subprocess.run(["bash", "-e", "-c", self.script], env=env, capture_output=True, text=True, check=False)

    def test_fails_a_description_that_states_a_test_result_and_says_why(self):
        result = self.run_check("## Summary\n\nA fix.\n\n## How to test\n\nall 100 tests pass\n\nFixes #7\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::", result.stdout)
        self.assertIn("test count or result (line(s) 7)", result.stdout)
        self.assertIn("names the command to run, not what it printed", result.stdout)
        self.assertNotIn("all 100 tests pass", result.stdout)  # only the line number, never the text

    def test_passes_a_description_that_names_only_the_command(self):
        body = "## Summary\n\nAdds three tests.\n\n## How to test\n\n`python3 -m unittest discover -s tests -t .`\n\nFixes #7\n"
        result = self.run_check(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_catches_counts_and_results_only(self):
        for text in (
            "all 100 tests pass",
            "All tests passed.",
            "every test passes",
            "3 new tests fail without the fix",
            "the test suite passes",
            "512 passed, 1 skipped in 3.10s",
            "Ran 531 tests in 28.2s",
            "OK (skipped=1)",
            "FAILED (failures=2)",
        ):
            self.assertEqual(self.run_check(f"## How to test\n\n{text}\n").returncode, 1, text)
        for text in (
            "adds three tests",
            "Adds 2 tests for failed downloads.",
            "`python3 -m unittest discover -s tests -t .`",
            "Once Tests pass, the pull request is squash-merged.",
            "Add a test for every bug fix.",
            "Shows 3 failed downloads on Home.",
        ):
            self.assertEqual(self.run_check(f"## How to test\n\n{text}\n").returncode, 0, text)


class StepTests(unittest.TestCase):
    def test_runs_on_pull_requests_and_when_their_text_is_edited(self):
        self.assertIn("if: github.event_name == 'pull_request'", STEP)
        self.assertRegex(TESTS, r"types: \[[^\]]*\bedited\b")


if __name__ == "__main__":
    unittest.main()
