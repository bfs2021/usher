"""A coding tool appends a footer to every issue, pull request and comment it writes. Strip footer removes that
exact trailing footer and nothing else, with the workflow's own token. The logic lives in
.github/scripts/strip_footer.py; made-up values only, and GitHub is never called."""

import contextlib
import importlib.util
import io
import json
import re
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "strip_footer.py"
WORKFLOW = ROOT / ".github" / "workflows" / "strip-footer.yml"

spec = importlib.util.spec_from_file_location("strip_footer", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

# Split so that no file here holds the footer as one phrase (tests/test_public_conventions.py looks for it).
FOOTER = "_Generated " + "by [Claude Code](https://example.invalid/code)_"
SESSION_FOOTER = "_Generated " + "by [Claude Code](https://example.invalid/code/session_0123abcd)_"
BODY = "## Summary\n\nA change.\n\nFixes #7"


class StripFooterTests(unittest.TestCase):
    def test_a_footer_at_the_end_is_removed_with_the_blank_lines_before_it(self):
        for gap in ("\n\n", "\n", "\n\n\n"):
            with self.subTest(gap=repr(gap)):
                self.assertEqual(script.strip_footer(f"{BODY}{gap}---\n{FOOTER}"), BODY)

    def test_a_body_without_the_footer_is_left_alone(self):
        for text in ("", BODY, BODY + "\n", "---\nOnly a rule.", "Text\n\n---\n_Some other italic line_"):
            with self.subTest(text=text):
                self.assertIsNone(script.strip_footer(text))

    def test_trailing_blank_lines_after_the_footer_do_not_matter(self):
        for tail in ("\n", "\n\n\n", "\n  \n\t\n", " "):
            with self.subTest(tail=repr(tail)):
                self.assertEqual(script.strip_footer(f"{BODY}\n\n---\n{FOOTER}{tail}"), BODY)

    def test_windows_line_endings_are_handled(self):
        text = f"{BODY}\n\n---\n{FOOTER}\n".replace("\n", "\r\n")
        self.assertEqual(script.strip_footer(text), BODY.replace("\n", "\r\n"))  # the author's own line endings stay

    def test_a_footer_like_line_in_the_middle_is_left_alone(self):
        text = f"Before.\n\n---\n{FOOTER}\n\nAfter the quoted footer."
        self.assertIsNone(script.strip_footer(text))

    def test_the_footer_must_follow_a_rule_line(self):
        self.assertIsNone(script.strip_footer(f"{BODY}\n\n{FOOTER}"))
        self.assertIsNone(script.strip_footer(f"{BODY}\n\n----\n{FOOTER}"))
        self.assertIsNone(script.strip_footer(f"{BODY}\n---{FOOTER}"))

    def test_the_link_inside_the_footer_may_differ(self):
        # On a pull request the tool links to the session instead of its home page.
        self.assertEqual(script.strip_footer(f"{BODY}\n\n---\n{SESSION_FOOTER}"), BODY)

    def test_only_the_exact_footer_goes(self):
        for line in (
            "_Generated " + "by [Claude Code](http://example.invalid/code)_",  # not https
            "_Generated " + "by [Claude Code](https://example.invalid/code) and more_",
            "_Generated " + "by [Someone Else](https://example.invalid/code)_",
            "_Generated " + "by [Claude Code]()_",
            "Generated " + "by [Claude Code](https://example.invalid/code)",
        ):
            with self.subTest(line=line):
                self.assertIsNone(script.strip_footer(f"{BODY}\n\n---\n{line}"))

    def test_a_rule_the_author_wrote_before_the_footer_stays(self):
        self.assertEqual(script.strip_footer(f"{BODY}\n\n---\n\n---\n{FOOTER}"), f"{BODY}\n\n---")

    def test_stripping_twice_changes_nothing_more(self):
        once = script.strip_footer(f"{BODY}\n\n---\n{FOOTER}")
        self.assertIsNone(script.strip_footer(once))

    def test_a_body_of_only_the_footer_becomes_empty(self):
        self.assertEqual(script.strip_footer(f"---\n{FOOTER}"), "")


class WriteTests(unittest.TestCase):
    REPO = "example-org/example-repo"

    def run_main(self, values, current=None):
        """Runs main with a fake `gh`: a read answers `current` (by default the event's own text), a write is recorded."""
        values = {"KIND": "issue", "ID": "5", "GITHUB_REPOSITORY": self.REPO, **values}
        live = values.get("TEXT", "") if current is None else current

        def gh(args, **kwargs):
            return mock.Mock(stdout=live + "\n")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(script.subprocess, "run", side_effect=gh) as run:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = script.main(values)
        writes = [c for c in run.call_args_list if "PATCH" in c.args[0]]
        reads = [c for c in run.call_args_list if "PATCH" not in c.args[0]]
        return code, writes, reads, out.getvalue(), err.getvalue()

    def test_a_footer_is_written_back_without_it_to_the_right_place(self):
        for kind, path in (
            ("issue", "repos/example-org/example-repo/issues/5"),
            ("pull_request", "repos/example-org/example-repo/pulls/5"),
            ("comment", "repos/example-org/example-repo/issues/comments/5"),
        ):
            with self.subTest(kind=kind):
                code, writes, reads, out, _ = self.run_main({"KIND": kind, "TEXT": f"{BODY}\n\n---\n{FOOTER}"})
                self.assertEqual(code, 0)
                self.assertEqual(len(writes), 1)
                args, kwargs = writes[0].args[0], writes[0].kwargs
                self.assertEqual(args, ["gh", "api", "--method", "PATCH", path, "--input", "-"])
                self.assertEqual(json.loads(kwargs["input"]), {"body": BODY})
                self.assertTrue(kwargs["check"])
                self.assertEqual(reads[0].args[0][:3], ["gh", "api", path])
                self.assertIn("footer removed", out)

    def test_the_current_text_is_what_is_stripped_not_the_events_copy(self):
        newer = f"{BODY}\n\nA line added after the event.\n\n---\n{FOOTER}"
        _, writes, _, _, _ = self.run_main({"TEXT": f"{BODY}\n\n---\n{FOOTER}"}, current=newer)
        self.assertEqual(json.loads(writes[0].kwargs["input"]), {"body": f"{BODY}\n\nA line added after the event."})

    def test_a_footer_already_gone_from_the_current_text_writes_nothing(self):
        code, writes, _, out, _ = self.run_main({"TEXT": f"{BODY}\n\n---\n{FOOTER}"}, current="Someone's newer text.")
        self.assertEqual(code, 0)
        self.assertEqual(writes, [])
        self.assertIn("edited since", out)

    def test_a_body_without_the_footer_makes_no_call_at_all(self):
        code, writes, reads, out, _ = self.run_main({"TEXT": BODY})
        self.assertEqual((code, writes, reads), (0, [], []))
        self.assertIn("nothing written", out)

    def test_a_missing_body_makes_no_call_at_all(self):
        code, writes, reads, _, _ = self.run_main({})  # an issue opened without a description has no text
        self.assertEqual((code, writes, reads), (0, [], []))

    def test_a_comment_of_only_the_footer_is_left_as_it_is(self):
        code, writes, _, out, _ = self.run_main({"KIND": "comment", "TEXT": f"---\n{FOOTER}"})
        self.assertEqual((code, writes), (0, []))
        self.assertIn("left as it is", out)

    def test_an_issue_of_only_the_footer_is_emptied(self):
        _, writes, _, _, _ = self.run_main({"TEXT": f"---\n{FOOTER}"})
        self.assertEqual(json.loads(writes[0].kwargs["input"]), {"body": ""})

    def test_a_bad_kind_id_or_repository_stops_before_any_call(self):
        for values in (
            {"KIND": "discussion"},
            {"KIND": ""},
            {"ID": ""},
            {"ID": "5; rm"},
            {"ID": "-1"},
            {"GITHUB_REPOSITORY": ""},
            {"GITHUB_REPOSITORY": "no-slash"},
            {"GITHUB_REPOSITORY": "a/b c"},
        ):
            with self.subTest(values=values):
                code, writes, reads, _, err = self.run_main({"TEXT": f"{BODY}\n\n---\n{FOOTER}", **values})
                self.assertEqual((code, writes, reads), (1, [], []))
                self.assertIn("Expected KIND", err)
                self.assertNotIn(BODY, err)  # the text is never echoed


def jobs_of(text):
    """Each job's name and the lines under it."""
    block = re.search(r"(?m)^jobs:\n((?:[ #].*\n|\n)+)", text).group(1)
    parts = re.split(r"(?m)^  ([\w-]+):[ \t]*(?:#.*)?\n", block)
    return dict(zip(parts[1::2], parts[2::2]))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.code = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))
        self.jobs = jobs_of(self.text)

    def test_it_runs_when_an_issue_pull_request_or_comment_is_written(self):
        on = re.search(r"(?m)^on:\n((?:  .*\n)+)", self.text).group(1)
        self.assertEqual(
            re.findall(r"(?m)^  (\w+):\n    types: \[([\w, ]+)\]$", on),
            [("issues", "opened, edited"), ("pull_request", "opened, edited"), ("issue_comment", "created, edited")],
        )
        self.assertEqual(sorted(self.jobs), ["comment", "issue", "pull-request"])

    def test_it_never_uses_pull_request_target(self):
        self.assertNotIn("pull_request_target", self.code)

    def test_nothing_is_granted_at_the_top_and_each_job_reads_contents_and_writes_one_thing(self):
        self.assertRegex(self.text.split("\njobs:", 1)[0], r"(?m)^permissions: \{\}$")
        wanted = {"issue": "issues", "pull-request": "pull-requests", "comment": "issues"}
        for job, scope in wanted.items():
            with self.subTest(job=job):
                perms = re.search(r"(?m)^    permissions:\n((?:      .*\n)+)", self.jobs[job]).group(1)
                self.assertEqual(re.findall(r"(?m)^      ([\w-]+): (\w+)", perms), [("contents", "read"), (scope, "write")])

    def test_a_forks_pull_request_is_skipped(self):
        cond = re.search(r"(?m)^    if: (.*)$", self.jobs["pull-request"]).group(1)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", cond)
        self.assertNotIn("||", cond)

    def test_each_job_answers_only_its_own_event(self):
        for job, event in (("issue", "issues"), ("pull-request", "pull_request"), ("comment", "issue_comment")):
            with self.subTest(job=job):
                self.assertIn(f"if: github.event_name == '{event}'", self.jobs[job])

    def test_the_text_and_the_number_reach_the_script_through_env_only(self):
        for job, source in (("issue", "issue.body"), ("pull-request", "pull_request.body"), ("comment", "comment.body")):
            with self.subTest(job=job):
                body = self.jobs[job]
                self.assertIn(f"TEXT: ${{{{ github.event.{source} }}}}", body)
                runs = re.findall(r"(?m)^        run: (.*)$", body)
                self.assertEqual(runs, ["python3 .github/scripts/strip_footer.py"])
        for line in self.code.splitlines():
            if line.strip().startswith("run:"):
                self.assertNotIn("${{", line)

    def test_the_script_comes_from_main_and_the_token_is_not_kept(self):
        for job, body in self.jobs.items():
            with self.subTest(job=job):
                self.assertRegex(body, r"(?m)^          ref: main$")
                self.assertRegex(body, r"(?m)^          persist-credentials: false$")
                self.assertRegex(body, r"(?m)^          sparse-checkout: \.github/scripts$")
                self.assertIn("GH_TOKEN: ${{ github.token }}", body)


if __name__ == "__main__":
    unittest.main()
