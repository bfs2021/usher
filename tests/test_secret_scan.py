"""Tests scans for committed secrets with gitleaks: the pull request's commits, or the tree on main. The tool is
built at an exact version and checked against Go's checksum database, its settings are in the repository, and
only file and line are ever printed."""

import os
import re
import shutil
import string
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"

# A token in the shape of a GitHub personal access token, built here so the source never holds one.
# Obviously made up: it is a slice of the alphabet.
FAKE_TOKEN = "ghp_" + (string.ascii_letters + string.digits)[7:43]


def _job():
    text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    return re.search(r"(?m)^  test:\n((?:    .*\n|\n)+)", text).group(1)


def _step(name):
    return _job().split(f"- name: {name}\n", 1)[1].split("\n      - ", 1)[0]


def _env(step):
    return dict(re.findall(r"(?m)^          (\w+): (.*)$", step.split("        run: |", 1)[0]))


def _script(step):
    """The shell of a run step, without the YAML indentation."""
    body = step.split("        run: |\n", 1)[1]
    return "".join(line[10:] + "\n" for line in body.splitlines())


class SecretScanStepTests(unittest.TestCase):
    def test_install_and_scan_run_in_the_tests_job_for_everyone(self):
        job = _job()
        self.assertLess(job.index("- name: Install gitleaks"), job.index("- name: Look for committed secrets"))
        for name in ("Install gitleaks", "Look for committed secrets"):
            with self.subTest(step=name):
                self.assertNotRegex(_step(name), r"(?m)^        if:")  # Dependabot's runs too: no secrets needed
                self.assertNotIn("secrets.", _step(name))

    def test_checkout_has_every_commit_and_keeps_no_token(self):
        checkout = _job().split("- uses: actions/checkout@", 1)[1].split("\n      - ", 1)[0]
        self.assertRegex(checkout, r"(?m)^          fetch-depth: 0\b")
        self.assertRegex(checkout, r"(?m)^          persist-credentials: false$")

    def test_gitleaks_is_built_at_an_exact_version(self):
        step = _step("Install gitleaks")
        self.assertRegex(_env(step).get("VERSION", ""), r"^v\d+\.\d+\.\d+$")  # never latest or a branch
        script = _script(step)
        self.assertIn('go install "github.com/zricethezav/gitleaks/v8@$VERSION"', script)
        self.assertEqual(script.count("go install"), 1)
        self.assertIn('export GOBIN="$RUNNER_TEMP/gitleaks-bin"', script)
        self.assertIn('echo "$GOBIN" >> "$GITHUB_PATH"', script)
        self.assertNotRegex(script, r"\b(curl|wget)\b")  # nothing fetched outside Go's checks

    def test_go_checksum_database_stays_on(self):
        """go install checks the module and its dependencies against sum.golang.org unless told not to."""
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        for setting in ("GONOSUMDB", "GONOSUMCHECK", "GONOSUMCHECKS", "GOSUMDB", "GOINSECURE", "GOPRIVATE", "GOPROXY"):
            with self.subTest(setting=setting):
                self.assertNotIn(setting, text)
        self.assertNotIn("GOFLAGS", text)
        self.assertNotIn("-insecure", text)

    def test_pull_requests_scan_their_commits_and_main_scans_the_tree(self):
        step = _step("Look for committed secrets")
        env = _env(step)
        self.assertEqual(env["BASE"], "${{ github.event.pull_request.base.sha }}")
        self.assertEqual(env["HEAD"], "${{ github.event.pull_request.head.sha }}")
        script = _script(step)
        self.assertIn('set -- git --log-opts="$BASE..$HEAD"', script)
        self.assertIn("set -- dir", script)
        self.assertNotIn("${{", script)  # event data reaches the shell through env only

    def test_matches_are_never_printed(self):
        script = _script(_step("Look for committed secrets"))
        self.assertIn("--redact", script)
        self.assertIn("--no-banner", script)
        self.assertIn("--config .gitleaks.toml", script)
        self.assertIn("--report-format json", script)
        self.assertNotRegex(script, r"\s(-v|--verbose)\b")
        self.assertIn(""".[] | "\\(.File):\\(.StartLine)\"""", script)
        self.assertNotRegex(script, r"\.(Secret|Match)\b")
        self.assertNotRegex(script, r"\bcat\b|upload-artifact")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.text = CONFIG.read_text(encoding="utf-8")

    def test_default_rules_apply(self):
        self.assertRegex(self.text, r"(?m)^\[extend\]\nuseDefault = true$")

    def test_only_tests_and_mocks_are_allowlisted_each_with_a_reason(self):
        lines = self.text.splitlines()
        paths = [n for n, line in enumerate(lines) if line.lstrip().startswith("'''")]
        self.assertEqual([lines[n].strip() for n in paths], ["'''^tests/mock_apps\\.py$''',", "'''^tests/test_[a-z0-9_]+\\.py$''',"])
        for n in paths:
            with self.subTest(path=lines[n].strip()):
                self.assertTrue(lines[n - 1].strip().startswith("#"), "no comment saying why")
        self.assertNotRegex(self.text, r"(?m)^\s*(regexes|stopwords|commits)\s*=")  # paths only

    def test_the_upstream_check_scripts_stay_scanned(self):
        pattern = re.compile(r"^tests/test_[a-z0-9_]+\.py$")
        self.assertIsNone(pattern.match("tests/upstream_seed.py"))
        self.assertIsNone(pattern.match("usher/test_x.py"))
        self.assertIsNotNone(pattern.match("tests/test_secret_scan.py"))


@unittest.skipUnless(shutil.which("gitleaks") and shutil.which("jq") and shutil.which("git"), "needs gitleaks and jq")
class ScanRunTests(unittest.TestCase):
    """Runs the workflow's own scan step on a throwaway repository, offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        shutil.copy(CONFIG, self.repo / ".gitleaks.toml")
        (self.repo / "README.md").write_text("Example\n", encoding="utf-8")
        self.base = self._commit("base")

    def _git(self, *args):
        env = {**os.environ, "GIT_AUTHOR_NAME": "Example", "GIT_AUTHOR_EMAIL": "example@example.invalid"}
        env.update(GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="example@example.invalid")
        return subprocess.run(["git", *args], cwd=self.repo, env=env, check=True, capture_output=True, text=True)

    def _commit(self, message):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _add(self, path, text):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text, encoding="utf-8")
        return self._commit(f"add {path}")

    def _scan(self, event, head=""):
        temp = Path(self.tmp.name) / "runner"
        temp.mkdir(exist_ok=True)
        env = {**os.environ, "EVENT": event, "BASE": self.base, "HEAD": head, "RUNNER_TEMP": str(temp)}
        script = _script(_step("Look for committed secrets"))
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_a_token_in_a_pull_request_fails_with_file_and_line_only(self):
        head = self._add("usher/settings.py", f'"""Settings."""\n\nTOKEN = "{FAKE_TOKEN}"\n')
        result = self._scan("pull_request", head)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("usher/settings.py:3", result.stdout)
        self.assertNotIn(FAKE_TOKEN, result.stdout + result.stderr)
        self.assertNotIn(FAKE_TOKEN[4:12], result.stdout + result.stderr)

    def test_a_token_removed_in_a_later_commit_still_fails(self):
        self._add("usher/settings.py", f'TOKEN = "{FAKE_TOKEN}"\n')
        head = self._add("usher/settings.py", 'TOKEN = ""\n')
        result = self._scan("pull_request", head)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("usher/settings.py:1", result.stdout)

    def test_a_token_in_the_tree_fails_on_main(self):
        self._add("deploy/example.env", f"GITHUB_TOKEN={FAKE_TOKEN}\n")
        result = self._scan("push")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("deploy/example.env:1", result.stdout)
        self.assertNotIn(FAKE_TOKEN, result.stdout + result.stderr)

    def test_made_up_keys_in_tests_pass(self):
        self._add("tests/test_example.py", f'TOKEN = "{FAKE_TOKEN}"\n')
        head = self._add("tests/mock_apps.py", f'KEY = "{FAKE_TOKEN}"\n')
        for event in ("pull_request", "push"):
            with self.subTest(event=event):
                result = self._scan(event, head)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("No secrets found.", result.stdout)

    def test_a_clean_pull_request_passes(self):
        result = self._scan("pull_request", self._add("usher/example.py", "NAME = 'example'\n"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
