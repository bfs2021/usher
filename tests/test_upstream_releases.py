"""The `Support <App> <version>` issues the upstream check opens for each new release, against made-up results."""

import datetime
import io
import json
import unittest
import urllib.error
from unittest import mock

from tests import upstream_releases

TODAY = datetime.date(2030, 1, 2)
RUN = "https://example.invalid/actions/runs/1"


class FakeGitHub:
    """Issues kept in memory, as GitHub's REST API returns them."""

    def __init__(self, issues=(), upstream_issue=None):
        self.issues = [dict(issue) for issue in issues]
        self.upstream_issue = upstream_issue
        self.comments = {}

    def release_issues(self):
        return self.issues  # unfiltered, so the script's own label and title checks are what count

    def open_upstream_issue(self):
        return self.upstream_issue

    def create(self, title, body, labels):
        number = 100 + len(self.issues)
        self.issues.append({"number": number, "title": title, "body": body, "state": "open", "state_reason": None, "labels": [{"name": n} for n in labels]})
        return number

    def comment(self, number, body):
        self.comments.setdefault(number, []).append(body)

    def close(self, number, reason):
        issue = self.issue(number)
        issue["state"], issue["state_reason"] = "closed", reason

    def issue(self, number):
        return next(i for i in self.issues if i["number"] == number)

    def titled(self, title):
        return [i for i in self.issues if i["title"] == title]


def existing(number, title, state="open", labels=(upstream_releases.LABEL, "enhancement", "P1")):
    return {"number": number, "title": title, "state": state, "labels": [{"name": n} for n in labels]}


def results(versions, failing=()):
    return {"versions": versions, "failures": [{"app": app, "call": "queue", "error": "KeyError"} for app in failing]}


def sync(api, versions, failing=()):
    return upstream_releases.sync(api, results(versions, failing), RUN, TODAY)


class NewReleaseTests(unittest.TestCase):
    def test_a_new_version_opens_a_p1_issue_naming_both_versions_and_the_release_notes(self):
        api = FakeGitHub([existing(5, "Support Sonarr 4.0.1.100", state="closed")])
        sync(api, {"Sonarr": "4.0.2.200"}, failing=["Sonarr"])
        [issue] = api.titled("Support Sonarr 4.0.2.200")
        self.assertEqual({label["name"] for label in issue["labels"]}, {"enhancement", "P1", "upstream-release"})
        self.assertEqual(issue["state"], "open")
        self.assertIn("Previous version: 4.0.1.100", issue["body"])
        self.assertIn("New version: 4.0.2.200", issue["body"])
        self.assertIn("https://github.com/Sonarr/Sonarr/releases/tag/v4.0.2.200", issue["body"])

    def test_a_failing_version_stays_open_and_links_the_upstream_issue(self):
        api = FakeGitHub(upstream_issue=42)
        sync(api, {"Radarr": "5.3.0"}, failing=["Radarr"])
        [issue] = api.titled("Support Radarr 5.3.0")
        self.assertEqual(issue["state"], "open")
        self.assertIn("fails on it, see #42", issue["body"])
        self.assertNotIn(issue["number"], api.comments)

    def test_a_passing_version_is_verified_with_its_matrix_row_and_closed(self):
        api = FakeGitHub()
        sync(api, {"Jellyfin": "10.11.2"})
        [issue] = api.titled("Support Jellyfin 10.11.2")
        self.assertIn("every call Usher makes passes", issue["body"])
        self.assertEqual((issue["state"], issue["state_reason"]), ("closed", "completed"))
        [note] = api.comments[issue["number"]]
        self.assertIn("Verified", note)
        self.assertIn("| Jellyfin | current and previous major version | 10.11.2 (2030-01-02) |", note)
        self.assertIn("python3 -m tests.upstream_report matrix results.json", note)

    def test_the_check_is_judged_per_app(self):
        api = FakeGitHub(upstream_issue=7)
        sync(api, {"Sonarr": "4.0.2", "Radarr": "5.3.0"}, failing=["Sonarr"])
        self.assertEqual(api.titled("Support Sonarr 4.0.2")[0]["state"], "open")
        self.assertEqual(api.titled("Support Radarr 5.3.0")[0]["state"], "closed")

    def test_with_no_earlier_issue_the_previous_version_is_none_recorded(self):
        api = FakeGitHub()
        sync(api, {"Transmission": "4.0.6"}, failing=["Transmission"])
        body = api.titled("Support Transmission 4.0.6")[0]["body"]
        self.assertIn("Previous version: none recorded", body)
        self.assertIn("https://github.com/transmission/transmission/releases/tag/4.0.6", body)

    def test_a_version_that_is_not_a_version_number_opens_nothing(self):
        api = FakeGitHub()
        done = sync(api, {"Sonarr": "4.0 [x](https://example.invalid)", "Radarr": ""})
        self.assertEqual(api.issues, [])
        self.assertEqual(done, ["Sonarr: skipped, not a stable version number"])

    def test_a_pre_release_opens_nothing_and_never_hides_its_stable_release(self):
        api = FakeGitHub()
        sync(api, {"Jellyfin": "10.11.0-rc1"})
        self.assertEqual(api.issues, [])
        sync(api, {"Jellyfin": "10.11.0"}, failing=["Jellyfin"])
        self.assertEqual(len(api.titled("Support Jellyfin 10.11.0")), 1)


class RerunTests(unittest.TestCase):
    def test_a_rerun_never_opens_a_second_issue_for_the_same_version(self):
        api = FakeGitHub()
        for _ in range(2):
            sync(api, {"Prowlarr": "1.30.0"}, failing=["Prowlarr"])
        self.assertEqual(len(api.titled("Support Prowlarr 1.30.0")), 1)

    def test_a_closed_issue_for_the_same_version_is_not_opened_again(self):
        api = FakeGitHub([existing(9, "Support Prowlarr 1.30.0", state="closed")])
        self.assertEqual(sync(api, {"Prowlarr": "1.30.0"}), [])
        self.assertEqual(len(api.issues), 1)
        self.assertEqual(api.comments, {})

    def test_an_open_issue_closes_once_its_version_passes(self):
        api = FakeGitHub([existing(9, "Support Sonarr 4.0.2")])
        sync(api, {"Sonarr": "4.0.2"}, failing=["Sonarr"])
        self.assertEqual(api.issue(9)["state"], "open")
        sync(api, {"Sonarr": "4.0.2"})
        self.assertEqual((api.issue(9)["state"], api.issue(9)["state_reason"]), ("closed", "completed"))
        self.assertIn("Verified", api.comments[9][0])
        self.assertEqual(len(api.issues), 1)

    def test_issues_without_the_label_or_with_another_title_are_ignored(self):
        unlabelled = existing(3, "Support Sonarr 4.0.10", labels=("enhancement",))
        pull_request = dict(existing(5, "Support Sonarr 4.0.10"), pull_request={})
        api = FakeGitHub([unlabelled, existing(4, "Support Sonarr v4 please"), pull_request])
        sync(api, {"Sonarr": "4.0.2"}, failing=["Sonarr"])
        self.assertEqual(len(api.titled("Support Sonarr 4.0.2")), 1)  # neither 4.0.10 counts as already seen
        self.assertEqual([api.issue(n)["state"] for n in (3, 4, 5)], ["open", "open", "open"])


class SupersededTests(unittest.TestCase):
    def test_a_newer_version_closes_the_open_older_issue_as_superseded(self):
        api = FakeGitHub([existing(9, "Support Sonarr 4.0.2")])
        sync(api, {"Sonarr": "4.0.10"}, failing=["Sonarr"])
        [new] = api.titled("Support Sonarr 4.0.10")
        self.assertEqual((api.issue(9)["state"], api.issue(9)["state_reason"]), ("closed", "not_planned"))
        self.assertIn(f"Superseded by #{new['number']}", api.comments[9][0])
        self.assertIn("Previous version: 4.0.2", new["body"])
        self.assertEqual([i["number"] for i in api.issues if i["state"] == "open"], [new["number"]])

    def test_a_rerun_closes_an_older_issue_a_stopped_run_left_open(self):
        api = FakeGitHub([existing(9, "Support Sonarr 4.0.2"), existing(10, "Support Sonarr 4.0.10")])
        sync(api, {"Sonarr": "4.0.10"}, failing=["Sonarr"])
        self.assertEqual((api.issue(9)["state"], api.issue(9)["state_reason"]), ("closed", "not_planned"))
        self.assertIn("Superseded by #10", api.comments[9][0])
        self.assertEqual(api.issue(10)["state"], "open")
        self.assertEqual(len(api.issues), 2)

    def test_an_older_image_than_one_already_seen_is_left_alone(self):
        api = FakeGitHub([existing(9, "Support Sonarr 4.0.10")])
        self.assertEqual(sync(api, {"Sonarr": "4.0.2"}), [])
        self.assertEqual(len(api.issues), 1)
        self.assertEqual(api.issue(9)["state"], "open")

    def test_versions_compare_by_number_not_text(self):
        self.assertGreater(upstream_releases.version_key("10.10.0"), upstream_releases.version_key("10.9.12"))


class Answer(io.BytesIO):
    def __init__(self, data, link=""):
        super().__init__(json.dumps(data).encode())
        self.headers = {"Link": link}


class GitHubClientTests(unittest.TestCase):
    """The REST calls themselves, with GitHub's answers made up."""

    def setUp(self):
        self.api = upstream_releases.GitHub("owner/repo", "made-up-token", "https://api.example.invalid")
        self.requests = []

    def answering(self, *answers):
        queue = list(answers)

        def urlopen(request, timeout):
            self.requests.append(request)
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return mock.patch.object(upstream_releases.urllib.request, "urlopen", urlopen)

    def test_release_issues_follow_every_next_link(self):
        nxt = "https://api.example.invalid/repos/owner/repo/issues?labels=upstream-release&state=all&per_page=100&page=2"
        with self.answering(Answer([{"number": 1}], f'<{nxt}>; rel="next"'), Answer([{"number": 2}])):
            self.assertEqual([i["number"] for i in self.api.release_issues()], [1, 2])
        self.assertTrue(self.requests[0].full_url.endswith("issues?labels=upstream-release&state=all&per_page=100"))
        self.assertEqual(self.requests[1].full_url, nxt)

    def test_an_issue_is_opened_with_its_labels(self):
        with self.answering(Answer({"number": 12})):
            self.assertEqual(self.api.create("Support Sonarr 4.0.2", "body", upstream_releases.LABELS), 12)
        sent = json.loads(self.requests[0].data)
        self.assertEqual((self.requests[0].get_method(), sent["labels"]), ("POST", ["enhancement", "P1", "upstream-release"]))

    def test_an_error_names_the_call_and_github_message_never_the_token(self):
        body = io.BytesIO(json.dumps({"message": "Validation Failed"}).encode())
        error = urllib.error.HTTPError("https://api.example.invalid/repos/owner/repo/issues?x=1", 422, "", {}, body)
        with self.answering(error), self.assertRaises(RuntimeError) as caught:
            self.api.create("t", "b", [])
        self.assertEqual(str(caught.exception), "GitHub answered 422 for POST issues: Validation Failed")
        self.assertNotIn("made-up-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
