"""The workflows run with tokens that can write, so the Tests job lints them: actionlint (syntax, expressions and,
through shellcheck, the shell in run steps) and zizmor (injection, permissions, token exposure, unpinned actions).
Both are pinned development tools. A finding accepted on purpose is listed with its reason in the tool's own
config file, never silenced inside a workflow."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB = ROOT / ".github"
TOOLS = ("actionlint-py", "shellcheck-py", "zizmor")
CONFIGS = (GITHUB / "actionlint.yaml", GITHUB / "zizmor.yml")

INJECTED = """\
name: Example
on: pull_request
permissions: {}
jobs:
  greet:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.pull_request.title }}"
"""
# The same step with the title passed through the environment, as the workflows here do it.
SAFE = INJECTED.replace(
    '      - run: echo "${{ github.event.pull_request.title }}"\n',
    '      - env:\n          TITLE: ${{ github.event.pull_request.title }}\n        run: echo "$TITLE"\n',
)


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def _pins(path):
    pins = {}
    for line in _read(path).splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[name.strip()] = version.strip()
    return pins


def _lint_step():
    text = _read(".github/workflows/tests.yml")
    job = re.search(r"(?m)^  test:\n((?:    .*\n|\n)+)", text).group(1)
    return job.split("- name: Lint the workflows\n", 1)[1].split("- name:", 1)[0]


def unexplained_ignores(text):
    """The ignore entries of a lint config that have no comment line directly above them saying why."""
    lines = text.splitlines()
    bad = []
    for number, line in enumerate(lines):
        if not re.match(r"^\s+- \S", line):
            continue
        above = lines[number - 1].strip() if number else ""
        if not (above.startswith("#") and len(above.split()) >= 4):
            bad.append(f"{number + 1}: {line.strip()}")
    return bad


class LintStepTests(unittest.TestCase):
    def test_both_tools_run_as_a_step_of_the_tests_job(self):
        step = _lint_step()
        self.assertRegex(step, r"(?m)^          actionlint$")
        self.assertRegex(step, r"(?m)^          zizmor --offline \.$")  # the repo: workflows and dependabot.yml
        self.assertNotRegex(step, r"(?m)^        if:")  # every run, Dependabot's included
        self.assertNotIn("${{", step)

    def test_tools_are_pinned_development_dependencies(self):
        pins = _pins("requirements-dev.txt")
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertRegex(pins.get(tool, ""), r"^\d+(\.\d+)+$")
                self.assertNotIn(tool, _read("requirements.txt"))

    def test_accepted_findings_are_listed_with_a_reason_in_the_config(self):
        configs = [path for path in CONFIGS if path.exists()]
        self.assertIn(GITHUB / "zizmor.yml", configs)
        for path in configs:
            with self.subTest(config=path.name):
                self.assertEqual(unexplained_ignores(path.read_text(encoding="utf-8")), [])

    def test_the_reason_check_catches_a_bare_entry(self):
        self.assertEqual(unexplained_ignores("rules:\n  x:\n    ignore:\n      - a.yml\n"), ["4: - a.yml"])
        explained = "rules:\n  x:\n    ignore:\n      # a reason given in words\n      - a.yml\n"
        self.assertEqual(unexplained_ignores(explained), [])

    def test_nothing_is_silenced_inside_a_workflow(self):
        for path in [*sorted((GITHUB / "workflows").glob("*.y*ml")), GITHUB / "dependabot.yml"]:
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"zizmor:\s*ignore")
                self.assertNotIn("shellcheck disable", text)


@unittest.skipUnless(shutil.which("actionlint") and shutil.which("zizmor"), "pip install -r requirements-dev.txt")
class InjectionExampleTests(unittest.TestCase):
    """A pull request's title inside a run step fails both tools; the same title through env passes."""

    def _lint(self, workflow):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".github" / "workflows" / "example.yml"
            path.parent.mkdir(parents=True)
            path.write_text(workflow, encoding="utf-8")
            actionlint = subprocess.run(["actionlint", str(path)], cwd=tmp, capture_output=True, text=True)
            zizmor = subprocess.run(["zizmor", "--offline", str(path)], cwd=tmp, capture_output=True, text=True)
        return actionlint, zizmor

    def test_a_title_in_a_run_step_fails_both(self):
        actionlint, zizmor = self._lint(INJECTED)
        self.assertNotEqual(actionlint.returncode, 0)
        self.assertIn("potentially untrusted", actionlint.stdout)
        self.assertNotEqual(zizmor.returncode, 0)
        self.assertIn("template-injection", zizmor.stdout + zizmor.stderr)

    def test_the_title_through_env_passes_both(self):
        actionlint, zizmor = self._lint(SAFE)
        self.assertEqual(actionlint.returncode, 0, actionlint.stdout)
        self.assertEqual(zizmor.returncode, 0, zizmor.stdout + zizmor.stderr)


if __name__ == "__main__":
    unittest.main()
