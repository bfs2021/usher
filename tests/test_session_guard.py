"""The session guard: rules a coding session must follow are enforced by Claude Code, not only written down.

`.claude/settings.json` denies the plainest forms by permission rule and registers `.claude/hooks/session_guard.py`
as a PreToolUse hook, which refuses a Bash command that would push without the check for identifying details
(`--no-verify`, or `core.hooksPath` not `.githooks`), use `gh issue` or `gh pr` (GraphQL, out of a session's
reach), or print the PRIVACY_PATTERNS variable. What a session runs to fix an issue still goes through.
The same settings start a session in accept-edits mode and allow only the test and lint commands without asking.
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / ".claude" / "settings.json"
HOOK = ROOT / ".claude" / "hooks" / "session_guard.py"
MADE_UP_PATTERNS = "made-up-pattern-q7z\nexample-hamlet-2x9"
BRANCH = "issue-1-example-change"

DENY_RULES = [
    "Bash(git push --no-verify*)",
    "Bash(git push * --no-verify*)",
    "Bash(gh issue *)",
    "Bash(gh pr *)",
    "Read(./.env)",
    "Read(./.env.*)",
    "Read(**/.env)",
    "Read(**/.env.*)",
]
# The test and lint commands a session runs on every change, allowed without asking.
ALLOW_RULES = [
    "Bash(python3 -m unittest discover *)",
    "Bash(ruff check *)",
    "Bash(ruff format *)",
]

# Everything a session runs to fix an issue (CONTRIBUTING.md), run in a clone with the hook turned on.
FIX_ISSUE_COMMANDS = [
    "gh api repos/{owner}/{repo}/issues/1",
    "gh api repos/{owner}/{repo}/issues/1/comments",
    "gh api repos/{owner}/{repo}/pulls -f title=example",
    f"git fetch origin && git checkout -b {BRANCH} origin/main",
    "grep -rn PRIVACY_PATTERNS tools/ tests/ .githooks/",
    "grep -rn 'os.environ.get(\"PRIVACY_PATTERNS\"' tools/",
    "grep -rn \"gh pr\" docs/ .claude/",
    "sed -n 1,60p tools/identifying_details.py",
    "python3 -m unittest discover -s tests -t .",
    "python3 -m unittest tests.test_session_guard 2>&1 | tail -n 20",
    "pip install -r requirements-dev.txt && ruff check . && ruff format --check .",
    "git status && git diff --stat",
    "git add -A && git commit -m 'fix(example): made-up change'",
    "git commit -F - <<'EOF'\nfix(example): don't stop early\n\nIt's a made-up change.\nEOF",
    "git config core.hooksPath .githooks",
    "git config --get core.hooksPath",
    "python3 tools/identifying_details.py",
    f"git push -u origin {BRANCH}",
    f"git config core.hooksPath .githooks && git push -u origin {BRANCH}",
    f"git push -n origin {BRANCH}",
    f"git push --dry-run --force-with-lease origin {BRANCH}",
    "git log --oneline -5 && git log -S'os.environ' --oneline",
    'echo "$PRIVACY_PATTERNS_FILE" && ls ~/.config/usher',
    "PRIVACY_PATTERNS_FILE=/nonexistent/example python3 tools/identifying_details.py",
    "env FOO=1 python3 -m unittest tests.test_versions",
    "env -i PATH=/usr/bin:/bin git status",
    "set -euo pipefail; export EXAMPLE=1; declare -A seen",
    "printenv HOME",
    "python3 -c \"import os; print(os.environ['HOME'], os.environ.get('LANG'))\"",
    "ps aux | head -n 5 && ps -o pid,etime,cmd",
    'echo "never use gh pr or gh issue; see the skill"',
    'echo "oops',  # an open quote: the shell refuses it anyway
    # Text that only names the code, in a commit message, a search or a file being written.
    'git commit -m "fix: read os.environ once"',
    "git commit -q -F - <<'EOF'\nfix: stop reading os.environ twice\n\nprocess.env and /proc/self/environ are left alone.\nEOF",
    "find . -name '*.py' | xargs grep -n os.environ",
    'grep -n "/proc/self/environ" docs/architecture.md',
    "cat > notes.md <<'EOF'\nNode reads process.env, Python os.environ.\nEOF",
]

NO_VERIFY_PUSHES = [
    "git push --no-verify",
    f"git push -u origin {BRANCH} --no-verify",
    f"git push --no-verify -u origin {BRANCH}",
    f"git push --force-with-lease --no-verify origin {BRANCH}",
    "git push --no-verif origin HEAD",
    "git -C . push origin --no-verify",
    f"cd . && git push --no-verify origin {BRANCH}",
    "echo ready; git push origin HEAD --no-verify 2>&1 | tail -n 5",
    "bash -c 'git push --no-verify'",
    "FOO=1 command git push --no-verify",
    "git -c core.hooksPath=/dev/null push origin HEAD",
    "git -c core.hooksPath= push origin HEAD",
]

GRAPHQL_COMMANDS = [
    "gh issue view 1",
    "gh issue list --label bug",
    "gh pr create --draft --title example",
    "gh pr list",
    "cd . && gh issue list",
    "GH_REPO=example/repo gh pr view 1",
    'echo "$(gh pr list)"',
    "timeout 30 gh issue list",
    "sh -c 'gh pr checks 1'",
]

PRINTS_THE_PATTERNS = [
    "echo $PRIVACY_PATTERNS",
    'echo "${PRIVACY_PATTERNS}"',
    "printf '%s' \"${PRIVACY_PATTERNS:-none}\"",
    "printenv PRIVACY_PATTERNS",
    "printenv",
    "env",
    "env | grep PRIVACY",
    "env -0",
    "set",
    "( set -o posix; set )",
    "export -p",
    "export",
    "declare -p",
    "declare -x",
    "declare -p PRIVACY_PATTERNS",
    "typeset -x",
    "cat /proc/self/environ",
    "tr '\\0' '\\n' < /proc/1/environ",
    "ps eww",
    "python3 -c 'import os; print(os.environ[\"PRIVACY_PATTERNS\"])'",
    "python3 -c \"import os; print(os.environ.get('PRIVACY_PATTERNS'))\"",
    "python3 -c \"import os; print(os.getenv('PRIVACY_PATTERNS'))\"",
    'python3 -c "import os; print(os.environ)"',
    'python3 -c "import os, json; print(json.dumps(dict(os.environ)))"',
    "python3 - <<'EOF'\nimport os\nprint(os.environ['PRIVACY_PATTERNS'])\nEOF",
    "cat <<EOF\n$PRIVACY_PATTERNS\nEOF",
    'node -e "console.log(process.env.PRIVACY_PATTERNS)"',
    'node -e "console.log(process.env)"',
    "ruby -e 'puts ENV[\"PRIVACY_PATTERNS\"]'",
    "perl -e 'print $ENV{PRIVACY_PATTERNS}'",
    "awk 'BEGIN { print ENVIRON[\"PRIVACY_PATTERNS\"] }'",
    "bash -c 'echo $PRIVACY_PATTERNS'",
    "sh -c printenv",
    "eval printenv",
    "cat <<'EOF' | python3\nimport os\nprint(os.environ)\nEOF",
    "git status && cat /proc/self/environ",
]


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@unittest.skipUnless(shutil.which("git"), "needs git")
class HookTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PRIVACY_PATTERNS=MADE_UP_PATTERNS, HOME=str(base))
        self.on, self.off = base / "on", base / "off"
        for repo in (self.on, self.off):
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, env=self.env)
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=self.on, check=True, env=self.env)

    def run_hook(self, command, cwd=None, tool="Bash"):
        event = {"session_id": "example", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command}}
        event["cwd"] = str(cwd or self.on)
        done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True, env=self.env, timeout=30)
        for line in MADE_UP_PATTERNS.splitlines():
            self.assertNotIn(line, done.stdout + done.stderr, "the hook printed the patterns")
        return done

    def assertRefused(self, command, says, cwd=None):
        done = self.run_hook(command, cwd)
        self.assertEqual(done.returncode, 2, f"not refused: {command!r}")
        self.assertIn(says, done.stderr, command)

    def assertAllowed(self, command, cwd=None):
        done = self.run_hook(command, cwd)
        self.assertEqual(done.returncode, 0, f"refused: {command!r}: {done.stderr}")
        self.assertEqual(done.stderr, "")

    def test_a_normal_fix_issue_run_goes_through(self):
        for command in FIX_ISSUE_COMMANDS:
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_a_push_with_no_verify_is_refused(self):
        for command in NO_VERIFY_PUSHES:
            with self.subTest(command=command):
                done = self.run_hook(command)
                self.assertEqual(done.returncode, 2, command)
                self.assertRegex(done.stderr, r"--no-verify|core\.hooksPath \.githooks")

    def test_a_push_without_the_pre_push_hook_is_refused(self):
        self.assertRefused(f"git push -u origin {BRANCH}", "git config core.hooksPath .githooks", cwd=self.off)
        self.assertRefused(f"git -C {self.off} push", "core.hooksPath", cwd=self.on)
        self.assertRefused(f"cd {self.off} && git push", "core.hooksPath", cwd=self.on)
        self.assertRefused("git config --get core.hooksPath && git push", "core.hooksPath", cwd=self.off)
        self.assertRefused("git config --unset core.hooksPath; git push", "core.hooksPath", cwd=self.on)
        self.assertRefused('cd "$HOME/elsewhere" && git push', "which repository")

    def test_a_push_where_the_hook_is_on_goes_through(self):
        self.assertAllowed(f"git -C {self.on} push origin HEAD", cwd=self.off)
        self.assertAllowed(f"cd {self.on} && git push origin HEAD", cwd=self.off)
        self.assertAllowed("git config core.hooksPath .githooks && git push origin HEAD", cwd=self.off)
        self.assertAllowed("git -c core.hooksPath=.githooks push origin HEAD", cwd=self.off)

    def test_gh_issue_and_gh_pr_are_refused(self):
        for command in GRAPHQL_COMMANDS:
            with self.subTest(command=command):
                self.assertRefused(command, "gh api")

    def test_printing_the_patterns_is_refused(self):
        for command in PRINTS_THE_PATTERNS:
            with self.subTest(command=command):
                self.assertRefused(command, "PRIVACY_PATTERNS")

    def test_other_tools_and_unreadable_input_are_let_through(self):
        self.assertEqual(self.run_hook("printenv", tool="Read").returncode, 0)
        for text in ("not json", "[" * 100000):  # the second is too deeply nested to parse
            done = subprocess.run([sys.executable, str(HOOK)], input=text, capture_output=True, text=True, env=self.env, timeout=30)
            self.assertEqual((done.returncode, done.stderr), (0, ""))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))

    def test_keeps_the_schema(self):
        self.assertEqual(self.settings["$schema"], "https://json.schemastore.org/claude-code-settings.json")

    def test_denies_each_rule(self):
        deny = self.settings["permissions"]["deny"]
        self.assertEqual(sorted(deny), sorted(DENY_RULES))
        for rule in deny:
            self.assertRegex(rule, r"^(Bash|Read)\([^)]+\)$")  # the shape the settings schema accepts

    def test_starts_in_accept_edits_mode(self):
        # Cloud sessions take only acceptEdits, plan, default and auto as the starting mode.
        self.assertEqual(self.settings["permissions"]["defaultMode"], "acceptEdits")

    def test_allows_the_test_and_lint_commands_only(self):
        allow = self.settings["permissions"]["allow"]
        self.assertEqual(sorted(allow), sorted(ALLOW_RULES))
        for command in ("python3 -m unittest discover -s tests -t .", "ruff check .", "ruff format --check ."):
            self.assertTrue(any(self.bash_matches(rule, command) for rule in allow), command)
        for command in ("git push --no-verify", "gh pr create --draft", "python3 -c 'print(1)'", "rm -rf web"):
            self.assertFalse(any(self.bash_matches(rule, command) for rule in allow), command)

    def test_no_allow_rule_overlaps_a_deny_rule(self):
        self.assertFalse(set(self.settings["permissions"]["allow"]) & set(self.settings["permissions"]["deny"]))

    def test_leaves_out_the_tools_own_git_instructions(self):
        # CONTRIBUTING.md sets the commit and pull request format; the tool's built-in instructions would compete.
        self.assertIs(self.settings["includeGitInstructions"], False)

    def test_registers_the_hook_for_bash(self):
        entries = [e for e in self.settings["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"]
        commands = [h["command"] for e in entries for h in e["hooks"] if h.get("type") == "command"]
        self.assertEqual(commands, ['python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/session_guard.py"'])
        self.assertTrue(HOOK.is_file())

    @staticmethod
    def bash_matches(rule, command):
        """Whether a Bash rule matches the command, read the way Claude Code reads it: `*` is any text."""
        m = re.fullmatch(r"Bash\((.*)\)", rule)
        return bool(m and re.fullmatch(".*".join(map(re.escape, m.group(1).split("*"))), command, re.S))

    def bash_denied(self, command):
        return any(self.bash_matches(rule, command) for rule in self.settings["permissions"]["deny"])

    def test_bash_rules_deny_the_plain_forms_and_nothing_a_fix_issue_run_needs(self):
        for command in ("git push --no-verify", f"git push origin {BRANCH} --no-verify", "gh issue view 1", "gh pr create --draft"):
            self.assertTrue(self.bash_denied(command), command)
        for command in FIX_ISSUE_COMMANDS:
            self.assertFalse(self.bash_denied(command), command)

    def test_read_rules_cover_env_files_only(self):
        globs = [re.fullmatch(r"Read\((?:\./|\*\*/)(.*)\)", r).group(1) for r in self.settings["permissions"]["deny"] if r.startswith("Read(")]

        def denied(name):
            return any(fnmatch.fnmatchcase(name, g) for g in globs)

        for name in (".env", ".env.local", ".env.production"):
            self.assertTrue(denied(name), name)
        listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.split()
        for path in listed or ["deploy/site.env.example"]:
            self.assertFalse(denied(Path(path).name), f"{path} is tracked but a Read rule denies it")


class GuidanceTests(unittest.TestCase):
    def test_claude_md_points_to_the_enforcing_file(self):
        self.assertTrue(".claude/settings.json" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8"), "CLAUDE.md doesn't name it")

    def test_contributing_says_a_session_push_needs_the_hook(self):
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn(f"`{HOOK.name}`", text, "CONTRIBUTING.md doesn't name it")


if __name__ == "__main__":
    unittest.main()
