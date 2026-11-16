"""Status answers "is everything working?": the summary card, Health, Storage. Settings is a
pushed screen with its own groups in a fixed order, like iOS Settings: account first, Sign out last."""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
STATUS = (WEB / "status.js").read_text(encoding="utf-8")
SETTINGS = (WEB / "settings.js").read_text(encoding="utf-8")
ADVANCED = (WEB / "advanced.js").read_text(encoding="utf-8")


def method(source, name):
    """The text of one class method, from its signature to the closing brace at the same indent."""
    start = re.search(rf"(?m)^  (?:async )?{re.escape(name)}\(.*\) \{{$", source)
    if not start:
        raise AssertionError(f"no method {name}()")
    end = source.index("\n  }\n", start.end())
    return source[start.start() : end]


def in_order(test, body, names, what):
    found = [body.index(name) for name in names]
    test.assertEqual(found, sorted(found), f"{what} are out of order")


class StatusOrder(unittest.TestCase):
    def test_summary_health_then_storage(self):
        body = method(STATUS, "status")
        in_order(self, body, ["summaryCard(", "healthSection(", "storageSection("], "Status groups")

    def test_status_no_longer_holds_the_settings_groups(self):
        for name in ("settingsGroups", "deviceSection", "accountSection", "aboutSection", "advancedSection", "notifySection"):
            self.assertNotIn(name, STATUS)

    def test_status_has_no_settings_row_since_the_account_button_is_the_one_way_in(self):
        self.assertNotIn("settingsRow", STATUS)
        self.assertNotIn("openSettings", STATUS)

    def test_group_headings(self):
        self.assertIn('h("h2", {}, "Health")', STATUS)
        for heading in ("Account", "Device", "Devices", "About"):
            self.assertIn(f'h("h2", {{}}, "{heading}")', SETTINGS)

    def test_summary_names_how_many_things_need_attention(self):
        summary = method(STATUS, "summaryCard")
        self.assertIn("Everything's working.", summary)
        self.assertIn("need", summary)
        self.assertIn("Checking…", summary)

    def test_attention_rows_are_only_parts_that_are_not_ok_and_are_in_the_card(self):
        summary = method(STATUS, "summaryCard")
        self.assertIn("!p.ok", summary)
        self.assertNotIn("attentionList", STATUS)
        self.assertNotIn("style:", summary)  # looks come from app.css

    def test_health_rows_keep_their_state_on_the_right_in_one_line(self):
        health = method(STATUS, "healthSection")
        self.assertIn('class: "meta"', health)
        self.assertIn('"Logs"', health)
        self.assertNotIn("white-space", health)


class SettingsOrder(unittest.TestCase):
    def test_account_device_devices_about_advanced_then_sign_out(self):
        groups = method(SETTINGS, "settingsGroups")
        order = ["accountSection(", "deviceSection(", "devicesSection(", "aboutSection(", "advancedSection(", "signOutSection("]
        in_order(self, groups, order, "the Settings groups")

    def test_settings_is_a_pushed_screen_with_a_back_button(self):
        self.assertIn('this.pushScreen("settings")', method(SETTINGS, "openSettings"))
        view = method(SETTINGS, "settingsView")
        self.assertIn("nav-back", view)
        self.assertIn('title: "Settings"', view)
        self.assertIn("this.settingsGroups()", view)


class EverythingStillReachable(unittest.TestCase):
    def test_device_settings_are_in_this_device(self):
        device = method(SETTINGS, "deviceSection")
        self.assertIn("this.openInRow()", device)
        self.assertIn("this.notifySection()", device)
        self.assertNotIn("updateNoticesRow", device)

    def test_account_names_who_is_signed_in(self):
        account = method(SETTINGS, "accountSection")
        self.assertIn("me.name", account)
        self.assertIn("Signed in as", account)
        for elsewhere in ("me.devices", "Sign out", "openInRow"):
            self.assertNotIn(elsewhere, account)

    def test_devices_lists_each_device_and_signs_out_the_others(self):
        devices = method(SETTINGS, "devicesSection")
        for text in ("me.devices", "Sign out other devices", "this.signOutOthers()"):
            self.assertIn(text, devices)
        self.assertNotIn('"Sign out"', devices)

    def test_sign_out_is_its_own_group_in_red(self):
        sign_out = method(SETTINGS, "signOutSection")
        self.assertIn('"Sign out"', sign_out)
        self.assertIn("this.signOut()", sign_out)
        self.assertIn("danger", sign_out)
        self.assertNotIn("section-head", sign_out)

    def test_about_keeps_version_changelog_reload_and_credits(self):
        about = method(SETTINGS, "aboutSection")
        for text in ("BUILD", "Changelog", "Reload the app", "updater.apply", "TMDB", "JustWatch"):
            self.assertIn(text, about)

    def test_update_notices_are_gone_but_last_update_stays(self):
        self.assertNotIn("Update notices", ADVANCED)
        self.assertNotIn("Update notices", STATUS + SETTINGS)
        self.assertIn('"Last update"', method(ADVANCED, "fillAdvanced"))
        self.assertIn("this.advancedSection()", method(SETTINGS, "settingsGroups"))

    def test_advanced_still_starts_collapsed(self):
        self.assertIn("open: !!this.advancedOpen", ADVANCED)


if __name__ == "__main__":
    unittest.main()
