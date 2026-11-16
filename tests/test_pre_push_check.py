"""Identifying details are caught before a push publishes them: the pre-push hook in .githooks/ runs
tools/identifying_details.py on every commit the remote doesn't have yet, naming only commit, file and line, and
never a path that itself matches."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "identifying_details.py"
HOOK = ROOT / ".githooks" / "pre-push"
SECRET_VALUE = "Example-Hamlet"  # made up: stands in for a detail the patterns name
PATTERN = "example-hamlet"


@unittest.skipUnless(shutil.which("git") and shutil.which("grep"), "needs git and grep")
class PrePushTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PRIVACY_PATTERNS", "GIT_"))}
        self.env.update(HOME=str(self.home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.remote = self.base / "remote.git"
        self.git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.repo = self.base / "work"
        self.git(self.base, "init", "-q", "-b", "main", str(self.repo))
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.commit("README.txt", "made-up project\n", "start")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "config", "core.hooksPath", str(HOOK.parent))
        self.git(self.repo, "switch", "-q", "-c", "topic")

    def git(self, cwd, *args, check=True):
        return subprocess.run(
            ["git", "-c", "user.name=Example", "-c", "user.email=example@example.invalid", *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=check,
        )

    def commit(self, name, text, message):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git(self.repo, "add", "--", f"./{name}")
        self.git(self.repo, "commit", "-q", "-m", message)
        return self.git(self.repo, "rev-parse", "--short=7", "HEAD").stdout.strip()

    def use_patterns(self, text):
        self.env["PRIVACY_PATTERNS"] = text

    def push(self):
        result = self.git(self.repo, "push", "origin", "topic", check=False)
        return result, result.stdout + result.stderr

    def remote_has_topic(self):
        return self.git(self.remote, "rev-parse", "--verify", "-q", "refs/heads/topic", check=False).returncode == 0

    def test_a_commit_with_a_detail_is_stopped_naming_only_file_and_line(self):
        self.use_patterns(f"another-place\r\n\n{PATTERN}\n")
        commit = self.commit("notes/today.txt", f"nothing here\nnear {SECRET_VALUE}\n", "add notes")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertFalse(self.remote_has_topic())
        self.assertIn(f"{commit} notes/today.txt:2", output)
        for private in (SECRET_VALUE, PATTERN, "another-place"):
            self.assertNotIn(private.lower(), output.lower())

    def test_a_clean_commit_is_pushed(self):
        self.use_patterns(PATTERN)
        self.commit("notes.txt", "nothing identifying\n", "add notes")
        result, output = self.push()
        self.assertEqual(result.returncode, 0, output)
        self.assertTrue(self.remote_has_topic())
        self.assertIn("No identifying details in 1 commit(s)", output)

    def test_a_detail_removed_by_a_later_commit_still_stops_the_push(self):
        # refs/pull/* keeps every commit of a pull request, so each one counts, not only the last tree.
        self.use_patterns(PATTERN)
        first = self.commit("notes.txt", f"{SECRET_VALUE}\n", "add notes")
        self.commit("notes.txt", "gone\n", "tidy notes")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(f"{first} notes.txt:1", output)

    def test_a_detail_in_a_commit_message_stops_the_push(self):
        self.use_patterns(PATTERN)
        commit = self.commit("notes.txt", "nothing\n", f"add notes\n\nseen at {SECRET_VALUE}")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(f"{commit} commit message, line 3", output)
        self.assertNotIn(SECRET_VALUE.lower(), output.lower())

    def test_commits_the_remote_already_has_are_not_checked_again(self):
        self.use_patterns(PATTERN)
        self.commit("notes.txt", "nothing\n", "add notes")
        self.assertEqual(self.push()[0].returncode, 0)
        self.commit("more.txt", "still nothing\n", "add more")
        result, output = self.push()
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("in 1 commit(s)", output)

    def test_without_patterns_the_push_is_stopped_with_how_to_provide_them(self):
        self.commit("notes.txt", "nothing\n", "add notes")
        for patterns in (None, " \n"):
            with self.subTest(patterns=patterns):
                if patterns is not None:
                    self.use_patterns(patterns)
                result, output = self.push()
                self.assertNotEqual(result.returncode, 0, output)
                self.assertFalse(self.remote_has_topic())
                for words in ("stopped", "PRIVACY_PATTERNS", "~/.config/usher/privacy-patterns", "CONTRIBUTING.md"):
                    self.assertIn(words, output)

    def test_patterns_come_from_a_file_outside_the_repository(self):
        default = self.home / ".config" / "usher" / "privacy-patterns"
        default.parent.mkdir(parents=True)
        default.write_text(PATTERN + "\n", encoding="utf-8")
        self.commit("notes.txt", f"{SECRET_VALUE}\n", "add notes")
        self.assertNotEqual(self.push()[0].returncode, 0)
        named = self.base / "elsewhere"
        named.write_text("nothing-matches-this\n", encoding="utf-8")
        self.env["PRIVACY_PATTERNS_FILE"] = str(named)
        self.assertEqual(self.push()[0].returncode, 0)

    def test_a_patterns_file_inside_the_repository_is_refused(self):
        inside = self.repo / "patterns.txt"
        inside.write_text("nothing-matches-this\n", encoding="utf-8")
        self.env["PRIVACY_PATTERNS_FILE"] = str(inside)
        self.commit("notes.txt", "nothing\n", "add notes")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("inside the repository", output)

    def test_an_invalid_pattern_stops_the_push_without_quoting_it(self):
        self.use_patterns("example-(hamlet")
        self.commit("notes.txt", "nothing\n", "add notes")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("isn't a valid extended regular expression", output)
        self.assertNotIn("hamlet", output.lower())

    def test_an_invalid_pattern_is_named_by_its_line_alone(self):
        # Line 4 counts the blank line, as the patterns are written. git grep rejects "a{1" though grep accepts it.
        for bad in ("example-(hamlet", "example-hamlet{1", "example-[[:hamlet:]]"):
            with self.subTest(bad=bad):
                self.use_patterns(f"another-place\r\n\n{PATTERN}\n{bad}\n")
                result = subprocess.run(["python3", str(SCRIPT)], cwd=self.repo, env=self.env, capture_output=True, text=True, check=False)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("Line 4 of the patterns isn't a valid extended regular expression", output)
                self.assertNotIn("hamlet", output.lower())

    def test_check_patterns_alone_names_the_line_without_quoting_it(self):
        patterns = self.base / "patterns"
        patterns.write_text(f"{PATTERN}\nexample-(hamlet\n", encoding="utf-8")
        result = subprocess.run(["python3", str(SCRIPT), "--check-patterns", str(patterns)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Line 2 of the patterns", result.stderr)
        self.assertNotIn("hamlet", result.stderr.lower())
        patterns.write_text(f"{PATTERN}\n\nanother-place\n", encoding="utf-8")
        result = subprocess.run(["python3", str(SCRIPT), "--check-patterns", str(patterns)], env=self.env, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_a_path_that_matches_is_counted_but_never_printed(self):
        self.use_patterns(PATTERN)
        commit = self.commit(f"notes/{SECRET_VALUE}.txt", f"{SECRET_VALUE}\nplain\n", "add notes")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(f"{commit} 1 file path(s) that match", output)
        self.assertIn(f"{commit} a file whose path isn't shown", output)
        self.assertNotIn(PATTERN, output.lower())

    def test_a_path_with_a_colon_or_a_line_break_is_handled(self):
        self.use_patterns(PATTERN)
        commit = self.commit("a:b.txt", f"x\n{SECRET_VALUE}\n", "add notes")
        self.commit("new\nline.txt", f"{SECRET_VALUE}\n", "add more")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(f"{commit} a:b.txt:2", output)
        self.assertIn("holds a line break", output)
        self.assertNotIn("Traceback", output)

    def tree(self, patterns_text):
        patterns = self.base / "patterns"
        patterns.write_text(patterns_text, encoding="utf-8")
        result = subprocess.run(["python3", str(SCRIPT), "--tree", str(patterns)], cwd=self.repo, env=self.env, capture_output=True, text=True, check=False)
        return result, result.stdout + result.stderr

    def test_tree_mode_lists_matches_and_hides_a_matching_path(self):
        self.commit(f"{SECRET_VALUE}.txt", "plain\n", "add a")
        self.commit("notes.txt", f"x\n{SECRET_VALUE}\n", "add b")
        result, output = self.tree(PATTERN + "\n")
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("  notes.txt:2", output)
        self.assertIn("1 file path(s) that match", output)
        self.assertNotIn(SECRET_VALUE.lower(), output.lower())

    def test_tree_mode_handles_colon_and_line_break_paths_and_indents_every_path(self):
        self.commit("a:b.txt", f"x\n{SECRET_VALUE}\n", "add a")
        self.commit("::error::x.txt", f"{SECRET_VALUE}\n", "add b")
        self.commit("new\nline.txt", f"{SECRET_VALUE}\n", "add c")
        result, output = self.tree(PATTERN + "\n")
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("  a:b.txt:2", output)
        self.assertIn("holds a line break", output)
        self.assertNotIn("Traceback", output)
        for line in output.splitlines():
            self.assertFalse(line.startswith("::"), line)

    def test_tree_mode_with_nothing_found_is_silent_and_succeeds(self):
        result, output = self.tree(PATTERN + "\n")
        self.assertEqual((result.returncode, output), (0, ""))

    def test_tree_mode_stops_on_a_bad_pattern_without_quoting_it(self):
        result, output = self.tree("example-(hamlet\n")
        self.assertEqual(result.returncode, 1, output)
        self.assertNotIn("hamlet", output.lower())

    def test_colour_settings_dont_change_the_output(self):
        self.use_patterns(PATTERN)
        self.git(self.repo, "config", "color.ui", "always")
        commit = self.commit("notes.txt", f"{SECRET_VALUE}\n", "add notes")
        result, output = self.push()
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(f"  {commit} notes.txt:1\n", output)

    def test_a_session_can_run_it_by_hand_before_pushing(self):
        self.use_patterns(PATTERN)
        self.commit("notes.txt", f"{SECRET_VALUE}\n", "add notes")
        result = subprocess.run(["python3", str(SCRIPT)], cwd=self.repo, env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"[0-9a-f]{7} notes\.txt:1")


class SetUpTests(unittest.TestCase):
    def test_hook_and_script_are_executable(self):
        for path in (HOOK, SCRIPT):
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, f"{path.name} isn't executable")

    def test_tests_still_searches_the_same_way(self):
        # Tests stays the second layer; PrePushTests show this check matches text files case-insensitively too.
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn('identifying_details.py" --tree "$RUNNER_TEMP/patterns"', workflow)

    def test_contributing_says_how_to_set_it_up(self):
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for words in ("git config core.hooksPath .githooks", "PRIVACY_PATTERNS", "~/.config/usher/privacy-patterns"):
            self.assertIn(words, text)
        self.assertRegex(text, r"never .*(transcript|log)")

    def test_a_session_turns_it_on_before_it_pushes(self):
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        rule = re.search(r"^- Push with git.*$", text, re.M)
        self.assertIsNotNone(rule, "CONTRIBUTING.md doesn't say how a session pushes")
        self.assertIn("`git config core.hooksPath .githooks` before the first push", rule.group(0))
        self.assertIn("never with `--no-verify`", rule.group(0))


if __name__ == "__main__":
    unittest.main()
