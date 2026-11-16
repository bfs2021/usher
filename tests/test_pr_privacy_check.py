"""Tests' check for identifying details also reads a pull request's title and description, which end up in the
squash commit. Only the field that matched is named, never the text."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_fork_safety import _privacy_step

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "identifying_details.py"
DETAIL = "Example-Hamlet"  # made up: stands in for a detail the patterns name
PATTERN = "example-hamlet"

# A stand-in for gh: prints the pull request JSON the test wrote, or fails; it records the token it was given
# and the one a python3 started by the step would see (see run_step).
FAKE_GH = """#!/bin/sh
[ -n "$FAKE_GH_FAILS" ] && { echo "boom $PATTERN_LEAK" >&2; exit 1; }
cat "$FAKE_PR"
"""
FAKE_PYTHON = """#!/bin/sh
echo "${GH_TOKEN:-none}" >> "$SEEN_TOKENS"
exec "$REAL_PYTHON" "$@"
"""


def _fields(folder, title, body, patterns=PATTERN):
    base = Path(folder)
    for name, text in (("patterns", patterns + "\n"), ("title", title), ("body", body)):
        (base / name).write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--fields", str(base / "patterns"), str(base / "title"), str(base / "body")],
        capture_output=True,
        text=True,
        check=False,
    )


@unittest.skipUnless(shutil.which("grep"), "needs grep")
class FieldsTests(unittest.TestCase):
    def test_a_clean_title_and_description_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            result = _fields(folder, "feat(app): add a thing", "## Summary\n\nNothing identifying.\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_title_that_matches_is_named_without_its_text(self):
        with tempfile.TemporaryDirectory() as folder:
            result = _fields(folder, f"fix(app): stop {DETAIL} failing", "clean\n")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("pull request title.", output)
        self.assertNotIn(PATTERN, output.lower())

    def test_a_description_that_matches_is_named_without_its_text(self):
        with tempfile.TemporaryDirectory() as folder:
            result = _fields(folder, "fix(app): a clean title", f"## Summary\n\nSeen near {DETAIL.upper()}.\n")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("pull request description.", output)
        self.assertNotIn(PATTERN, output.lower())

    def test_both_fields_are_named_when_both_match(self):
        with tempfile.TemporaryDirectory() as folder:
            result = _fields(folder, DETAIL, DETAIL)
        self.assertEqual(result.returncode, 1)
        self.assertIn("title and description", result.stdout)

    def test_an_invalid_pattern_stops_it_without_quoting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            result = _fields(folder, "clean", "clean", patterns="example-(place")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertNotIn("example-(place", output)
        self.assertEqual(result.stdout, "")
        self.assertIn("isn't a valid extended regular expression", output)

    def test_a_missing_file_or_wrong_arguments_stop_it_with_a_message(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--fields", str(Path(folder) / "p"), str(Path(folder) / "t"), str(Path(folder) / "b")],
                capture_output=True,
                text=True,
                check=False,
            )
            usage = subprocess.run([sys.executable, "-I", str(SCRIPT), "--fields", "p"], capture_output=True, text=True, check=False)
        self.assertEqual((missing.returncode, usage.returncode), (1, 1))
        self.assertIn("couldn't be read", missing.stderr)
        self.assertIn("Usage", usage.stderr)


@unittest.skipUnless(shutil.which("git") and shutil.which("grep"), "needs git and grep")
class StepTests(unittest.TestCase):
    """The workflow step itself, run as the runner runs it, with a fake gh."""

    def run_step(self, title, body, number="12", author="someone", from_fork="false", gh_fails=False, commit_message="init"):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as tools:
            subprocess.run(["git", "init", "-q", folder], check=True)
            (Path(folder) / "notes.txt").write_text("nothing here\n", encoding="utf-8")
            subprocess.run(["git", "-C", folder, "add", "notes.txt"], check=True)
            subprocess.run(
                ["git", "-C", folder, "-c", "user.name=a", "-c", "user.email=a@example.invalid", "commit", "-q", "-m", commit_message],
                check=True,
            )
            (Path(folder) / "fake-pr.json").write_text(json.dumps({"title": title, "body": body}), encoding="utf-8")
            for name, text in (("gh", FAKE_GH), ("python3", FAKE_PYTHON)):
                (Path(tools) / name).write_text(text, encoding="utf-8")
                (Path(tools) / name).chmod(0o755)
            self.seen = Path(folder) / "seen-tokens"
            lines = _privacy_step().split("        run: |\n", 1)[1].splitlines()
            script = "\n".join(line[10:] for line in lines) + "\n"
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-c", script],
                cwd=folder,
                env={
                    "PATH": ":".join([tools, "/usr/bin", "/bin"]),
                    "REAL_PYTHON": sys.executable,
                    "SEEN_TOKENS": str(self.seen),
                    "FAKE_GH_FAILS": "1" if gh_fails else "",
                    "PATTERN_LEAK": PATTERN,
                    "PR_AUTHOR": author,
                    "GITHUB_WORKSPACE": str(ROOT),
                    "GITHUB_REPOSITORY": "example/repo",
                    "RUNNER_TEMP": folder,
                    "GITHUB_STEP_SUMMARY": str(Path(folder) / "summary.md"),
                    "PATTERNS": PATTERN,
                    "FROM_FORK": from_fork,
                    "NUMBER": number,
                    "GH_TOKEN": "made-up",
                    "FAKE_PR": str(Path(folder) / "fake-pr.json"),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            result.tokens = self.seen.read_text(encoding="utf-8").split() if self.seen.exists() else []
            return result

    def test_a_pull_request_without_a_match_passes(self):
        result = self.run_step("feat(app): add a thing", "## Summary\n\nFine.\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No identifying details found", result.stdout)

    def test_a_matching_title_fails_naming_the_title(self):
        result = self.run_step(f"feat(app): {DETAIL}", "fine\n")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("::error::Identifying details found in the pull request title.", output)
        self.assertNotIn(PATTERN, output.lower())
        self.assertNotIn("No identifying details found (", output)

    def test_a_matching_description_fails_naming_the_description(self):
        result = self.run_step("feat(app): add a thing", f"## Summary\n\n{DETAIL}\n")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("::error::Identifying details found in the pull request description.", output)
        self.assertNotIn(PATTERN, output.lower())

    def test_a_run_without_a_pull_request_reads_no_text(self):
        # A push to main has no pull request; a fake gh that would match shows it is never asked.
        result = self.run_step(DETAIL, DETAIL, number="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_run_on_main_checks_the_newest_commit_message(self):
        result = self.run_step("x", "x", number="", commit_message=f"feat(app): {DETAIL}")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("::error::Identifying details found in the newest commit's message.", output)
        self.assertNotIn(PATTERN, output.lower())

    def test_a_pull_request_run_does_not_read_the_merge_commit_message(self):
        result = self.run_step("feat(app): add a thing", "fine\n", commit_message=DETAIL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_token_never_reaches_the_pull_requests_own_script(self):
        result = self.run_step("feat(app): add a thing", "fine\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.tokens)
        self.assertEqual(set(result.tokens), {"none"})

    def test_a_failed_read_fails_the_step_instead_of_passing_unchecked(self):
        result = self.run_step(DETAIL, DETAIL, gh_fails=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("could not be read, so they were not checked", output)
        self.assertNotIn("No identifying details found (", output)
        self.assertNotIn(PATTERN, output.lower())

    def test_an_empty_description_and_crlf_text_are_handled(self):
        self.assertEqual(self.run_step("feat(app): add a thing", "").returncode, 0)
        result = self.run_step("feat(app): add a thing", f"## Summary\r\n\r\n{DETAIL}\r\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("description", result.stdout)

    def test_a_title_starting_with_a_dash_is_text_not_an_option(self):
        result = self.run_step(f"--{DETAIL}", "fine\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pull request title", result.stdout)

    def test_a_fork_reads_no_text(self):
        result = self.run_step(DETAIL, DETAIL, from_fork="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.tokens, [])

    def test_dependabots_pull_request_is_skipped_whoever_edits_it(self):
        result = self.run_step(DETAIL, DETAIL, author="dependabot[bot]")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
