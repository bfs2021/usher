"""deploy/setup.sh reads its settings from usher.toml alone; site.env only has to name the repo. It checks for every tool
it needs before it changes anything, installs none of them, and lets Docker Compose wait for Usher to be healthy."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SETUP = Path(__file__).resolve().parent.parent / "deploy" / "setup.sh"
BASH = shutil.which("bash")
# The tools deploy/README.md lists under Requirements, as setup.sh looks for them
TOOLS = ("git", "curl", "flock", "logger", "systemctl", "python3", "docker")


class SetupScriptTests(unittest.TestCase):
    def setUp(self):
        self.text = SETUP.read_text(encoding="utf-8")

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_it_parses(self):
        subprocess.run(["bash", "-n", str(SETUP)], check=True)

    def test_site_env_only_needs_the_repo(self):
        loop = [line for line in self.text.splitlines() if line.startswith("for v in")]
        self.assertEqual(loop, [], "no list of required site.env variables")
        self.assertIn('[ -n "${USHER_REPO:-}" ]', self.text)
        # what it needs is checked only after reading usher.toml
        self.assertLess(self.text.index("tomlget server origin"), self.text.index('[ -n "$ORIGIN" ]'))
        self.assertLess(self.text.index("tomlget download_client user"), self.text.index('[ -n "$TR_USER" ]'))

    def test_settings_come_only_from_usher_toml(self):
        self.assertIn('tomlget server origin', self.text)
        self.assertIn('tomlget media_server user_id', self.text)
        self.assertIn('tomlget download_client user', self.text)
        self.assertIn('usher.example.toml', self.text)
        self.assertIn('usher.cli config check', self.text)
        # no fallback to the old site.env settings, and no migration from them
        for old in ("migrate", "USHER_ORIGIN", "JELLYFIN_USER", "TR_USER:-", "JF_NAME", "this release"):
            self.assertNotIn(old, self.text)

    def test_it_installs_nothing(self):
        self.assertNotIn("apt-get", self.text)

    def test_it_pulls_the_image_of_a_release_and_builds_nothing(self):
        self.assertIn("docker compose pull && docker compose up -d --wait --wait-timeout 120", self.text)
        self.assertNotIn("docker compose build", self.text)
        self.assertNotIn("--branch stable", self.text)
        self.assertIn('--branch "v$VER"', self.text)
        self.assertIn('setkey USHER_VERSION "$VER"', self.text)  # the version to run is written, once

    def test_compose_waits_for_a_healthy_usher(self):
        self.assertIn("docker compose up -d --wait --wait-timeout 120", self.text)
        self.assertNotIn("seq 1 30", self.text)
        self.assertLess(self.text.index("--wait-timeout 120"), self.text.index("curl -fsS http://127.0.0.1:8484/healthz && echo"))

    def test_reverse_proxy_is_left_to_the_operator(self):
        """The setup script no longer edits another app's files; deploy/README.md says how to point the proxy at Usher."""
        self.assertNotIn("Caddyfile", self.text)
        self.assertNotIn('HOST=${USHER_ORIGIN', self.text)


@unittest.skipUnless(BASH, "needs bash")
class ToolCheckTests(unittest.TestCase):
    """setup.sh run with a PATH that holds only fakes of the tools it needs (and id, so it runs as root)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin, self.log, self.home = self.tmp / "bin", self.tmp / "calls.log", self.tmp / "usher"
        self.bin.mkdir()
        self.fake("id", "echo 0")
        for tool in TOOLS:
            self.fake(tool, "exit 0")
        self.fake("python3", 'case "$*" in *version_info*) exit "${FAKE_PYTHON_OLD:-0}";; esac')
        self.fake("docker", 'case "$*" in "compose version --short") echo "${FAKE_COMPOSE-2.29.1}";; esac')

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f'#!{BASH}\necho "{name} $*" >> "{self.log}"\n{body}\n')
        path.chmod(0o755)

    def run_setup(self, **extra):
        env = {"PATH": str(self.bin), "USHER_HOME": str(self.home), **extra}
        return subprocess.run([BASH, str(SETUP)], env=env, capture_output=True, text=True, timeout=30)

    def assertStopsNaming(self, run, *names):
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        (line,) = [x for x in run.stdout.splitlines() if x.startswith("FAIL")]
        for name in names:
            self.assertIn(name, line)
        self.assertFalse(self.home.exists(), "created the install folder")
        checks = [c for c in self.log.read_text().splitlines() if not c.startswith("id ")]
        self.assertTrue(all(c.startswith(("python3 -c", "docker compose version")) for c in checks), checks)

    def test_with_every_tool_it_goes_on_to_read_site_env(self):
        run = self.run_setup()
        self.assertEqual(run.returncode, 1)
        self.assertIn("site.env is missing", run.stdout)
        self.assertFalse(self.home.exists())

    def test_no_ssh_tools_are_needed(self):
        """Only the tools above are on the path, and none of them is ssh, ssh-keygen or ssh-keyscan."""
        self.assertFalse({"ssh", "ssh-keygen", "ssh-keyscan"} & {p.name for p in self.bin.iterdir()})
        self.assertIn("site.env is missing", self.run_setup().stdout)

    def test_a_repo_address_that_is_not_https_is_refused(self):
        self.home.mkdir()
        (self.home / "site.env").write_text("USHER_REPO=git@github.com:example/usher.git\n")
        run = self.run_setup()
        self.assertEqual(run.returncode, 1)
        self.assertIn("must be an https:// address", run.stdout)

    def test_an_unreadable_repo_stops_setup_with_a_hint(self):
        self.home.mkdir()
        (self.home / "site.env").write_text("USHER_REPO=https://example.invalid/usher.git\n")
        self.fake("git", "exit 128")
        run = self.run_setup()
        self.assertEqual(run.returncode, 1)
        self.assertIn("can't read https://example.invalid/usher.git", run.stdout)
        self.assertNotIn("ssh", [c.split()[0] for c in self.log.read_text().splitlines()])

    def test_missing_git_is_named_and_nothing_is_created(self):
        (self.bin / "git").unlink()
        self.assertStopsNaming(self.run_setup(), "git")

    def test_every_missing_tool_is_named_in_one_line(self):
        (self.bin / "flock").unlink()
        (self.bin / "systemctl").unlink()
        self.assertStopsNaming(self.run_setup(), "flock", "systemctl")

    def test_missing_docker_is_named_once(self):
        (self.bin / "docker").unlink()
        run = self.run_setup()
        self.assertStopsNaming(run, "docker")
        self.assertNotIn("Compose", run.stdout)

    def test_flock_and_logger_for_usher_update_are_checked(self):
        (self.bin / "flock").unlink()
        (self.bin / "logger").unlink()
        self.assertStopsNaming(self.run_setup(), "flock", "logger")

    def test_a_container_that_does_not_become_healthy_stops_setup_with_a_hint(self):
        text = SETUP.read_text()
        self.assertIn("isn't healthy. Look at: sudo docker logs --tail 50 usher", text)

    def test_old_python_is_named(self):
        self.assertStopsNaming(self.run_setup(FAKE_PYTHON_OLD="1"), "python3 3.11 or later")

    def test_old_or_missing_compose_is_named(self):
        for version in ("2.17.0", "v2.20.1", "2.9.0", "2.20", ""):
            with self.subTest(version=version):
                self.assertStopsNaming(self.run_setup(FAKE_COMPOSE=version), "Docker Compose 2.20.2 or later")

    def test_newer_compose_versions_pass(self):
        for version in ("2.20.2", "v2.29.1-desktop.1", "2.100.0", "5.6.1"):
            with self.subTest(version=version):
                self.assertIn("site.env is missing", self.run_setup(FAKE_COMPOSE=version).stdout)


@unittest.skipUnless(BASH, "needs bash")
class SetkeyTests(unittest.TestCase):
    """The secrets section, run with a PATH that holds only fakes of the tools it needs: setkey keeps every line of
    .env but the one it writes, and an .env it cannot read is never replaced by one holding only the new key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin, self.home = self.tmp / "bin", self.tmp / "usher"
        self.bin.mkdir()
        self.home.mkdir()
        self.fake("id", "echo 0")
        for tool in ("curl", "flock", "logger", "systemctl"):
            self.fake(tool, "exit 0")
        self.fake("git", 'case "$*" in *"ls-remote --tags"*) printf "0123456789abcdef\\trefs/tags/v1.0.0\\n";; esac')
        self.fake("docker", 'case "$*" in "compose version --short") echo "2.29.1";; esac')
        # the real interpreter, for the version check and tomlget
        self.fake("python3", f'exec "{Path(sys.executable).as_posix()}" "$@"')
        # the run stops here: step 4 would write /etc/default/usher, which the tests must not touch
        self.fake("install", "exit 1")
        (self.home / "site.env").write_text("USHER_REPO=https://example.invalid/usher.git\n")
        (self.home / "config").mkdir()
        (self.home / "config" / "usher.toml").write_text(
            '[server]\norigin = "https://usher.example.invalid"\n\n[media_server]\nuser_id = "viewer1"\n\n[download_client]\nuser = "downloader1"\n'
        )
        self.env_file = self.home / ".env"

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/bash\n{body}\n")
        path.chmod(0o755)

    def bash_path(self, path):
        """A full path as bash sees it: /c/... on Windows (USHER_HOME and the answers must start with a slash)."""
        path = Path(path)
        if path.drive:
            return f"/{path.drive.rstrip(':').lower()}{path.as_posix()[len(path.drive):]}"
        return path.as_posix()

    def run_setup(self, *answers):
        # the answers go in as bytes, so a prompt sees one \n-ended line on every platform
        env = dict(os.environ, USHER_HOME=self.bash_path(self.home))
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        run = subprocess.run(
            [BASH, str(SETUP)], env=env, input="".join(f"{a}\n" for a in answers).encode(), capture_output=True, timeout=60
        )
        run.stdout = run.stdout.decode(errors="replace")
        run.stderr = run.stderr.decode(errors="replace")
        return run

    def test_an_env_that_cannot_be_read_is_never_replaced_by_one_holding_only_the_new_key(self):
        """grep exits 1 when no line is left, which is fine, and 2 when it can't read the file, which must stop."""
        self.env_file.write_text("TR_PASS=old-made-up\nJELLYFIN_API_KEY=old-made-up\n")
        self.fake("grep", "exit 2")
        run = self.run_setup("made-up-new-pass")
        self.assertEqual(run.returncode, 1)
        self.assertIn("can't read", run.stdout)
        self.assertEqual(self.env_file.read_text(), "TR_PASS=old-made-up\nJELLYFIN_API_KEY=old-made-up\n")

    def test_a_key_is_set_in_an_empty_env_with_every_answer_kept(self):
        sonarr, radarr, prowlarr = (self.home / f"{name}.xml" for name in ("sonarr", "radarr", "prowlarr"))
        for path in (sonarr, radarr, prowlarr):
            path.write_text("<ApiKey>made-up</ApiKey>\n")
        self.fake("curl", "printf '{\"Id\":\"viewer1\"}'")  # what Jellyfin answers about the viewing account
        run = self.run_setup(
            "made-up-tr-pass",
            "made-up-jellyfin-key",
            "",  # TMDb token, skipped
            self.bash_path(sonarr),
            self.bash_path(radarr),
            self.bash_path(prowlarr),
            "/made-up/recycle",
        )
        self.assertEqual(run.returncode, 1)  # stopped at the faked install, after every key was set
        self.assertEqual(
            self.env_file.read_text(),
            "TR_PASS=made-up-tr-pass\n"
            "JELLYFIN_API_KEY=made-up-jellyfin-key\n"
            f"USHER_SONARR_XML={self.bash_path(sonarr)}\n"
            f"USHER_RADARR_XML={self.bash_path(radarr)}\n"
            f"USHER_PROWLARR_XML={self.bash_path(prowlarr)}\n"
            "USHER_RECYCLE_BIN=/made-up/recycle\n",
        )
